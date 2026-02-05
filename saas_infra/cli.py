#!/usr/bin/env python3
"""CLI for managing luthien-proxy SaaS instances on Railway."""

import json
import sys

import click

from .models import InstanceStatus
from .provisioner import Provisioner, ProvisioningConfig
from .railway_client import RailwayAPIError, RailwayAuthError, RailwayClient
from .utils import (
    NameValidationError,
    calculate_deletion_date,
    format_datetime,
    format_deletion_countdown,
    validate_instance_name,
)


def get_client() -> RailwayClient:
    """Get Railway client, handling auth errors gracefully."""
    try:
        return RailwayClient.from_env()
    except RailwayAuthError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def output_json(data: dict) -> None:
    """Output data as JSON."""
    click.echo(json.dumps(data, indent=2, default=str))


def output_error(message: str, json_mode: bool = False) -> None:
    """Output an error message."""
    if json_mode:
        output_json({"error": message})
    else:
        click.echo(f"Error: {message}", err=True)


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Manage luthien-proxy instances on Railway.

    Requires RAILWAY_TOKEN environment variable.
    Optionally set RAILWAY_TEAM_ID for team-scoped operations.
    """
    pass


@cli.command()
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option(
    "--repo",
    default=None,
    help="GitHub repo in owner/repo format (default: LuthienResearch/luthien-proxy)",
)
def create(
    name: str,
    json_output: bool,
    repo: str | None,
):
    """Create a new luthien-proxy instance.

    NAME should be lowercase alphanumeric with hyphens (e.g., 'my-tenant').
    """
    # Validate name early
    try:
        validate_instance_name(name)
    except NameValidationError as e:
        output_error(str(e), json_output)
        sys.exit(1)

    client = get_client()

    config = ProvisioningConfig()
    if repo:
        config.repo = repo

    provisioner = Provisioner(client, config)

    if not json_output:
        click.echo(f"Creating instance '{name}'...")

    result = provisioner.create_instance(name)

    if not result.success or result.instance is None:
        output_error(result.error or "Unknown error", json_output)
        sys.exit(1)

    instance = result.instance
    if json_output:
        output_json(
            {
                "name": instance.name,
                "project_id": instance.project_id,
                "url": instance.url,
                "status": instance.status.value,
                "proxy_api_key": result.proxy_api_key,
                "admin_api_key": result.admin_api_key,
            }
        )
    else:
        click.echo(f"\nInstance '{name}' created successfully!")
        click.echo(f"URL: {instance.url}")
        click.echo(result.credentials_message)


@cli.command("list")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def list_instances(json_output: bool):
    """List all luthien-proxy instances."""
    client = get_client()

    try:
        instances = client.list_luthien_instances()
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)

    if json_output:
        output_json(
            {
                "instances": [
                    {
                        "name": inst.name,
                        "project_id": inst.project_id,
                        "status": inst.status.value,
                        "url": inst.url,
                        "created_at": inst.created_at.isoformat() if inst.created_at else None,
                        "deletion_scheduled_at": (
                            inst.deletion_scheduled_at.isoformat() if inst.deletion_scheduled_at else None
                        ),
                    }
                    for inst in instances
                ]
            }
        )
        return

    if not instances:
        click.echo("No instances found.")
        return

    click.echo(f"{'NAME':<20} {'STATUS':<20} {'CREATED':<22}")
    click.echo("-" * 62)

    for inst in instances:
        status_str = inst.status.value
        if inst.status == InstanceStatus.DELETION_SCHEDULED and inst.deletion_scheduled_at:
            countdown = format_deletion_countdown(inst.deletion_scheduled_at)
            status_str = f"deleting in {countdown}"

        created = format_datetime(inst.created_at)[:19] if inst.created_at else "unknown"

        click.echo(f"{inst.name:<20} {status_str:<20} {created:<22}")


@cli.command()
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def status(name: str, json_output: bool):
    """Show detailed status of an instance."""
    client = get_client()

    try:
        instance = client.get_instance(name)
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)

    if not instance:
        output_error(f"Instance '{name}' not found", json_output)
        sys.exit(1)

    if json_output:
        output_json(
            {
                "name": instance.name,
                "project_id": instance.project_id,
                "status": instance.status.value,
                "url": instance.url,
                "created_at": instance.created_at.isoformat() if instance.created_at else None,
                "deletion_scheduled_at": (
                    instance.deletion_scheduled_at.isoformat() if instance.deletion_scheduled_at else None
                ),
                "services": {
                    name: {
                        "id": svc.id,
                        "status": svc.status.value,
                        "url": svc.url,
                    }
                    for name, svc in instance.services.items()
                },
            }
        )
        return

    click.echo(f"Instance: {instance.name}")
    click.echo(f"Status: {instance.status.value}")
    click.echo(f"URL: {instance.url or 'not available'}")
    click.echo(f"Created: {format_datetime(instance.created_at)}")

    if instance.status == InstanceStatus.DELETION_SCHEDULED and instance.deletion_scheduled_at:
        countdown = format_deletion_countdown(instance.deletion_scheduled_at)
        click.echo(f"Deletion scheduled: {format_datetime(instance.deletion_scheduled_at)} ({countdown} remaining)")

    click.echo("\nServices:")
    for svc_name, svc in instance.services.items():
        url_str = f" ({svc.url})" if svc.url else ""
        click.echo(f"  - {svc_name}: {svc.status.value}{url_str}")


@cli.command()
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.option("--force", is_flag=True, help="Delete immediately without grace period")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def delete(name: str, json_output: bool, force: bool, yes: bool):
    """Delete an instance (with 7-day grace period unless --force)."""
    client = get_client()

    # Check instance exists
    try:
        instance = client.get_instance(name)
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)

    if not instance:
        output_error(f"Instance '{name}' not found", json_output)
        sys.exit(1)

    # Confirm deletion
    if not yes and not json_output:
        if force:
            message = f"This will PERMANENTLY delete instance '{name}' and all its data. Continue?"
        else:
            message = f"This will schedule instance '{name}' for deletion in 7 days. Continue?"

        if not click.confirm(message):
            click.echo("Cancelled.")
            return

    try:
        if force:
            client.force_delete_instance(name)
            if json_output:
                output_json({"deleted": name, "immediate": True})
            else:
                click.echo(f"Instance '{name}' has been permanently deleted.")
        else:
            deletion_date = calculate_deletion_date()
            client.schedule_deletion(name, deletion_date)
            if json_output:
                output_json(
                    {
                        "scheduled_for_deletion": name,
                        "deletion_date": deletion_date.isoformat(),
                    }
                )
            else:
                click.echo(f"Instance '{name}' scheduled for deletion on {format_datetime(deletion_date)}.")
                click.echo("Use 'cancel-delete' to cancel, or '--force' to delete immediately.")

    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)


@cli.command("cancel-delete")
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def cancel_delete(name: str, json_output: bool):
    """Cancel scheduled deletion of an instance."""
    client = get_client()

    try:
        instance = client.get_instance(name)
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)

    if not instance:
        output_error(f"Instance '{name}' not found", json_output)
        sys.exit(1)

    if instance.status != InstanceStatus.DELETION_SCHEDULED:
        output_error(f"Instance '{name}' is not scheduled for deletion", json_output)
        sys.exit(1)

    try:
        client.cancel_deletion(name)
        if json_output:
            output_json({"cancelled": name})
        else:
            click.echo(f"Deletion cancelled for instance '{name}'.")
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)


@cli.command()
@click.argument("name")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def redeploy(name: str, json_output: bool):
    """Trigger redeployment of an instance from latest main branch."""
    client = get_client()

    try:
        instance = client.get_instance(name)
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)

    if not instance:
        output_error(f"Instance '{name}' not found", json_output)
        sys.exit(1)

    gateway = instance.services.get("gateway")
    if not gateway:
        output_error(f"Gateway service not found for instance '{name}'", json_output)
        sys.exit(1)

    # Get environment ID from project
    project = client.get_project(instance.project_id)
    if not project:
        output_error(f"Could not fetch project details for '{name}'", json_output)
        sys.exit(1)

    env_edges = project.get("environments", {}).get("edges", [])
    if not env_edges:
        output_error(f"No environment found for instance '{name}'", json_output)
        sys.exit(1)
    environment_id = env_edges[0]["node"]["id"]

    try:
        client.trigger_deployment(gateway.id, environment_id)
        if json_output:
            output_json({"instance": name, "redeployed": True})
        else:
            click.echo(f"Redeployment triggered for instance '{name}'.")
    except RailwayAPIError as e:
        output_error(str(e), json_output)
        sys.exit(1)


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def whoami(json_output: bool):
    """Show current Railway user/token info."""
    client = get_client()

    try:
        user = client.get_current_user()
        if json_output:
            output_json(user)
        else:
            click.echo(f"Name: {user.get('name', 'N/A')}")
            click.echo(f"Email: {user.get('email', 'N/A')}")

            teams = user.get("teams", {}).get("edges", [])
            if teams:
                click.echo("\nTeams:")
                for edge in teams:
                    team = edge["node"]
                    click.echo(f"  - {team['name']} (ID: {team['id']})")
    except RailwayAPIError:
        # Likely a team token - can't query 'me' but can list projects
        try:
            projects = client.list_projects()
            if json_output:
                output_json({"token_type": "team", "accessible_projects": len(projects)})
            else:
                click.echo("Token type: Team token (cannot query user info)")
                click.echo(f"Accessible projects: {len(projects)}")
                if projects:
                    click.echo("\nProjects:")
                    for proj in projects[:5]:  # Show first 5
                        click.echo(f"  - {proj['name']}")
                    if len(projects) > 5:
                        click.echo(f"  ... and {len(projects) - 5} more")
        except RailwayAPIError as e:
            output_error(f"Token validation failed: {e}", json_output)
            sys.exit(1)


if __name__ == "__main__":
    cli()
