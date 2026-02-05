"""Parse Rails controllers for auth filters and strong params."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional, List, Dict, Set, Tuple

from tree_sitter import Node

from .models import Endpoint, Parameter
from .ruby_helpers import (
    parse_ruby,
    node_text,
    extract_call_info,
    extract_string_value,
    extract_array_elements,
    extract_hash_from_args,
    find_block_body,
    camelize,
)

logger = logging.getLogger(__name__)

# Auth filter name patterns
AUTH_PATTERNS = re.compile(
    r"auth|login|session|token|verify|signed_in|ensure_logged_in|"
    r"require_login|require_admin|check_auth|validate_token|"
    r"doorkeeper_authorize",
    re.IGNORECASE,
)

AUTH_EXACT = {
    "authenticate_user!", "authenticate!", "require_login",
    "ensure_logged_in", "authorize", "authorize!",
    "doorkeeper_authorize!", "verify_authenticity_token",
    "require_admin", "check_auth", "validate_token",
    "authenticate_api_user!", "require_authentication",
}

MAX_INHERITANCE_DEPTH = 3


class ControllerScanner:
    """Scan Rails controllers for auth filters and strong params."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root
        self._controller_cache: Dict[str, ControllerInfo] = {}

    def scan(self, endpoints: List[Endpoint]) -> None:
        """Scan controllers and update endpoints with auth/params info."""
        # Group endpoints by controller
        by_controller: Dict[str, List[Endpoint]] = {}
        for ep in endpoints:
            if ep.controller and not ep.is_mounted_engine:
                by_controller.setdefault(ep.controller, []).append(ep)

        for controller_name, eps in by_controller.items():
            info = self._get_controller_info(controller_name)
            if info is None:
                continue

            for ep in eps:
                self._apply_filters(ep, info)
                self._apply_params(ep, info)

    def _get_controller_info(self, controller_name: str) -> Optional[ControllerInfo]:
        """Get or parse controller info, walking inheritance chain."""
        if controller_name in self._controller_cache:
            return self._controller_cache[controller_name]

        filepath = self._resolve_controller_path(controller_name)
        if filepath is None:
            self._controller_cache[controller_name] = None
            return None

        info = self._parse_controller(filepath, controller_name)
        self._controller_cache[controller_name] = info

        # Walk inheritance chain (up to MAX_INHERITANCE_DEPTH)
        if info and info.parent_class:
            parent_name = self._resolve_parent_controller(info.parent_class, controller_name)
            if parent_name:
                self._walk_inheritance(info, parent_name, depth=1)

        return info

    def _walk_inheritance(self, info: ControllerInfo, parent_name: str,
                          depth: int) -> None:
        """Walk inheritance chain to collect inherited filters."""
        if depth >= MAX_INHERITANCE_DEPTH:
            return

        parent_info = self._get_controller_info(parent_name)
        if parent_info is None:
            return

        # Prepend parent's before_actions (they run first)
        info.before_actions = parent_info.before_actions + info.before_actions
        # Inherit skip_before_actions only if not already overridden
        existing_names = {s.filter_name for s in info.skip_before_actions}
        for skip in parent_info.skip_before_actions:
            if skip.filter_name not in existing_names:
                info.skip_before_actions.append(skip)

    def _resolve_controller_path(self, controller_name: str) -> Optional[str]:
        """Resolve controller name to file path."""
        # controller_name like "api/v1/users" → app/controllers/api/v1/users_controller.rb
        rel_path = os.path.join("app", "controllers",
                                f"{controller_name}_controller.rb")
        full_path = os.path.join(self.repo_root, rel_path)
        if os.path.isfile(full_path):
            return full_path

        # Try without module prefix
        parts = controller_name.split("/")
        if len(parts) > 1:
            simple = parts[-1]
            simple_path = os.path.join(self.repo_root, "app", "controllers",
                                       f"{simple}_controller.rb")
            if os.path.isfile(simple_path):
                return simple_path

        return None

    def _resolve_parent_controller(self, parent_class: str,
                                   child_name: str) -> Optional[str]:
        """Resolve parent class name to controller name."""
        if parent_class == "ApplicationController":
            return "application"
        if parent_class == "ActionController::Base":
            return None
        if "::" in parent_class:
            # Convert Api::V1::BaseController → api/v1/base
            from .ruby_helpers import underscore
            name = underscore(parent_class)
            # Remove _controller suffix
            if name.endswith("_controller"):
                name = name[:-11]
            return name
        # Simple name — try in same module
        from .ruby_helpers import underscore
        name = underscore(parent_class)
        if name.endswith("_controller"):
            name = name[:-11]
        # Prepend module prefix from child
        parts = child_name.split("/")
        if len(parts) > 1:
            module_prefix = "/".join(parts[:-1])
            return f"{module_prefix}/{name}"
        return name

    def _parse_controller(self, filepath: str,
                          controller_name: str) -> Optional[ControllerInfo]:
        """Parse a controller file and extract filters and params."""
        try:
            with open(filepath, "rb") as f:
                source = f.read()
        except OSError as e:
            logger.warning("Cannot read controller %s: %s", filepath, e)
            return None

        root = parse_ruby(source)
        info = ControllerInfo(name=controller_name)

        self._walk_controller(root, info)
        return info

    def _walk_controller(self, node: Node, info: ControllerInfo) -> None:
        """Walk controller AST to extract filters and params."""
        for child in node.children:
            self._process_controller_node(child, info)

    def _process_controller_node(self, node: Node, info: ControllerInfo) -> None:
        """Process a single node in a controller file."""
        # Extract class definition
        if node.type == "class":
            self._extract_class_info(node, info)
            self._walk_controller(node, info)
            return

        call_info = extract_call_info(node)

        # Check for call + block wrappers, but NOT for container nodes
        # that hold multiple children (body_statement, block_body, etc.)
        if call_info is None and node.type not in (
            "body_statement", "block_body", "program",
            "then", "else", "elsif",
        ):
            for child in node.children:
                inner = extract_call_info(child)
                if inner is not None:
                    method_name, args, _ = inner
                    block = None
                    for sibling in node.children:
                        if sibling.type in ("do_block", "block"):
                            block = sibling
                            break
                    call_info = (method_name, args, block)
                    break

        # Check for method definitions with _params suffix
        if node.type == "method":
            self._extract_params_method(node, info)

        if call_info is None:
            self._walk_controller(node, info)
            return

        method_name, args, block_node = call_info

        if method_name in ("before_action", "before_filter"):
            self._extract_before_action(args, info)
        elif method_name in ("skip_before_action", "skip_before_filter"):
            self._extract_skip_before_action(args, info)
        else:
            self._walk_controller(node, info)

    def _extract_class_info(self, node: Node, info: ControllerInfo) -> None:
        """Extract class name and parent from a class definition."""
        name_node = node.child_by_field_name("name")
        superclass_node = node.child_by_field_name("superclass")

        if name_node:
            info.class_name = node_text(name_node)
        if superclass_node:
            # superclass node text is "< ClassName", extract just the class name
            # Look for constant child
            for child in superclass_node.children:
                if child.type in ("constant", "scope_resolution"):
                    info.parent_class = node_text(child).strip()
                    break
            if info.parent_class is None:
                text = node_text(superclass_node).strip()
                if text.startswith("<"):
                    text = text[1:].strip()
                info.parent_class = text

    def _extract_before_action(self, args: List[Node], info: ControllerInfo) -> None:
        """Extract before_action declarations."""
        filter_name = None
        only_actions: Optional[List[str]] = None
        except_actions: Optional[List[str]] = None

        filtered = [a for a in args if a.type not in (",", "(", ")")]

        for arg in filtered:
            if arg.type in ("simple_symbol", "string") and filter_name is None:
                filter_name = extract_string_value(arg)
            elif arg.type == "pair":
                key_node = arg.child_by_field_name("key")
                value_node = arg.child_by_field_name("value")
                if key_node is None or value_node is None:
                    children = [c for c in arg.children if c.type not in (":", "=>", ",")]
                    if len(children) >= 2:
                        key_node, value_node = children[0], children[1]
                    else:
                        continue
                key = extract_string_value(key_node)
                if key == "only":
                    only_actions = extract_array_elements(value_node)
                    if not only_actions:
                        val = extract_string_value(value_node)
                        if val:
                            only_actions = [val]
                elif key == "except":
                    except_actions = extract_array_elements(value_node)
                    if not except_actions:
                        val = extract_string_value(value_node)
                        if val:
                            except_actions = [val]

        if filter_name:
            info.before_actions.append(BeforeAction(
                filter_name=filter_name,
                only=only_actions,
                except_=except_actions,
            ))

    def _extract_skip_before_action(self, args: List[Node],
                                    info: ControllerInfo) -> None:
        """Extract skip_before_action declarations."""
        filter_name = None
        only_actions = None
        except_actions = None

        filtered = [a for a in args if a.type not in (",", "(", ")")]

        for arg in filtered:
            if arg.type in ("simple_symbol", "string") and filter_name is None:
                filter_name = extract_string_value(arg)
            elif arg.type == "pair":
                key_node = arg.child_by_field_name("key")
                value_node = arg.child_by_field_name("value")
                if key_node is None or value_node is None:
                    children = [c for c in arg.children if c.type not in (":", "=>", ",")]
                    if len(children) >= 2:
                        key_node, value_node = children[0], children[1]
                    else:
                        continue
                key = extract_string_value(key_node)
                if key == "only":
                    only_actions = extract_array_elements(value_node)
                    if not only_actions:
                        val = extract_string_value(value_node)
                        if val:
                            only_actions = [val]
                elif key == "except":
                    except_actions = extract_array_elements(value_node)
                    if not except_actions:
                        val = extract_string_value(value_node)
                        if val:
                            except_actions = [val]

        if filter_name:
            info.skip_before_actions.append(BeforeAction(
                filter_name=filter_name,
                only=only_actions,
                except_=except_actions,
            ))

    def _extract_params_method(self, node: Node, info: ControllerInfo) -> None:
        """Extract strong params from a method ending in _params."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        method_name = node_text(name_node)
        if not method_name.endswith("_params"):
            return

        # Find params.require(...).permit(...) calls
        params = self._find_permit_calls(node)
        if params:
            info.strong_params[method_name] = params

    def _find_permit_calls(self, node: Node) -> List[Parameter]:
        """Find params.permit or params.require.permit calls recursively."""
        text = node_text(node)
        params = []

        # Look for .permit( patterns
        permit_match = re.search(r"\.permit\(([^)]*)\)", text)
        if permit_match:
            permit_args = permit_match.group(1)
            # Parse the arguments: :name, :email, nested: [:a, :b]
            for match in re.finditer(r":(\w+)", permit_args):
                param_name = match.group(1)
                params.append(Parameter(
                    name=param_name,
                    location="body",
                    param_type="string",
                    required=False,
                ))

        return params

    def _apply_filters(self, ep: Endpoint, info: ControllerInfo) -> None:
        """Apply before_action filters to an endpoint."""
        # Build set of skipped filter names for this action
        skipped = set()
        for skip in info.skip_before_actions:
            # Check if the skip applies to this action
            if skip.only is not None and ep.action not in skip.only:
                continue
            if skip.except_ is not None and ep.action in skip.except_:
                continue
            skipped.add(skip.filter_name)

        active_filters = []
        for ba in info.before_actions:
            if ba.filter_name in skipped:
                continue
            if ba.only is not None and ep.action not in ba.only:
                continue
            if ba.except_ is not None and ep.action in ba.except_:
                continue
            active_filters.append(ba.filter_name)

        ep.auth_filters = [f for f in active_filters if self._is_auth_filter(f)]
        ep.has_auth = len(ep.auth_filters) > 0

    def _apply_params(self, ep: Endpoint, info: ControllerInfo) -> None:
        """Apply strong params to an endpoint."""
        if ep.action not in ("create", "update"):
            return

        # Find matching params method
        # Convention: <resource>_params, e.g., user_params
        for method_name, params in info.strong_params.items():
            ep.body_params = params
            break  # Use first found

    @staticmethod
    def _is_auth_filter(name: str) -> bool:
        """Check if a filter name looks like an authentication filter."""
        if name in AUTH_EXACT:
            return True
        return bool(AUTH_PATTERNS.search(name))


class BeforeAction:
    """Represents a before_action declaration."""

    def __init__(self, filter_name: str, only: Optional[List[str]] = None,
                 except_: Optional[List[str]] = None):
        self.filter_name = filter_name
        self.only = only
        self.except_ = except_


class ControllerInfo:
    """Parsed controller information."""

    def __init__(self, name: str):
        self.name = name
        self.class_name: Optional[str] = None
        self.parent_class: Optional[str] = None
        self.before_actions: List[BeforeAction] = []
        self.skip_before_actions: List[str] = []
        self.strong_params: Dict[str, List[Parameter]] = {}
