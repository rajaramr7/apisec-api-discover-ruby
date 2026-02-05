"""Rich console output: summary table with auth status."""

from __future__ import annotations

from typing import List

from rich.console import Console
from rich.table import Table

from .models import Endpoint
from .ruby_helpers import camelize


def print_report(endpoints: List[Endpoint], show_all: bool = False) -> None:
    """Print a summary report of discovered endpoints."""
    console = Console()

    if not endpoints:
        console.print("[yellow]No endpoints discovered.[/yellow]")
        return

    # Filter for display
    if show_all:
        display_eps = endpoints
    else:
        display_eps = [ep for ep in endpoints if not ep.has_auth]

    # Build table
    table = Table(title="Discovered Endpoints" if show_all else "Unprotected Endpoints")
    table.add_column("Method", style="bold cyan", width=8)
    table.add_column("Path", style="white", max_width=40)
    table.add_column("Controller#Action", max_width=42)
    table.add_column("Auth", width=14)

    for ep in sorted(display_eps, key=lambda e: (e.path, e.method)):
        method_style = _method_style(ep.method)
        ctrl_action = _format_controller_action(ep)
        auth_display = _format_auth(ep)

        table.add_row(
            f"[{method_style}]{ep.method}[/{method_style}]",
            ep.path,
            ctrl_action,
            auth_display,
        )

    console.print(table)
    console.print()

    # Summary stats
    _print_summary(console, endpoints)


def _print_summary(console: Console, endpoints: List[Endpoint]) -> None:
    """Print summary statistics."""
    total = len(endpoints)
    authenticated = sum(1 for ep in endpoints if ep.has_auth is True)
    unprotected = sum(1 for ep in endpoints if ep.has_auth is False)
    unknown = sum(1 for ep in endpoints if ep.has_auth is None)
    conditional = sum(1 for ep in endpoints if ep.condition)
    engines = sum(1 for ep in endpoints if ep.is_mounted_engine)
    dynamic = sum(1 for ep in endpoints if ep.is_dynamic)

    console.print("[bold]Summary:[/bold]")
    console.print(f"  Total endpoints:   {total}")

    if total > 0:
        auth_pct = authenticated * 100 // total
        console.print(f"  Authenticated:     {authenticated:>4}  ({auth_pct}%)")

        if unprotected > 0:
            unprot_pct = unprotected * 100 // total
            console.print(
                f"  [bold red]UNPROTECTED:       {unprotected:>4}  ({unprot_pct}%)[/bold red]"
            )
        else:
            console.print(f"  UNPROTECTED:          0  (0%)")

        if unknown > 0:
            unk_pct = unknown * 100 // total
            console.print(f"  Unknown auth:      {unknown:>4}  ({unk_pct}%)")

        if conditional > 0:
            cond_pct = conditional * 100 // total
            console.print(f"  Conditional:       {conditional:>4}  ({cond_pct}%)")

        if engines > 0:
            eng_pct = engines * 100 // total
            console.print(f"  Mounted engines:   {engines:>4}  ({eng_pct}%)")

        if dynamic > 0:
            dyn_pct = dynamic * 100 // total
            console.print(
                f"  [yellow]Dynamic (unresolved): {dynamic:>3}  ({dyn_pct}%)[/yellow]"
            )

    console.print()


def _method_style(method: str) -> str:
    """Return a Rich style for an HTTP method."""
    styles = {
        "GET": "green",
        "POST": "yellow",
        "PUT": "blue",
        "PATCH": "blue",
        "DELETE": "red",
        "*": "magenta",
    }
    return styles.get(method, "white")


def _format_controller_action(ep: Endpoint) -> str:
    """Format the controller#action display string."""
    if ep.is_mounted_engine:
        return f"[magenta]{ep.engine_name or 'Engine'}[/magenta]"

    ctrl = camelize(ep.controller) + "Controller" if ep.controller else "?"
    action = ep.action or "?"

    display = f"{ctrl}#{action}"
    if len(display) > 42:
        display = display[:39] + "..."
    return display


def _format_auth(ep: Endpoint) -> str:
    """Format the auth status display."""
    if ep.is_mounted_engine:
        return "[magenta]engine[/magenta]"
    if ep.has_auth is True:
        filters = ", ".join(ep.auth_filters[:2])
        return f"[green]✓ {filters}[/green]"
    if ep.has_auth is False:
        return "[bold red]⚠ NONE[/bold red]"
    return "[yellow]? unknown[/yellow]"
