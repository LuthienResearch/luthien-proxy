"""luthien policy -- view and manage the active gateway policy."""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from luthien_cli.config import load_config
from luthien_cli.gateway_client import GatewayClient, GatewayError

# Policies whose module path contains "presets" are grouped separately.
_PRESET_PATH = ".presets."


def _make_client() -> GatewayClient:
    config = load_config()
    return GatewayClient(base_url=config.gateway_url, admin_key=config.admin_key)


def _short_name(class_ref: str) -> str:
    """Extract class name from a full class ref like 'module.path:ClassName'."""
    return class_ref.rsplit(":", 1)[-1] if ":" in class_ref else class_ref


def _resolve_class_ref(name: str, policies: list[dict]) -> str | None:
    """Resolve a short name or full class_ref to the canonical class_ref.

    Tries exact class_ref match first, then case-insensitive class name match.
    Returns None if no match found.
    """
    for p in policies:
        if p["class_ref"] == name:
            return p["class_ref"]

    name_lower = name.lower()
    for p in policies:
        if _short_name(p["class_ref"]).lower() == name_lower:
            return p["class_ref"]

    return None


def _is_preset(p: dict) -> bool:
    return _PRESET_PATH in p.get("class_ref", "")


def _truncate(text: str, max_len: int = 40) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _policy_completions(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Shell completion callback for policy names."""
    try:
        client = _make_client()
        policies = client.list_policies()
    except Exception:
        return []
    names = [_short_name(p["class_ref"]) for p in policies]
    return [n for n in names if n.lower().startswith(incomplete.lower())]


def _interactive_pick(policies: list[dict], active_ref: str) -> int | None:
    """Show an arrow-key navigable menu. Returns selected index or None.

    The active policy is pinned at the top with a divider, then the rest
    follow below. Returns the index into the original `policies` list.
    """
    from simple_term_menu import TerminalMenu

    # Build ordered list: active first, divider, then rest
    active_idx = None
    for i, p in enumerate(policies):
        if p["class_ref"] == active_ref:
            active_idx = i
            break

    # Map from menu row -> original policy index
    menu_to_policy: list[int] = []
    entries: list[str] = []

    def _entry(p: dict, highlight: bool = False) -> str:
        name = _short_name(p["class_ref"])
        desc = _truncate(p.get("description", ""), 45)
        if highlight:
            return f"[active] {name}    {desc}"
        return f"         {name}    {desc}"

    if active_idx is not None:
        entries.append(_entry(policies[active_idx], highlight=True))
        menu_to_policy.append(active_idx)
        entries.append("")  # empty line as divider (skipped by skip_empty_entries)
        menu_to_policy.append(-1)  # not selectable (skipped)

    # Cursor starts on the first non-active policy (after the divider)
    first_other = len(entries)

    for i, p in enumerate(policies):
        if i == active_idx:
            continue
        entries.append(_entry(p))
        menu_to_policy.append(i)

    menu = TerminalMenu(
        entries,
        title="\n  Select a policy (↑/↓ to move, Enter to select, q to cancel)\n",
        cursor_index=first_other,
        menu_cursor_style=("fg_green", "bold"),
        menu_highlight_style=("fg_green", "bold"),
        skip_empty_entries=True,
    )
    menu_idx = menu.show()
    if menu_idx is None:
        return None
    # Map back to original policy index; skip divider rows
    policy_idx = menu_to_policy[menu_idx]
    if policy_idx == -1:
        return None
    return policy_idx


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.pass_context
def policy(ctx: click.Context):
    """View and manage the active gateway policy."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@policy.command()
def current():
    """Show the currently active policy details."""
    console = Console()
    client = _make_client()

    try:
        info = client.get_current_policy()
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    name = info["policy"]
    class_ref = info["class_ref"]
    lines = [f"[bold green]{name}[/bold green]", f"[dim]{class_ref}[/dim]"]

    parts = []
    if info.get("enabled_by"):
        parts.append(f"by {info['enabled_by']}")
    if info.get("enabled_at"):
        parts.append(f"at {info['enabled_at']}")
    if parts:
        lines.append(f"[dim]Enabled {' '.join(parts)}[/dim]")

    if info.get("config"):
        lines.append("")
        lines.append(f"[bold]Config:[/bold] {json.dumps(info['config'], indent=2)}")

    console.print()
    console.print(Panel("\n".join(lines), title="Active Policy", border_style="green", padding=(1, 2)))


@policy.command("list")
@click.option("-v", "--verbose", is_flag=True, help="Show class refs and config params.")
def list_policies(verbose: bool):
    """List all available policies."""
    console = Console()
    client = _make_client()

    try:
        policies = client.list_policies()
        active = client.get_current_policy()
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    active_ref = active.get("class_ref", "")

    core = [p for p in policies if not _is_preset(p)]
    presets = [p for p in policies if _is_preset(p)]

    console.print()

    def _print_group(title: str, items: list[dict]) -> None:
        if not items:
            return
        console.print(f"  [bold]{title}[/bold]")
        for p in items:
            is_active = p["class_ref"] == active_ref
            marker = "[green]>[/green]" if is_active else " "
            name = _short_name(p["class_ref"])
            desc = _truncate(p.get("description", ""))
            # Pad the raw name before applying markup so alignment is correct
            padded_name = name.ljust(30)
            if is_active:
                padded_name = f"[green]{padded_name}[/green]"
            console.print(f"    {marker} [bold]{padded_name}[/bold] [dim]{desc}[/dim]")
            if verbose:
                console.print(f"      [dim]{p['class_ref']}[/dim]")
                if p.get("config_schema"):
                    params = ", ".join(p["config_schema"].keys())
                    console.print(f"      [cyan]config:[/cyan] {params}")
        console.print()

    _print_group("Policies", core)
    _print_group("Presets", presets)

    n_core = len(core)
    n_preset = len(presets)
    policy_word = "policy" if n_core == 1 else "policies"
    preset_word = "preset" if n_preset == 1 else "presets"
    console.print(f"  [dim]{n_core} {policy_word} + {n_preset} {preset_word}  |  [green]>[/green] = active[/dim]")
    console.print()


@policy.command()
@click.argument("name", required=False, shell_complete=_policy_completions)
def show(name: str | None):
    """Show details for a policy (defaults to active if NAME omitted).

    \b
    Examples:
      luthien policy show DeslopifyPolicy
      luthien policy show              # shows active policy
    """
    console = Console()
    client = _make_client()

    try:
        policies = client.list_policies()
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if name is None:
        try:
            active = client.get_current_policy()
        except GatewayError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)
        active_ref = active.get("class_ref", "")
        class_ref = active_ref
    else:
        # Best-effort: fetch active policy for the "(active)" label,
        # but don't fail if the gateway is unreachable.
        try:
            active = client.get_current_policy()
            active_ref = active.get("class_ref", "")
        except GatewayError:
            active_ref = ""
        class_ref = _resolve_class_ref(name, policies)
        if class_ref is None:
            console.print(f"[red]No policy found matching '{name}'[/red]")
            console.print("[dim]Run [bold]luthien policy list[/bold] to see available policies.[/dim]")
            raise SystemExit(1)

    p = next((p for p in policies if p["class_ref"] == class_ref), None)
    if p is None:
        console.print(f"[red]Active policy '{class_ref}' not found in policy list.[/red]")
        console.print("[dim]Run [bold]luthien policy list[/bold] to see available policies.[/dim]")
        raise SystemExit(1)

    body_parts = [f"[dim]{p['class_ref']}[/dim]"]

    if p.get("description"):
        body_parts.append("")
        body_parts.append(p["description"])

    if p.get("config_schema"):
        body_parts.append("")
        body_parts.append("[bold]Config parameters:[/bold]")
        for param_name, param_info in p["config_schema"].items():
            ptype = param_info.get("type", "any")
            default = param_info.get("default")
            required = param_info.get("required", False)
            line = f"  [cyan]{param_name}[/cyan]: {ptype}"
            if required:
                line += " [red](required)[/red]"
            elif default is not None:
                line += f" [dim](default: {default})[/dim]"
            body_parts.append(line)

    if p.get("example_config"):
        body_parts.append("")
        body_parts.append("[bold]Example:[/bold]")
        example_json = json.dumps(p["example_config"], indent=2)
        for line in example_json.split("\n"):
            body_parts.append(f"  {line}")

    is_active = class_ref == active_ref
    if not is_active:
        body_parts.append("")
        body_parts.append(f"[dim]Activate with:[/dim] luthien policy set {_short_name(class_ref)}")

    title = Text()
    title.append(p["name"], style="bold")
    if is_active:
        title.append(" (active)", style="green")

    console.print()
    console.print(Panel("\n".join(body_parts), title=title, padding=(1, 2)))


