"""AST node extraction utilities and Ruby inflection helpers."""

from __future__ import annotations

import re
from typing import Optional, Tuple, List

import tree_sitter_ruby as tsruby
from tree_sitter import Language, Parser, Node

RUBY_LANGUAGE = Language(tsruby.language())


def get_parser() -> Parser:
    """Return a tree-sitter parser for Ruby."""
    return Parser(RUBY_LANGUAGE)


def parse_ruby(source: bytes) -> Node:
    """Parse Ruby source and return the root node."""
    parser = get_parser()
    tree = parser.parse(source)
    return tree.root_node


def node_text(node: Node) -> str:
    """Get the text content of a node as a string."""
    if node is None:
        return ""
    return node.text.decode("utf-8")


def extract_call_info(node: Node) -> Optional[Tuple[str, List[Node], Optional[Node]]]:
    """Extract (method_name, argument_nodes, block_node) from any call-like node.

    Handles: call, command, command_call, method_call, and their _with_block wrappers.
    Returns None if the node is not a method call.
    """
    block_node = None

    # Check if this node is a block wrapper (do_block, block)
    # In tree-sitter-ruby, a `call` with a block is still a `call` node
    # but may have a block child
    if node.type == "do_block" or node.type == "block":
        # This is a block; the call is the preceding sibling or parent context
        return None

    # Handle `method arg1, arg2` (no parens, no receiver)
    if node.type == "call":
        # `call` in tree-sitter-ruby 0.23 is: method(args) or method args
        # It has: method, arguments (argument_list), and optionally a block
        method_node = node.child_by_field_name("method")
        if method_node is None:
            return None
        method_name = node_text(method_node)
        args = []
        block = None
        for child in node.children:
            if child.type == "argument_list":
                args = list(child.children)
            elif child.type in ("do_block", "block"):
                block = child
        return (method_name, args, block)

    if node.type == "command":
        # `command` is: identifier arg1, arg2 (no parens, no receiver)
        # First child is identifier (method name), rest are arguments
        children = list(node.children)
        if not children:
            return None
        method_name = node_text(children[0])
        args = children[1:]  # everything after the method name
        return (method_name, args, None)

    if node.type == "command_call":
        # receiver.method arg1, arg2 (no parens)
        method_node = node.child_by_field_name("method")
        if method_node is None:
            # Fall back to positional
            children = list(node.children)
            # Format: receiver . method args
            if len(children) >= 3:
                method_name = node_text(children[2])
            else:
                return None
        else:
            method_name = node_text(method_node)
        args = []
        for child in node.children:
            if child.type == "argument_list":
                args = list(child.children)
        return (method_name, args, None)

    # The call/command may be wrapped in a node that includes a block
    # e.g., a parent node whose children are [call_node, do_block]
    # In tree-sitter-ruby 0.23, the grammar for `resources :users do ... end`
    # creates a single `call` or `command` node that has the do_block as a child
    return None


def find_block_body(block_node: Node) -> List[Node]:
    """Extract the body statements from a do_block or block node."""
    if block_node is None:
        return []
    # do_block: do |params| ... end -> body_statement
    # block: { |params| ... } -> body_statement or block_body
    for child in block_node.children:
        if child.type == "body_statement" or child.type == "block_body":
            return list(child.children)
    return []


def walk_statements(node: Node) -> List[Node]:
    """Walk a node and yield direct child statements.

    For program nodes, returns top-level children.
    For body_statement nodes, returns children.
    """
    if node.type in ("program", "body_statement", "block_body"):
        return [c for c in node.children if c.type not in ("end", "do", "{", "}")]
    return [node]


def extract_symbol_name(node: Node) -> Optional[str]:
    """Extract the name from a :symbol node. Returns None if not a symbol."""
    text = node_text(node)
    if text.startswith(":"):
        return text[1:]
    return None


