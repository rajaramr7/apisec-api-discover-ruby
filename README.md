# API Discover

A Python CLI tool that statically analyzes Rails codebases to discover all API endpoints, flags which ones lack authentication ("shadow APIs"), and outputs an OpenAPI 3.0 spec.

Built for security and platform teams who want to audit a Rails codebase for undocumented or unprotected endpoints — without running the application.

## What It Does

```
Input (local path or git URL)
  │
  ├─ 1. Repo Resolution ──────── clone or validate local path
  ├─ 2. Framework Detection ──── parse Gemfile for Rails gem + version
  ├─ 3. Route Discovery ─────── parse all route files (config/routes.rb + draw fragments)
  ├─ 4. Controller Analysis ──── map endpoints to controllers
  │     ├─ auth filter extraction (before_action / skip_before_action)
  │     └─ strong params extraction (params.permit)
  ├─ 5. OpenAPI 3.0 Spec ─────── YAML or JSON output
  └─ 6. Console Report ────────── summary table with auth status
```

## Installation

Requires Python 3.9+.

```bash
# Clone the repo
git clone https://github.com/rajaramr7/apisec-api-discover-ruby.git
cd apisec-api-discover-ruby

# Install
pip install -e .

# Or install with dev dependencies (for running tests)
pip install -e ".[dev]"
```

## Usage

### Basic

```bash
# Scan a local Rails app
api-discover /path/to/rails-app

# Scan a remote repo (public)
api-discover https://github.com/org/repo

# Scan a private repo
api-discover https://github.com/org/repo --token ghp_xxxxx
```

### Options

```
Usage: api-discover [OPTIONS] SOURCE

Options:
  -o, --output TEXT        Output file path (default: openapi-spec.yaml)
  --format [yaml|json]     Output format (default: yaml)
  --show-all               Show all endpoints in table (default: unprotected only)
  -v, --verbose            Enable debug logging
  --include-conditional    Include env-conditional routes in spec
  --exclude-engines        Skip mounted engines
  --token TEXT             Git auth token for private repos
  --version                Show the version and exit.
  --help                   Show this message and exit.
```

### Examples

```bash
# Output as JSON
api-discover /path/to/app --format json --output spec.json

# Show all endpoints (not just unprotected ones)
api-discover /path/to/app --show-all

# Skip mounted engines (Sidekiq, etc.)
api-discover /path/to/app --exclude-engines

# Verbose mode for debugging
api-discover /path/to/app -v
```

## Sample Output

### Console Report

```
┌──────────┬──────────────────────────┬─────────────────────────────────┬────────────┐
│ Method   │ Path                     │ Controller#Action               │ Auth       │
├──────────┼──────────────────────────┼─────────────────────────────────┼────────────┤
│ GET      │ /admin/users             │ Admin::UsersController#index    │ ✓ auth     │
│ POST     │ /webhooks/stripe         │ WebhooksController#stripe       │ ⚠ NONE     │
│ GET      │ /health                  │ HealthController#check          │ ⚠ NONE     │
│ DELETE   │ /api/v1/sessions/:id     │ Api::V1::SessionsController#... │ ? unknown  │
│ GET      │ /api/v1/users            │ Api::V1::UsersController#index  │ ✓ auth     │
└──────────┴──────────────────────────┴─────────────────────────────────┴────────────┘

Summary:
  Total endpoints:    61
  Authenticated:      22  (36%)
  UNPROTECTED:         4  (6%)
  Unknown auth:       35  (57%)
  Conditional:         1  (1%)
  Mounted engines:     1  (1%)
```

### OpenAPI Spec (excerpt)

```yaml
openapi: 3.0.3
info:
  title: API discovered from my-rails-app
  version: discovered
paths:
  /api/v1/users:
    get:
      operationId: api_v1_users_index
      tags: [api/v1/users]
      x-controller: Api::V1::UsersController
      x-action: index
      x-auth-status: authenticated
      x-auth-filters: [authenticate_user!]
      x-source: config/routes.rb
    post:
      operationId: api_v1_users_create
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                name: {type: string}
                email: {type: string}
      x-auth-status: UNPROTECTED
```

