"""Core route parser: tree-sitter AST walker for Rails routes.rb files."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional, List, Dict

from tree_sitter import Node

from .models import Endpoint, RouteContext
from .ruby_helpers import (
    parse_ruby,
    node_text,
    extract_call_info,
    extract_symbol_name,
    extract_string_value,
    extract_array_elements,
    extract_hash_from_args,
    find_block_body,
    singularize,
)

logger = logging.getLogger(__name__)

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

RESOURCE_ACTIONS = ["index", "new", "create", "show", "edit", "update", "destroy"]
SINGULAR_RESOURCE_ACTIONS = ["new", "create", "show", "edit", "update", "destroy"]

# Actions that require :id in the path
MEMBER_ACTIONS = {"show", "edit", "update", "destroy"}
# update uses both PUT and PATCH
UPDATE_METHODS = {"PATCH": "update", "PUT": "update"}


class RouteParser:
    """Parse Rails route files and extract Endpoint objects."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self.endpoints: List[Endpoint] = []
        self.concerns: Dict[str, Node] = {}  # name -> block AST node
        self._current_file = ""
        self._condition_stack: List[Optional[str]] = []

    def parse(self) -> List[Endpoint]:
        """Parse all route files and return discovered endpoints."""
        routes_file = os.path.join(self.repo_root, "config", "routes.rb")
        if not os.path.isfile(routes_file):
            logger.warning("No config/routes.rb found in %s", self.repo_root)
            return []

        self._parse_file(routes_file, RouteContext())
        return self.endpoints

    def _parse_file(self, filepath: str, context: RouteContext) -> None:
        """Parse a single route file."""
        try:
            with open(filepath, "rb") as f:
                source = f.read()
        except OSError as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return

        self._current_file = os.path.relpath(filepath, self.repo_root)
        root = parse_ruby(source)
        self._walk_node(root, context)

    def _walk_node(self, node: Node, context: RouteContext) -> None:
        """Walk an AST node and process route declarations."""
        for child in node.children:
            self._process_node(child, context)

    def _get_handler(self, method_name: str):
        """Get the handler function for a route DSL method."""
        handlers = {
            "resources": self._handle_resources,
            "resource": self._handle_resource,
            "namespace": self._handle_namespace,
            "scope": self._handle_scope,
            "member": self._handle_member,
            "collection": self._handle_collection,
            "concern": self._handle_concern_def,
            "concerns": self._handle_concerns_use,
            "mount": self._handle_mount,
            "root": self._handle_root,
            "match": self._handle_match,
            "get": self._handle_http_verb,
            "post": self._handle_http_verb,
            "put": self._handle_http_verb,
            "patch": self._handle_http_verb,
            "delete": self._handle_http_verb,
            "with_options": self._handle_with_options,
            "constraints": self._handle_constraints,
            "defaults": self._handle_defaults,
            "direct": self._handle_noop,
            "resolve": self._handle_noop,
        }
        return handlers.get(method_name)

    # ---- DSL Handlers ----

    def _handle_resources(self, args: List[Node], block_node: Optional[Node],
                          context: RouteContext) -> None:
        """Handle `resources :name, options do...end`."""
        name, opts = self._extract_name_and_opts(args)
        if not name:
            return

        path_name = opts.get("path", name)
        controller = opts.get("controller", name)
        param = opts.get("param", "id")
        actions = self._filter_actions(RESOURCE_ACTIONS, opts)

        new_ctx = context.copy()
        # If nested inside a parent resource, insert /:parent_id before this resource
        if context.resource_name:
            parent_singular = singularize(context.resource_name)
            parent_param = context.resource_param.lstrip(":")
            parent_id = f"{parent_singular}_{parent_param}"
            parent_base = f"{context.path_prefix}/:{parent_id}"
            new_ctx.path_prefix = self._join_path(parent_base, path_name)
        else:
            new_ctx.path_prefix = self._join_path(context.path_prefix, path_name)
        new_ctx.module_prefix = context.module_prefix
        new_ctx.controller = self._resolve_controller(context, controller)
        new_ctx.resource_name = name
        new_ctx.resource_param = f":{param}"

        # Emit resource endpoints
        for action in actions:
            self._emit_resource_action(action, new_ctx, is_singular=False)

        # Handle concerns
        if "concerns" in opts:
            self._replay_concerns(opts["concerns"], new_ctx)

        # Walk block for nested routes
        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_resource(self, args: List[Node], block_node: Optional[Node],
                         context: RouteContext) -> None:
        """Handle `resource :name, options do...end` (singular resource)."""
        name, opts = self._extract_name_and_opts(args)
        if not name:
            return

        path_name = opts.get("path", name)
        controller = opts.get("controller", name if name.endswith("s") else name + "s")
        param = opts.get("param", "id")
        actions = self._filter_actions(SINGULAR_RESOURCE_ACTIONS, opts)

        new_ctx = context.copy()
        new_ctx.path_prefix = self._join_path(context.path_prefix, path_name)
        new_ctx.module_prefix = context.module_prefix
        new_ctx.controller = self._resolve_controller(context, controller)
        new_ctx.resource_name = name
        new_ctx.resource_param = f":{param}"

        for action in actions:
            self._emit_resource_action(action, new_ctx, is_singular=True)

        if "concerns" in opts:
            self._replay_concerns(opts["concerns"], new_ctx)

        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_namespace(self, args: List[Node], block_node: Optional[Node],
                          context: RouteContext) -> None:
        """Handle `namespace :name do...end`."""
        name, opts = self._extract_name_and_opts(args)
        if not name:
            return

        path_part = opts.get("path", name)
        module_part = opts.get("module", name)

        new_ctx = context.copy()
        if path_part:
            new_ctx.path_prefix = self._join_path(context.path_prefix, path_part)
        new_ctx.module_prefix = self._join_module(context.module_prefix, module_part)

        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_scope(self, args: List[Node], block_node: Optional[Node],
                      context: RouteContext) -> None:
        """Handle `scope '/path', module: :mod do...end`."""
        name, opts = self._extract_name_and_opts(args)

        new_ctx = context.copy()
        # scope with a path argument
        if name and not name.startswith(":"):
            new_ctx.path_prefix = self._join_path(context.path_prefix, name)
        # scope with path: option
        if "path" in opts:
            new_ctx.path_prefix = self._join_path(context.path_prefix, opts["path"])
        # scope with module: option
        if "module" in opts:
            new_ctx.module_prefix = self._join_module(context.module_prefix, opts["module"])
        # scope with controller: option
        if "controller" in opts:
            new_ctx.controller = self._resolve_controller(new_ctx, opts["controller"])

        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_member(self, args: List[Node], block_node: Optional[Node],
                       context: RouteContext) -> None:
        """Handle `member do...end` block."""
        new_ctx = context.copy()
        new_ctx.scope_type = "member"
        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_collection(self, args: List[Node], block_node: Optional[Node],
                           context: RouteContext) -> None:
        """Handle `collection do...end` block."""
        new_ctx = context.copy()
        new_ctx.scope_type = "collection"
        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_concern_def(self, args: List[Node], block_node: Optional[Node],
                            context: RouteContext) -> None:
        """Handle `concern :name do...end` definition."""
        if not args:
            return
        name = self._extract_value(args[0])
        if name and block_node:
            self.concerns[name] = block_node

    def _handle_concerns_use(self, args: List[Node], block_node: Optional[Node],
                             context: RouteContext) -> None:
        """Handle `concerns [:name1, :name2]` usage on a resource."""
        for arg in args:
            if arg.type == "array":
                names = extract_array_elements(arg)
                for n in names:
                    self._replay_concern(n, context)
            else:
                name = self._extract_value(arg)
                if name:
                    self._replay_concern(name, context)

    def _handle_mount(self, args: List[Node], block_node: Optional[Node],
                      context: RouteContext) -> None:
        """Handle `mount Engine => '/path'`."""
        # mount SomeEngine => '/path' or mount SomeEngine, at: '/path'
        engine_name = None
        mount_path = None

        for arg in args:
            text = node_text(arg).strip()
            if arg.type == "pair":
                # Engine => '/path'
                children = list(arg.children)
                if len(children) >= 2:
                    engine_name = node_text(children[0]).strip()
                    mount_path = self._extract_value(children[-1])
            elif arg.type in ("scope_resolution", "constant"):
                engine_name = text
            elif arg.type in ("simple_symbol", "string"):
                if mount_path is None:
                    mount_path = self._extract_value(arg)

        opts = extract_hash_from_args(args)
        if "at" in opts:
            mount_path = self._extract_value(opts["at"])

        if engine_name and mount_path:
            full_path = self._join_path(context.path_prefix, mount_path)
            self.endpoints.append(Endpoint(
                method="*",
                path=full_path,
                controller="",
                action="",
                source_file=self._current_file,
                source_line=0,
                is_mounted_engine=True,
                engine_name=engine_name,
                condition=self._current_condition(),
            ))

    def _handle_root(self, args: List[Node], block_node: Optional[Node],
                     context: RouteContext) -> None:
        """Handle `root 'controller#action'` or `root to: 'controller#action'`."""
        controller = None
        action = None

        opts = extract_hash_from_args(args)
        if "to" in opts:
            target = self._extract_value(opts["to"])
            if target and "#" in target:
                controller, action = target.split("#", 1)
        else:
            for arg in args:
                val = self._extract_value(arg)
                if val and "#" in val:
                    controller, action = val.split("#", 1)
                    break

        if controller is None:
            controller = ""
        if action is None:
            action = "root"

        full_ctrl = self._resolve_controller(context, controller) if controller else ""
        self.endpoints.append(Endpoint(
            method="GET",
            path=self._join_path(context.path_prefix, "/") if context.path_prefix else "/",
            controller=full_ctrl,
            action=action,
            source_file=self._current_file,
            source_line=0,
            condition=self._current_condition(),
        ))

    def _handle_match(self, args: List[Node], block_node: Optional[Node],
                      context: RouteContext) -> None:
        """Handle `match '/path', via: [:get, :post]`."""
        name, opts = self._extract_name_and_opts(args)
        if not name:
            return

        via = opts.get("via", "all")
        if via == "all":
            methods = list(HTTP_METHODS)
        elif isinstance(via, list):
            methods = via
        else:
            methods = [via]

        controller, action = self._resolve_target(name, opts, context)
        path = self._join_path(context.path_prefix, name)
        path_params = re.findall(r":(\w+)", path)

        for method in methods:
            self.endpoints.append(Endpoint(
                method=method.upper(),
                path=path,
                controller=controller,
                action=action,
                path_params=path_params,
                source_file=self._current_file,
                source_line=0,
                condition=self._current_condition(),
            ))

    def _handle_http_verb(self, args: List[Node], block_node: Optional[Node],
                          context: RouteContext) -> None:
        """Handle `get '/path'`, `post '/path'`, etc."""
        # The method name comes from the call context; we need to figure it out.
        # Since we dispatched based on method_name, we need the name back.
        # We'll use a trick: check what handler was dispatched.
        # Actually, we pass through _process_node which already has the method_name.
        # We need to thread it. Let's use a workaround: store it on self temporarily.
        method = getattr(self, "_current_verb", "GET")
        name, opts = self._extract_name_and_opts(args)
        if not name:
            return

        controller, action = self._resolve_target(name, opts, context)
        path = self._build_verb_path(name, context)
        path_params = re.findall(r":(\w+)", path)

        is_redirect = "redirect" in node_text(args[0] if args else Node) if args else False
        # Check for redirect in opts
        if "to" in opts:
            to_text = node_text(opts["to"]) if hasattr(opts.get("to"), "text") else ""
            if "redirect" in to_text.lower():
                is_redirect = True

        self.endpoints.append(Endpoint(
            method=method.upper(),
            path=path,
            controller=controller,
            action=action,
            path_params=path_params,
            source_file=self._current_file,
            source_line=0,
            condition=self._current_condition(),
            is_redirect=is_redirect if isinstance(is_redirect, bool) else False,
        ))

    def _handle_with_options(self, args: List[Node], block_node: Optional[Node],
                             context: RouteContext) -> None:
        """Handle `with_options opts do...end`."""
        opts = extract_hash_from_args(args)
        new_ctx = context.copy()

        if "controller" in opts:
            new_ctx.controller = self._resolve_controller(new_ctx, self._extract_value(opts["controller"]))
        if "path" in opts:
            new_ctx.path_prefix = self._join_path(context.path_prefix, self._extract_value(opts["path"]))
        if "module" in opts:
            new_ctx.module_prefix = self._join_module(context.module_prefix, self._extract_value(opts["module"]))

        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, new_ctx)

    def _handle_constraints(self, args: List[Node], block_node: Optional[Node],
                            context: RouteContext) -> None:
        """Handle `constraints(...) do...end`."""
        # Constraints don't affect path/controller, just record and walk block
        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, context)

    def _handle_defaults(self, args: List[Node], block_node: Optional[Node],
                         context: RouteContext) -> None:
        """Handle `defaults format: :json do...end`."""
        if block_node:
            body = find_block_body(block_node)
            for stmt in body:
                self._process_node(stmt, context)

    def _handle_noop(self, args: List[Node], block_node: Optional[Node],
                     context: RouteContext) -> None:
        """Handle DSL methods we intentionally skip (direct, resolve)."""
        pass

    def _handle_draw(self, args: List[Node], context: RouteContext) -> None:
        """Handle `draw(:name)` — load config/routes/{name}.rb."""
        if not args:
            return
        # Filter out punctuation tokens like ( and )
        filtered = [a for a in args if a.type not in ("(", ")", ",")]
        if not filtered:
            return
        name = self._extract_value(filtered[0])
        if not name:
            return
        draw_file = os.path.join(self.repo_root, "config", "routes", f"{name}.rb")
        if os.path.isfile(draw_file):
            self._parse_file(draw_file, context)
        else:
            logger.warning("draw(%s) referenced but file not found: %s", name, draw_file)

    def _handle_conditional(self, node: Node, context: RouteContext) -> None:
        """Handle conditional routes (if/unless blocks)."""
        condition_text = None
        for child in node.children:
            if child.type not in ("if", "unless", "then", "end",
                                  "body_statement", "block_body"):
                condition_text = node_text(child).strip()
                break

        self._condition_stack.append(condition_text)
        # Walk the body
        for child in node.children:
            if child.type in ("then", "body_statement", "block_body"):
                self._walk_node(child, context)
            elif child.type == "else":
                self._walk_node(child, context)
            elif child.type == "elsif":
                self._process_node(child, context)
        self._condition_stack.pop()

    def _handle_dynamic_block(self, node: Node, context: RouteContext) -> None:
        """Handle .each loops — flag endpoints as dynamic."""
        old = getattr(self, "_force_dynamic", False)
        self._force_dynamic = True
        self._walk_node(node, context)
        self._force_dynamic = old

    # ---- Endpoint emission ----

    def _emit_resource_action(self, action: str, ctx: RouteContext,
                              is_singular: bool) -> None:
        """Emit an endpoint for a standard resource action."""
        method_map = {
            "index": "GET",
            "new": "GET",
            "create": "POST",
            "show": "GET",
            "edit": "GET",
            "update": None,  # both PUT and PATCH
            "destroy": "DELETE",
        }

        if action == "update":
            for http_method in ("PATCH", "PUT"):
                path = self._resource_action_path(action, ctx, is_singular)
                self._emit_endpoint(http_method, path, ctx, action)
        else:
            http_method = method_map.get(action, "GET")
            path = self._resource_action_path(action, ctx, is_singular)
            self._emit_endpoint(http_method, path, ctx, action)

    def _resource_action_path(self, action: str, ctx: RouteContext,
                              is_singular: bool) -> str:
        """Build the path for a standard resource action."""
        base = ctx.path_prefix
        if not is_singular and action in MEMBER_ACTIONS:
            base = f"{base}/{ctx.resource_param}"
        if action in ("new",):
            return f"{base}/new"
        if action in ("edit",):
            return f"{base}/edit" if is_singular else f"{base}/edit"
        return base

    def _emit_endpoint(self, method: str, path: str, ctx: RouteContext,
                       action: str) -> None:
        """Create and store an Endpoint."""
        path_params = re.findall(r":(\w+)", path)
        ep = Endpoint(
            method=method,
            path=path,
            controller=ctx.controller or "",
            action=action,
            path_params=path_params,
            source_file=self._current_file,
            source_line=0,
            condition=self._current_condition(),
            is_dynamic=getattr(self, "_force_dynamic", False),
        )
        self.endpoints.append(ep)

    # ---- Helper methods ----

    def _extract_name_and_opts(self, args: List[Node]) -> tuple:
        """Extract the first positional arg (name) and keyword options from args."""
        name = None
        opts_dict = {}

        # Filter out commas and other punctuation
        filtered = [a for a in args if a.type not in (",", ")", "(")]

        for arg in filtered:
            if arg.type in ("simple_symbol", "string", "string_content"):
                if name is None:
                    name = self._extract_value(arg)
            elif arg.type == "pair":
                key_node = arg.child_by_field_name("key")
                value_node = arg.child_by_field_name("value")
                if key_node is None or value_node is None:
                    children = [c for c in arg.children if c.type not in (":", "=>", ",")]
                    if len(children) >= 2:
                        key_node, value_node = children[0], children[1]
                    else:
                        continue
                key = self._extract_value(key_node)
                if key == "only" or key == "except":
                    opts_dict[key] = self._extract_action_list(value_node)
                elif key == "via":
                    opts_dict[key] = self._extract_action_list(value_node)
                elif key == "concerns":
                    opts_dict[key] = self._extract_action_list(value_node)
                else:
                    opts_dict[key] = self._extract_value(value_node)
            elif arg.type == "hash":
                sub_opts = extract_hash_from_args(list(arg.children))
                for k, v_node in sub_opts.items():
                    if k in ("only", "except", "via", "concerns"):
                        opts_dict[k] = self._extract_action_list(v_node)
                    else:
                        opts_dict[k] = self._extract_value(v_node)
            elif arg.type == "argument_list":
                # Recurse into argument_list
                inner_name, inner_opts = self._extract_name_and_opts(list(arg.children))
                if inner_name and name is None:
                    name = inner_name
                opts_dict.update(inner_opts)

        return name, opts_dict

    def _extract_value(self, node: Node) -> Optional[str]:
        """Extract a string value from various node types."""
        if node is None:
            return None
        text = node_text(node).strip()

        if node.type == "simple_symbol":
            return text[1:] if text.startswith(":") else text
        if node.type == "string":
            return extract_string_value(node)
        if node.type == "string_content":
            return text
        if text.startswith('"') or text.startswith("'"):
            return text[1:-1] if len(text) >= 2 else ""
        if text.startswith(":"):
            return text[1:]
        return text

    def _extract_action_list(self, node: Node) -> list:
        """Extract a list of action names from a node (array or single symbol)."""
        if node.type in ("array", "symbol_array"):
            return extract_array_elements(node)
        val = self._extract_value(node)
        if val:
            return [val]
        return []

    def _filter_actions(self, all_actions: List[str], opts: dict) -> List[str]:
        """Filter resource actions by only:/except: options."""
        if "only" in opts:
            return [a for a in all_actions if a in opts["only"]]
        if "except" in opts:
            return [a for a in all_actions if a not in opts["except"]]
        return list(all_actions)

    def _resolve_controller(self, context: RouteContext, name: str) -> str:
        """Resolve full controller path including module prefix."""
        if not name:
            return context.module_prefix.rstrip("/")
        if "/" in name:
            return name
        prefix = context.module_prefix
        return f"{prefix}{name}" if prefix else name

    def _resolve_target(self, path: str, opts: dict, context: RouteContext) -> tuple:
        """Resolve controller#action target from route options."""
        controller = context.controller or ""
        action = ""

        # Check `to:` option
        if "to" in opts:
            target = opts["to"]
            if target and "#" in target:
                parts = target.split("#", 1)
                controller = self._resolve_controller(context, parts[0])
                action = parts[1]
                return controller, action
            elif target:
                action = target

        # Check for 'controller#action' as the path itself
        if "#" in path:
            parts = path.split("#", 1)
            controller = self._resolve_controller(context, parts[0])
            action = parts[1]
            return controller, action

        # Check controller: and action: options
        if "controller" in opts:
            controller = self._resolve_controller(context, opts["controller"])
        if "action" in opts:
            action = opts["action"]

        # Infer action from path
        if not action:
            action = path.strip("/").split("/")[-1] if path else ""
            # Remove parameter segments
            if action.startswith(":"):
                action = ""

        return controller, action

    def _build_verb_path(self, name: str, context: RouteContext) -> str:
        """Build path for HTTP verb routes, respecting member/collection scope."""
        if context.scope_type == "member":
            # member routes get /:id prefix from the parent resource
            base = f"{context.path_prefix}/{context.resource_param}"
            return self._join_path(base, name)
        elif context.scope_type == "collection":
            return self._join_path(context.path_prefix, name)
        else:
            return self._join_path(context.path_prefix, name)

    def _replay_concerns(self, concern_names, context: RouteContext) -> None:
        """Replay stored concerns in the current context."""
        if isinstance(concern_names, str):
            concern_names = [concern_names]
        for name in concern_names:
            self._replay_concern(name, context)

    def _replay_concern(self, name: str, context: RouteContext) -> None:
        """Replay a single stored concern block in the current context."""
        block_node = self.concerns.get(name)
        if block_node is None:
            logger.warning("Concern '%s' referenced but not defined", name)
            return
        body = find_block_body(block_node)
        for stmt in body:
            self._process_node(stmt, context)

    def _current_condition(self) -> Optional[str]:
        """Return the current condition string if inside a conditional block."""
        for cond in reversed(self._condition_stack):
            if cond:
                return cond
        return None

    @staticmethod
    def _join_path(prefix: str, suffix: str) -> str:
        """Join path segments, normalizing slashes."""
        if not suffix or suffix == "/":
            return prefix or "/"
        suffix = suffix.lstrip("/")
        if prefix:
            return f"{prefix.rstrip('/')}/{suffix}"
        return f"/{suffix}"

    @staticmethod
    def _join_module(prefix: str, module: str) -> str:
        """Join module prefixes."""
        if not module:
            return prefix
        module = module.strip("/")
        if prefix:
            return f"{prefix.rstrip('/')}/{module}/"
        return f"{module}/"

    def _process_node(self, node: Node, context: RouteContext) -> None:
        """Process a single AST node — overridden to thread HTTP verb."""
        # Check for conditional
        if node.type in ("if", "unless", "if_modifier", "unless_modifier"):
            self._handle_conditional(node, context)
            return

        # Check for .each
        if node.type in ("call", "command_call"):
            text_head = node_text(node)[:80]
            if ".each" in text_head:
                self._handle_dynamic_block(node, context)
                return

        call_info = extract_call_info(node)

        # Try to find call + block wrapper
        if call_info is None:
            for child in node.children:
                call_info_inner = extract_call_info(child)
                if call_info_inner is not None:
                    method_name, args, _ = call_info_inner
                    block = None
                    for sibling in node.children:
                        if sibling.type in ("do_block", "block"):
                            block = sibling
                            break
                    call_info = (method_name, args, block)
                    break

        if call_info is None:
            self._walk_node(node, context)
            return

        method_name, args, block_node = call_info

        if block_node is None:
            for child in node.children:
                if child.type in ("do_block", "block"):
                    block_node = child
                    break

        # Thread HTTP verb for verb handlers
        if method_name in HTTP_METHODS:
            self._current_verb = method_name

        handler = self._get_handler(method_name)
        if handler:
            handler(args, block_node, context)
        elif method_name == "draw":
            if args:
                self._handle_draw(args, context)
            elif block_node:
                # Rails.application.routes.draw do...end — walk block body
                body = find_block_body(block_node)
                for stmt in body:
                    self._process_node(stmt, context)
        else:
            if block_node:
                body = find_block_body(block_node)
                for stmt in body:
                    self._process_node(stmt, context)