def extract_string_value(node: Node) -> Optional[str]:
    """Extract the value from a string node."""
    if node.type == "string":
        # String node has children: `"`, string_content, `"`
        for child in node.children:
            if child.type == "string_content":
                return node_text(child)
        # Empty string
        return ""
    if node.type == "simple_symbol":
        return extract_symbol_name(node)
    if node.type == "string_content":
        return node_text(node)
    text = node_text(node)
    if text.startswith('"') or text.startswith("'"):
        return text[1:-1] if len(text) >= 2 else ""
    if text.startswith(":"):
        return text[1:]
    return text


def extract_array_elements(node: Node) -> List[str]:
    """Extract string/symbol values from an array node like [:show, :index]."""
    if node.type == "array":
        results = []
        for child in node.children:
            if child.type in ("simple_symbol", "string", "string_content"):
                val = extract_string_value(child)
                if val:
                    results.append(val)
        return results
    if node.type == "symbol_array":
        # %i[show index] or %I[show index]
        results = []
        for child in node.children:
            if child.type == "bare_symbol":
                results.append(node_text(child))
        return results
    return []


def extract_hash_from_args(args: List[Node]) -> dict:
    """Extract keyword arguments / hash pairs from argument list.

    Handles both Ruby 3 style `key: value` (pair nodes) and
    hash literal `{ key: value }` arguments.
    Returns a dict mapping string keys to Node values.
    """
    result = {}
    for arg in args:
        if arg.type == "pair":
            key_node = arg.child_by_field_name("key")
            value_node = arg.child_by_field_name("value")
            if key_node is None or value_node is None:
                # Fallback: pair has children [key, "=>", value] or [key, value]
                children = [c for c in arg.children if c.type not in (":", "=>", ",")]
                if len(children) >= 2:
                    key_node, value_node = children[0], children[1]
                else:
                    continue
            key = extract_string_value(key_node)
            if key:
                result[key] = value_node
        elif arg.type == "hash":
            # Recurse into hash literal
            for child in arg.children:
                if child.type == "pair":
                    key_node = child.child_by_field_name("key")
                    value_node = child.child_by_field_name("value")
                    if key_node is None or value_node is None:
                        children = [c for c in child.children if c.type not in (":", "=>", ",")]
                        if len(children) >= 2:
                            key_node, value_node = children[0], children[1]
                        else:
                            continue
                    key = extract_string_value(key_node)
                    if key:
                        result[key] = value_node
        elif arg.type == "hash_splat_argument":
            # **options — can't resolve statically
            pass
    return result


