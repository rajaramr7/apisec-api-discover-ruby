"""Tests for the controller scanner."""

import os

import pytest

from api_discover.controller_scanner import ControllerScanner
from api_discover.models import Endpoint

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "sample_app")


class TestControllerScanner:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.scanner = ControllerScanner(FIXTURES)

    def test_detects_auth_on_api_controller(self):
        ep = Endpoint(
            method="GET", path="/api/v1/users",
            controller="api/v1/users", action="index",
        )
        self.scanner.scan([ep])
        assert ep.has_auth is True
        assert "authenticate_api_user!" in ep.auth_filters

    def test_detects_skip_before_action(self):
        ep = Endpoint(
            method="GET", path="/health",
            controller="health", action="check",
        )
        self.scanner.scan([ep])
        # HealthController skips authenticate_user!, so no auth on this action
        assert ep.has_auth is False

    def test_webhook_no_auth(self):
        ep = Endpoint(
            method="POST", path="/webhooks/stripe",
            controller="webhooks", action="stripe",
        )
        self.scanner.scan([ep])
        assert ep.has_auth is False

    def test_admin_has_auth(self):
        ep = Endpoint(
            method="GET", path="/admin/users",
            controller="admin/users", action="index",
        )
        self.scanner.scan([ep])
        assert ep.has_auth is True

    def test_posts_index_no_auth(self):
        """PostsController skips auth for index and show."""
        ep = Endpoint(
            method="GET", path="/posts",
            controller="posts", action="index",
        )
        self.scanner.scan([ep])
        assert ep.has_auth is False

    def test_posts_create_has_auth(self):
        """PostsController requires auth for create."""
        ep = Endpoint(
            method="POST", path="/posts",
            controller="posts", action="create",
        )
        self.scanner.scan([ep])
        assert ep.has_auth is True

    def test_strong_params_extracted(self):
        ep = Endpoint(
            method="POST", path="/posts",
            controller="posts", action="create",
        )
        self.scanner.scan([ep])
        assert len(ep.body_params) > 0
        param_names = {p.name for p in ep.body_params}
        assert "title" in param_names
        assert "body" in param_names

    def test_mounted_engine_skipped(self):
        ep = Endpoint(
            method="*", path="/sidekiq",
            controller="", action="",
            is_mounted_engine=True,
            engine_name="Sidekiq::Web",
        )
        self.scanner.scan([ep])
        # Should not crash on mounted engines
        assert ep.has_auth is None
