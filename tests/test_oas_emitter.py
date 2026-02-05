"""Tests for the OpenAPI emitter."""

import json

import yaml

from api_discover.models import Endpoint, Parameter
from api_discover.oas_emitter import emit_openapi, emit_yaml, emit_json


def _sample_endpoints():
    return [
        Endpoint(
            method="GET", path="/api/v1/users",
            controller="api/v1/users", action="index",
            has_auth=True, auth_filters=["authenticate_user!"],
            source_file="config/routes.rb", source_line=10,
        ),
        Endpoint(
            method="POST", path="/api/v1/users",
            controller="api/v1/users", action="create",
            has_auth=True, auth_filters=["authenticate_user!"],
            body_params=[
                Parameter(name="name", location="body"),
                Parameter(name="email", location="body"),
            ],
            source_file="config/routes.rb", source_line=10,
        ),
        Endpoint(
            method="POST", path="/webhooks/stripe",
            controller="webhooks", action="stripe",
            has_auth=False,
            source_file="config/routes.rb", source_line=20,
        ),
        Endpoint(
            method="*", path="/sidekiq",
            controller="", action="",
            is_mounted_engine=True, engine_name="Sidekiq::Web",
        ),
    ]


class TestOASEmitter:
    def test_emits_valid_openapi(self):
        spec = emit_openapi(_sample_endpoints(), repo_name="test-app")
        assert spec["openapi"] == "3.0.3"
        assert "paths" in spec
        assert "/api/v1/users" in spec["paths"]

    def test_auth_status_extension(self):
        spec = emit_openapi(_sample_endpoints())
        get_op = spec["paths"]["/api/v1/users"]["get"]
        assert get_op["x-auth-status"] == "authenticated"

        post_webhook = spec["paths"]["/webhooks/stripe"]["post"]
        assert post_webhook["x-auth-status"] == "UNPROTECTED"

    def test_request_body_from_params(self):
        spec = emit_openapi(_sample_endpoints())
        post_op = spec["paths"]["/api/v1/users"]["post"]
        assert "requestBody" in post_op
        schema = post_op["requestBody"]["content"]["application/json"]["schema"]
        assert "name" in schema["properties"]
        assert "email" in schema["properties"]

    def test_mounted_engine_extension(self):
        spec = emit_openapi(_sample_endpoints())
        sidekiq_path = spec["paths"].get("/sidekiq", {})
        assert "x-mounted-engine" in sidekiq_path

    def test_yaml_output(self):
        spec = emit_openapi(_sample_endpoints())
        yaml_str = emit_yaml(spec)
        parsed = yaml.safe_load(yaml_str)
        assert parsed["openapi"] == "3.0.3"

    def test_json_output(self):
        spec = emit_openapi(_sample_endpoints())
        json_str = emit_json(spec)
        parsed = json.loads(json_str)
        assert parsed["openapi"] == "3.0.3"

    def test_exclude_engines(self):
        spec = emit_openapi(_sample_endpoints(), exclude_engines=True)
        assert "/sidekiq" not in spec["paths"]

    def test_path_params_converted(self):
        eps = [Endpoint(
            method="GET", path="/users/:id",
            controller="users", action="show",
            path_params=["id"],
        )]
        spec = emit_openapi(eps)
        assert "/users/{id}" in spec["paths"]
        params = spec["paths"]["/users/{id}"]["get"]["parameters"]
        assert any(p["name"] == "id" for p in params)