## What It Detects

### Rails Route DSL

| Pattern | Support |
|---|---|
| `resources` / `resource` | Full (with `only:`, `except:`, `path:`, `param:`) |
| `namespace` | Full |
| `scope` (path, module, controller) | Full |
| `member do` / `collection do` | Full |
| `concern` / `concerns` | Full |
| `draw(:name)` (route fragments) | Full |
| `mount Engine => '/path'` | Detected and flagged |
| `root` | Full |
| `get` / `post` / `put` / `patch` / `delete` | Full |
| `match` with `via:` | Full |
| `with_options` | Full |
| `constraints` | Block walked, constraint recorded |
| Conditional routes (`if Rails.env...`) | Included and tagged |
| Dynamic routes (`.each` loops) | Flagged as `[DYNAMIC]` |

### Auth Detection

Scans controllers for authentication filters by:

- Extracting `before_action` / `before_filter` declarations
- Respecting `only:` and `except:` options
- Handling `skip_before_action` with scoping
- Walking the inheritance chain (up to 3 levels)
- Matching filter names against known auth patterns:
  `authenticate_user!`, `authorize!`, `require_login`, `doorkeeper_authorize!`, and any filter matching `/auth|login|session|token|verify|signed_in/i`

### Strong Params

Extracts `params.require(:model).permit(:field1, :field2)` from controller methods ending in `_params`, and maps them to OpenAPI request body schemas.

## GitHub Action

Use api-discover as a GitHub Action to automatically scan your Rails app on every push or PR.

### Basic Usage

```yaml
- uses: rajaramr7/apisec-api-discover-ruby@main
  with:
    source: "."  # path to your Rails app (default: repo root)
```

### Full Example

```yaml
name: API Security Scan

on:
  pull_request:
    branches: [main]

jobs:
  api-discover:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Discover API endpoints
        id: discover
        uses: rajaramr7/apisec-api-discover-ruby@main
        with:
          source: "."
          output: openapi-spec.yaml
          format: yaml
          show-all: "true"
          fail-on-unprotected: "true"
          comment-on-pr: "true"

      - name: Upload OpenAPI spec
        uses: actions/upload-artifact@v4
        with:
          name: openapi-spec
          path: openapi-spec.yaml
```

### Inputs

| Input | Default | Description |
|---|---|---|
| `source` | `.` | Path to the Rails app |
| `output` | `openapi-spec.yaml` | Output file path |
| `format` | `yaml` | `yaml` or `json` |
| `show-all` | `false` | Include all endpoints in summary |
| `include-conditional` | `false` | Include env-conditional routes |
| `exclude-engines` | `false` | Skip mounted engines |
| `token` | | Git auth token for private repos |
| `fail-on-unprotected` | `false` | Fail the step if unprotected endpoints found |
| `comment-on-pr` | `false` | Post results as a PR comment |
| `python-version` | `3.11` | Python version to use |

### Outputs

| Output | Description |
|---|---|
| `spec-path` | Path to the generated spec file |
| `total-endpoints` | Total number of discovered endpoints |
| `authenticated-count` | Endpoints with authentication |
| `unprotected-count` | Endpoints without authentication |
| `unknown-count` | Endpoints with unknown auth status |
| `has-unprotected` | `true` if any unprotected endpoints found |

### Quality Gate

Set `fail-on-unprotected: "true"` to use api-discover as a quality gate. The step will exit with a failure if any unprotected (shadow) APIs are found, blocking the PR from merging.

## Limitations

- **Static analysis only** — the Rails app is never booted. Constants and runtime values cannot be resolved.
- **Rails only** — Grape, Sinatra, and Hanami are not supported.
- **Response schemas** — cannot be inferred statically (only request bodies from strong params).
- **Plugin routes** — routes added by gems/plugins are flagged but not resolved.
- **Auth heuristics** — detection is pattern-based. Custom auth mechanisms with non-standard names may be missed.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
