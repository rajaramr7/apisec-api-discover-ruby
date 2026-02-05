"""GitHub Action entrypoint — runs the api-discover pipeline and writes outputs."""

from __future__ import annotations

import os
import sys


def _env(name: str, default: str = "") -> str:
    """Read an environment variable (INPUT_* convention)."""
    return os.environ.get(name, default).strip()


def _env_bool(name: str) -> bool:
    return _env(name).lower() in ("true", "1", "yes")


def _write_output(name: str, value: str) -> None:
    """Append a key=value pair to $GITHUB_OUTPUT."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{name}={value}\n")


def _write_summary(markdown: str) -> None:
    """Append Markdown to $GITHUB_STEP_SUMMARY."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as f:
            f.write(markdown)


def main() -> int:
    # Read inputs
    source = _env("INPUT_SOURCE", ".")
    output = _env("INPUT_OUTPUT", "openapi-spec.yaml")
    fmt = _env("INPUT_FORMAT", "yaml")
    show_all = _env_bool("INPUT_SHOW_ALL")
    include_conditional = _env_bool("INPUT_INCLUDE_CONDITIONAL")
    exclude_engines = _env_bool("INPUT_EXCLUDE_ENGINES")
    token = _env("INPUT_TOKEN") or None
    fail_on_unprotected = _env_bool("INPUT_FAIL_ON_UNPROTECTED")

    # Import pipeline modules
    from api_discover.repo import RepoResolver
    from api_discover.detector import detect_rails
    from api_discover.route_parser import RouteParser
    from api_discover.controller_scanner import ControllerScanner
    from api_discover.oas_emitter import emit_openapi, emit_yaml, emit_json

    # 1. Resolve repo
    resolver = RepoResolver(source, token=token)
    try:
        repo_root = resolver.resolve()
        print(f"Repo resolved: {repo_root}")
    except ValueError as e:
        print(f"::error::Failed to resolve repo: {e}")
        return 1

    try:
        # 2. Detect framework
        is_rails, version = detect_rails(repo_root)
        if not is_rails:
            print("::warning::Rails gem not found in Gemfile. Proceeding anyway.")
        else:
            print(f"Rails detected: {version or 'unknown'}")

        # 3. Parse routes
        parser = RouteParser(repo_root)
        endpoints = parser.parse()
        print(f"Discovered {len(endpoints)} endpoints")

        if not endpoints:
            print("::warning::No endpoints found. Check that config/routes.rb exists.")
            _write_output("spec-path", output)
            _write_output("total-endpoints", "0")
            _write_output("authenticated-count", "0")
            _write_output("unprotected-count", "0")
            _write_output("unknown-count", "0")
            _write_output("has-unprotected", "false")
            _write_summary("## API Discover\n\nNo endpoints found.\n")
            return 0

        # 4. Scan controllers
        scanner = ControllerScanner(repo_root)
        scanner.scan(endpoints)

        # Compute stats
        total = len(endpoints)
        authenticated = sum(1 for ep in endpoints if ep.has_auth is True)
        unprotected = sum(1 for ep in endpoints if ep.has_auth is False)
        unknown = sum(1 for ep in endpoints if ep.has_auth is None)
        has_unprotected = unprotected > 0

        print(f"Auth analysis: {authenticated} authenticated, "
              f"{unprotected} unprotected, {unknown} unknown")

        # 5. Emit OpenAPI spec
        repo_name = os.path.basename(repo_root)
        spec = emit_openapi(endpoints, repo_name=repo_name,
                            include_conditional=include_conditional,
                            exclude_engines=exclude_engines)

        if fmt == "json":
            spec_content = emit_json(spec)
        else:
            spec_content = emit_yaml(spec)

        with open(output, "w") as f:
            f.write(spec_content)
        print(f"OpenAPI spec written to: {output}")

        # 6. Write outputs
        _write_output("spec-path", output)
        _write_output("total-endpoints", str(total))
        _write_output("authenticated-count", str(authenticated))
        _write_output("unprotected-count", str(unprotected))
        _write_output("unknown-count", str(unknown))
        _write_output("has-unprotected", str(has_unprotected).lower())

        # 7. Write step summary
        summary_lines = [
            "## API Discover Results\n\n",
            f"| Metric | Count |\n",
            f"|---|---|\n",
            f"| Total endpoints | {total} |\n",
            f"| Authenticated | {authenticated} |\n",
            f"| **Unprotected** | **{unprotected}** |\n",
            f"| Unknown auth | {unknown} |\n",
            "\n",
        ]

        if has_unprotected:
            summary_lines.append("### Unprotected Endpoints\n\n")
            summary_lines.append("| Method | Path | Controller |\n")
            summary_lines.append("|---|---|---|\n")
            for ep in endpoints:
                if ep.has_auth is False:
                    ctrl = ep.controller or "?"
                    summary_lines.append(
                        f"| `{ep.method}` | `{ep.path}` | {ctrl}#{ep.action} |\n"
                    )
            summary_lines.append("\n")

        if show_all:
            summary_lines.append("<details>\n<summary>All Endpoints</summary>\n\n")
            summary_lines.append("| Method | Path | Auth |\n")
            summary_lines.append("|---|---|---|\n")
            for ep in sorted(endpoints, key=lambda e: (e.path, e.method)):
                if ep.has_auth is True:
                    auth = "authenticated"
                elif ep.has_auth is False:
                    auth = "UNPROTECTED"
                else:
                    auth = "unknown"
                summary_lines.append(
                    f"| `{ep.method}` | `{ep.path}` | {auth} |\n"
                )
            summary_lines.append("\n</details>\n")

        summary_lines.append(
            f"\nSpec written to `{output}` ({fmt})\n"
        )

        _write_summary("".join(summary_lines))

        # 8. Quality gate
        if fail_on_unprotected and has_unprotected:
            print(f"::error::Found {unprotected} unprotected endpoint(s). "
                  f"Failing because fail-on-unprotected is enabled.")
            return 1

        return 0

    finally:
        resolver.cleanup()


if __name__ == "__main__":
    sys.exit(main())
