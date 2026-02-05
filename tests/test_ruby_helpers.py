"""Tests for Ruby helpers."""

from api_discover.ruby_helpers import (
    pluralize, singularize, underscore, camelize,
    parse_ruby, node_text, extract_call_info,
    extract_symbol_name, extract_string_value,
    extract_array_elements, extract_hash_from_args,
)


class TestInflection:
    def test_pluralize_regular(self):
        assert pluralize("user") == "users"
        assert pluralize("post") == "posts"
        assert pluralize("category") == "categories"
        assert pluralize("box") == "boxes"

    def test_pluralize_irregular(self):
        assert pluralize("person") == "people"
        assert pluralize("child") == "children"

    def test_singularize_regular(self):
        assert singularize("users") == "user"
        assert singularize("posts") == "post"
        assert singularize("categories") == "category"

    def test_singularize_irregular(self):
        assert singularize("people") == "person"
        assert singularize("children") == "child"

    def test_underscore(self):
        assert underscore("UsersController") == "users_controller"
        assert underscore("Api::V1::Users") == "api/v1/users"
        assert underscore("HTMLParser") == "html_parser"

    def test_camelize(self):
        assert camelize("users") == "Users"
        assert camelize("api/v1/users") == "Api::V1::Users"
        assert camelize("admin_users") == "AdminUsers"


class TestASTHelpers:
    def test_extract_call_info_command(self):
        root = parse_ruby(b"resources :users")
        # tree-sitter-ruby parses this as a `call` node
        child = root.children[0]
        info = extract_call_info(child)
        assert info is not None
        method_name, args, block = info
        assert method_name == "resources"

    def test_extract_symbol_name(self):
        root = parse_ruby(b":users")
        sym = root.children[0]
        name = extract_symbol_name(sym)
        assert name == "users"

    def test_extract_string_value(self):
        root = parse_ruby(b"'hello'")
        str_node = root.children[0]
        val = extract_string_value(str_node)
        assert val == "hello"

    def test_extract_array_elements(self):
        root = parse_ruby(b"[:index, :show, :create]")
        arr = root.children[0]
        elements = extract_array_elements(arr)
        assert elements == ["index", "show", "create"]
