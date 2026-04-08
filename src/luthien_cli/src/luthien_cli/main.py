"""CLI entry point."""

from importlib.metadata import version

import click


@click.group()
@click.version_option(version=version("luthien-cli"))
def cli():
    """Luthien -- manage and interact with luthien-proxy gateways."""


from luthien_cli.commands.agent_tutorial import agent_tutorial
from luthien_cli.commands.claude import claude
from luthien_cli.commands.config_cmd import config
from luthien_cli.commands.hackathon import hackathon
from luthien_cli.commands.logs import logs
from luthien_cli.commands.onboard import onboard
from luthien_cli.commands.policy import policy
from luthien_cli.commands.status import status
from luthien_cli.commands.up import down, up

cli.add_command(agent_tutorial)
cli.add_command(claude)
cli.add_command(config)
cli.add_command(down)
cli.add_command(hackathon)
cli.add_command(logs)
cli.add_command(onboard)
cli.add_command(policy)
cli.add_command(status)
cli.add_command(up)


if __name__ == "__main__":
    cli()
