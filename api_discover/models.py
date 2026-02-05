"""Data models for API Discover."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Parameter:
    name: str
    location: str  # "path" | "query" | "body"
    param_type: str = "string"  # "string" | "integer"
    required: bool = False


@dataclass
class Endpoint:
    method: str  # GET, POST, PUT, PATCH, DELETE
    path: str  # /api/v1/users/:id
    controller: str  # api/v1/users
    action: str  # show
    path_params: list = field(default_factory=list)
    body_params: list = field(default_factory=list)  # list[Parameter]
    auth_filters: list = field(default_factory=list)
    has_auth: Optional[bool] = None  # derived from auth_filters
    source_file: str = ""
    source_line: int = 0
    condition: Optional[str] = None
    is_mounted_engine: bool = False
    engine_name: Optional[str] = None
    is_redirect: bool = False
    is_dynamic: bool = False  # flagged for .each loops etc.


@dataclass
class RouteContext:
    """Tracks nesting state during route parsing."""

    path_prefix: str = ""
    module_prefix: str = ""
    controller: Optional[str] = None
    resource_name: Optional[str] = None
    resource_param: str = ":id"
    scope_type: Optional[str] = None  # "member" | "collection" | None
    as_prefix: Optional[str] = None  # route helper name prefix

    def copy(self) -> RouteContext:
        return RouteContext(
            path_prefix=self.path_prefix,
            module_prefix=self.module_prefix,
            controller=self.controller,
            resource_name=self.resource_name,
            resource_param=self.resource_param,
            scope_type=self.scope_type,
            as_prefix=self.as_prefix,
        )