def extract_rocket_pair(node: Node) -> Optional[Tuple[str, str]]:
    """Extract 'controller#action' from a `=> 'controller#action'` pair."""
    text = node_text(node).strip()
    if "#" in text:
        # Remove quotes
        text = text.strip("'\"")
        parts = text.split("#", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
    return None


# ---- Ruby inflection helpers ----

# Common irregular plurals
IRREGULARS = {
    "person": "people",
    "child": "children",
    "man": "men",
    "woman": "women",
    "tooth": "teeth",
    "foot": "feet",
    "mouse": "mice",
    "goose": "geese",
    "ox": "oxen",
    "datum": "data",
    "medium": "media",
    "analysis": "analyses",
    "crisis": "crises",
    "thesis": "theses",
}

IRREGULARS_REVERSE = {v: k for k, v in IRREGULARS.items()}

# Uncountable words (same singular and plural)
UNCOUNTABLE = {
    "equipment", "information", "rice", "money", "species",
    "series", "fish", "sheep", "jeans", "police", "data",
    "feedback", "status", "metadata",
}

PLURAL_RULES = [
    (r"(quiz)$", r"\1zes"),
    (r"^(oxen)$", r"\1"),
    (r"^(ox)$", r"\1en"),
    (r"(m|l)ice$", r"\1ice"),
    (r"(m|l)ouse$", r"\1ice"),
    (r"(pea)se$", r"\1se"),
    (r"(pea)$", r"\1se"),
    (r"(matr|vert|append)ix$", r"\1ices"),
    (r"(x|ch|ss|sh)$", r"\1es"),
    (r"([^aeiouy]|qu)y$", r"\1ies"),
    (r"(hive)$", r"\1s"),
    (r"([^f])fe$", r"\1ves"),
    (r"([lr])f$", r"\1ves"),
    (r"sis$", "ses"),
    (r"([ti])a$", r"\1a"),
    (r"([ti])um$", r"\1a"),
    (r"(buffal|tomat|volcan)o$", r"\1oes"),
    (r"(bu|mis|gas)s$", r"\1ses"),
    (r"(alias|status)$", r"\1es"),
    (r"(octop|vir|radi|nucle|fung|cact|stimul)us$", r"\1i"),
    (r"(octop|vir|radi|nucle|fung|cact|stimul)i$", r"\1i"),
    (r"(ax|test)is$", r"\1es"),
    (r"s$", "s"),
    (r"$", "s"),
]

SINGULAR_RULES = [
    (r"(database)s$", r"\1"),
    (r"(quiz)zes$", r"\1"),
    (r"(matr)ices$", r"\1ix"),
    (r"(vert|append)ices$", r"\1ix"),
    (r"^(ox)en", r"\1"),
    (r"(alias|status)es$", r"\1"),
    (r"(octop|vir|radi|nucle|fung|cact|stimul)i$", r"\1us"),
    (r"(cris|ax|test)es$", r"\1is"),
    (r"(shoe)s$", r"\1"),
    (r"(o)es$", r"\1"),
    (r"(bus)es$", r"\1"),
    (r"(m|l)ice$", r"\1ouse"),
    (r"(x|ch|ss|sh)es$", r"\1"),
    (r"(m)ovies$", r"\1ovie"),
    (r"(s)eries$", r"\1eries"),
    (r"([^aeiouy]|qu)ies$", r"\1y"),
    (r"([lr])ves$", r"\1f"),
    (r"(tive)s$", r"\1"),
    (r"(hive)s$", r"\1"),
    (r"([^f])ves$", r"\1fe"),
    (r"(t)he(sis|ses)$", r"\1hesis"),
    (r"(analy)(sis|ses)$", r"\1sis"),
    (r"([ti])a$", r"\1um"),
    (r"((a)naly|(b)a|(d)iagno|(p)arenthe|(p)rogno|(s)ynop|(t)he)ses$", r"\1\2sis"),
    (r"(n)ews$", r"\1ews"),
    (r"s$", ""),
]


def pluralize(word: str) -> str:
    """Pluralize a word using Rails-like inflection rules."""
    if not word:
        return word
    lower = word.lower()
    if lower in UNCOUNTABLE:
        return word
    if lower in IRREGULARS:
        return IRREGULARS[lower]
    for pattern, replacement in PLURAL_RULES:
        result, count = re.subn(pattern, replacement, word, flags=re.IGNORECASE)
        if count > 0:
            return result
    return word + "s"


def singularize(word: str) -> str:
    """Singularize a word using Rails-like inflection rules."""
    if not word:
        return word
    lower = word.lower()
    if lower in UNCOUNTABLE:
        return word
    if lower in IRREGULARS_REVERSE:
        return IRREGULARS_REVERSE[lower]
    for pattern, replacement in SINGULAR_RULES:
        result, count = re.subn(pattern, replacement, word, flags=re.IGNORECASE)
        if count > 0:
            return result
    return word


def underscore(camel: str) -> str:
    """Convert CamelCase to snake_case (Rails-style underscore)."""
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", camel)
    s2 = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s1)
    return s2.replace("-", "_").replace("::", "/").lower()


def camelize(snake: str) -> str:
    """Convert snake_case to CamelCase."""
    parts = snake.split("/")
    return "::".join("".join(word.capitalize() for word in part.split("_")) for part in parts)