@policy.command("set")
@click.argument("name", required=False, shell_complete=_policy_completions)
@click.option("--config", "config_json", default=None, help="Policy config as JSON string.")
def set_policy(name: str | None, config_json: str | None):
    """Activate a policy on the gateway.

    NAME can be a short class name (e.g. DeslopifyPolicy) or full class ref.
    If omitted, shows an interactive picker.
    """
    console = Console()
    client = _make_client()

    config: dict = {}
    if config_json:
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON in --config: {e}[/red]")
            raise SystemExit(1)

    try:
        policies = client.list_policies()
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    # Interactive picker when no name given
    if name is None:
        if not sys.stdin.isatty():
            console.print("[red]No policy name given and stdin is not a terminal.[/red]")
            console.print("[dim]Usage: luthien policy set <name>[/dim]")
            raise SystemExit(1)

        try:
            active = client.get_current_policy()
        except GatewayError:
            active = {}
        active_ref = active.get("class_ref", "")

        idx = _interactive_pick(policies, active_ref)
        if idx is None:
            console.print("[dim]Cancelled.[/dim]")
            raise SystemExit(0)

        class_ref = policies[idx]["class_ref"]
    else:
        class_ref = _resolve_class_ref(name, policies)
        if class_ref is None:
            console.print(f"[red]No policy found matching '{name}'[/red]")
            console.print("[dim]Run [bold]luthien policy list[/bold] to see available policies.[/dim]")
            raise SystemExit(1)

    try:
        result = client.set_policy(class_ref, config)
    except GatewayError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)

    if result.get("success"):
        console.print(f"\n  [green]Active policy set to [bold]{_short_name(class_ref)}[/bold][/green]\n")
    else:
        console.print(f"[red]Failed: {result.get('error', 'unknown error')}[/red]")
        for hint in result.get("troubleshooting", []):
            console.print(f"  [yellow]- {hint}[/yellow]")
        raise SystemExit(1)
