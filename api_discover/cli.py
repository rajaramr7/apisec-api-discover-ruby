"""CLI entry point — orchestrates the API discovery pipeline."""

from __future__ import annotations

import logging
import os
import sys

import click

from . import __version__


@click.command()
@click.argument("source")
@click.option("--output", "-o", default="openapi-spec.yaml",
              help="Output file path (default: openapi-spec.yaml)")
@click.option("--format", "fmt", type=click.Choice(["yaml", "json"]),
              default="yaml", help="Output format (default: yaml)")
@click.option("--show-all", is_flag=True,
              help="Show all endpoints in table (default: unprotected only)")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--include-conditional", is_flag=True,
              help="Include env-conditional routes in spec")
@click.option("--exclude-engines", is_flag=True,
              help="Skip mounted engines")
@click.option("--token", envvar="GIT_TOKEN",
              help="Git auth token for private repos")
@click.version_option(version=__version__)
def main(source: str, output: str, fmt: str, show_all: bool, verbose: bool,
         include_conditional: bool, exclude_engines: bool,
         token: str) -> None:
    """Discover API endpoints in a Rails codebase.

    SOURCE is a local path or git URL to a Rails application.
    """
    # Set up logging
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s: %(message)s",
    )
    logger = logging.getLogger("api_discover")

    # Import here to keep CLI snappy for --help
    from .repo import RepoResolver
    from .detector import detect_rails
    from .route_parser import RouteParser
    from .controller_scanner import ControllerScanner
    from .oas_emitter import emit_openapi, emit_yaml, emit_json
    from .reporter import print_report

    from rich.console import Console
    console = Console()

    # 1. Resolve repo
    resolver = RepoResolver(source, token=token)
    try:
        repo_root = resolver.resolve()
        console.print(f"[green]✓[/green] Repo resolved: {repo_root}")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    try:
        # 2. Detect framework
        is_rails, version = detect_rails(repo_root)
        if not is_rails:
            console.print("[yellow]Warning:[/yellow] Rails gem not found in Gemfile. "
                          "Proceeding anyway (routes.rb exists).")
        else:
            ver_str = version or "unknown"
            console.print(f"[green]✓[/green] Rails detected: {ver_str}")

        # 3. Parse routes
        console.print("[dim]Parsing routes...[/dim]")
        parser = RouteParser(repo_root)
        endpoints = parser.parse()
        console.print(f"[green]✓[/green] Discovered {len(endpoints)} endpoints")

        if not endpoints:
            console.print("[yellow]No endpoints found. Check that config/routes.rb "
                          "contains route definitions.[/yellow]")
            return

        # 4. Scan controllers
        console.print("[dim]Scanning controllers...[/dim]")
        scanner = ControllerScanner(repo_root)
        scanner.scan(endpoints)

        auth_count = sum(1 for ep in endpoints if ep.has_auth is True)
        unauth_count = sum(1 for ep in endpoints if ep.has_auth is False)
        console.print(f"[green]✓[/green] Auth analysis: "
                      f"{auth_count} authenticated, "
                      f"{unauth_count} unprotected")

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
        console.print(f"[green]✓[/green] OpenAPI spec written to: {output}")

        # 6. Print report
        console.print()
        print_report(endpoints, show_all=show_all)

    finally:
        resolver.cleanup()


if __name__ == "__main__":
    main()
