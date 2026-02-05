"""Tests for the route parser."""

import os
import tempfile

import pytest

from api_discover.route_parser import RouteParser
from api_discover.models import RouteContext

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "sample_app")


class TestRouteParserBasic:
    """Test basic route parsing against the sample app fixture."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.parser = RouteParser(FIXTURES)
        self.endpoints = self.parser.parse()

    def test_discovers_endpoints(self):
        assert len(self.endpoints) > 0

    def test_root_route(self):
        roots = [ep for ep in self.endpoints if ep.path == "/" and ep.method == "GET"]
        assert len(roots) == 1
        assert roots[0].action == "home"

    def test_resources_generates_seven_routes(self):
        """resources :posts should generate index, new, create, show, edit, update (x2), destroy = 9 base routes."""
        post_eps = [ep for ep in self.endpoints
                    if ep.controller == "posts" and ep.action in
                    ("index", "new", "create", "show", "edit", "update", "destroy")]
        actions = {ep.action for ep in post_eps}
        assert "index" in actions
        assert "show" in actions
        assert "create" in actions
        assert "update" in actions
        assert "destroy" in actions

    def test_member_route(self):
        publish_eps = [ep for ep in self.endpoints if ep.action == "publish"]
        assert len(publish_eps) >= 1
        ep = publish_eps[0]
        assert ep.method == "POST"
        assert ":id" in ep.path

    def test_collection_route(self):
        drafts_eps = [ep for ep in self.endpoints if ep.action == "drafts"]
        assert len(drafts_eps) >= 1
        ep = drafts_eps[0]
        assert ep.method == "GET"
        assert ":id" not in ep.path

    def test_nested_resources(self):
        comment_eps = [ep for ep in self.endpoints
                       if "comments" in ep.path and "posts" in ep.path]
        assert len(comment_eps) >= 1

    def test_singular_resource(self):
        profile_eps = [ep for ep in self.endpoints if "profile" in ep.path]
        assert len(profile_eps) >= 1
        # Singular resource should not have :id in show path
        show_eps = [ep for ep in profile_eps if ep.action == "show"]
        if show_eps:
            assert ":id" not in show_eps[0].path

    def test_namespace(self):
        api_eps = [ep for ep in self.endpoints if ep.path.startswith("/api/v1/")]
        assert len(api_eps) > 0

    def test_namespace_controller_prefix(self):
        api_user_eps = [ep for ep in self.endpoints
                        if ep.controller == "api/v1/users"]
        assert len(api_user_eps) > 0

    def test_scope_with_module(self):
        internal_eps = [ep for ep in self.endpoints if "/internal/" in ep.path]
        assert len(internal_eps) >= 1

    def test_http_verb_routes(self):
        health = [ep for ep in self.endpoints
                  if ep.path == "/health" and ep.method == "GET"]
        assert len(health) == 1

        webhook = [ep for ep in self.endpoints
                   if "webhooks" in ep.path and ep.method == "POST"]
        assert len(webhook) >= 1

    def test_match_multiple_verbs(self):
        search_eps = [ep for ep in self.endpoints if "search" in ep.path]
        methods = {ep.method for ep in search_eps}
        assert "GET" in methods
        assert "POST" in methods

    def test_mounted_engine(self):
        engines = [ep for ep in self.endpoints if ep.is_mounted_engine]
        assert len(engines) >= 1
        sidekiq = [ep for ep in engines if "Sidekiq" in (ep.engine_name or "")]
        assert len(sidekiq) >= 1

    def test_concern_replay(self):
        article_comment_eps = [ep for ep in self.endpoints
                               if "articles" in ep.path and "comments" in ep.path]
        assert len(article_comment_eps) >= 1

    def test_conditional_routes_tagged(self):
        debug_eps = [ep for ep in self.endpoints if "debug" in ep.path]
        assert len(debug_eps) >= 1
        assert debug_eps[0].condition is not None

    def test_draw_external_file(self):
        legacy_eps = [ep for ep in self.endpoints if "old-dashboard" in ep.path]
        assert len(legacy_eps) >= 1

    def test_only_filter(self):
        """resources :sessions, only: [:create, :destroy] should have only 2-3 endpoints."""
        session_eps = [ep for ep in self.endpoints
                       if ep.controller == "api/v1/sessions"]
        actions = {ep.action for ep in session_eps}
        assert "create" in actions
        assert "destroy" in actions
        assert "index" not in actions

    def test_except_filter(self):
        """resources :users, except: [:new, :edit] should not have new or edit."""
        api_user_eps = [ep for ep in self.endpoints
                        if ep.controller == "api/v1/users"]
        actions = {ep.action for ep in api_user_eps}
        assert "new" not in actions
        assert "edit" not in actions
        assert "index" in actions


class TestRouteParserInline:
    """Test route parsing with inline route definitions."""

    def _parse(self, ruby_source: str):
        """Parse a Ruby route source string and return endpoints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = os.path.join(tmpdir, "config")
            os.makedirs(config_dir)
            routes_file = os.path.join(config_dir, "routes.rb")
            with open(routes_file, "w") as f:
                f.write(ruby_source)
            parser = RouteParser(tmpdir)
            return parser.parse()

    def test_simple_resources(self):
        eps = self._parse("""
Rails.application.routes.draw do
  resources :users
end
""")
        actions = {ep.action for ep in eps}
        assert "index" in actions
        assert "show" in actions
        assert "create" in actions

    def test_resources_with_path_option(self):
        eps = self._parse("""
Rails.application.routes.draw do
  resources :users, path: 'people'
end
""")
        paths = {ep.path for ep in eps}
        assert any("/people" in p for p in paths)
        assert not any("/users" in p for p in paths)

    def test_deeply_nested_namespace(self):
        eps = self._parse("""
Rails.application.routes.draw do
  namespace :api do
    namespace :v2 do
      namespace :internal do
        resources :metrics, only: [:index]
      end
    end
  end
end
""")
        assert len(eps) >= 1
        assert eps[0].path == "/api/v2/internal/metrics"
        assert eps[0].controller == "api/v2/internal/metrics"

    def test_scope_path_only(self):
        eps = self._parse("""
Rails.application.routes.draw do
  scope '/v1' do
    resources :items, only: [:index]
  end
end
""")
        assert any(ep.path == "/v1/items" for ep in eps)

    def test_scope_module_only(self):
        eps = self._parse("""
Rails.application.routes.draw do
  scope module: :v1 do
    resources :items, only: [:index]
  end
end
""")
        items = [ep for ep in eps if ep.action == "index"]
        assert len(items) >= 1
        assert items[0].controller == "v1/items"

    def test_resource_singular(self):
        eps = self._parse("""
Rails.application.routes.draw do
  resource :session
end
""")
        actions = {ep.action for ep in eps}
        assert "show" in actions
        assert "create" in actions
        assert "destroy" in actions
        # Singular resource should not have index
        assert "index" not in actions
        # Show should not have :id
        show = [ep for ep in eps if ep.action == "show"]
        assert ":id" not in show[0].path

    def test_root_with_to(self):
        eps = self._parse("""
Rails.application.routes.draw do
  root to: 'home#index'
end
""")
        assert len(eps) >= 1
        assert eps[0].path == "/"
        assert eps[0].action == "index"
