"""CLI entry point."""

import click


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Luthien -- manage and interact with luthien-proxy gateways."""


from luthien_cli.commands.claude import claude
from luthien_cli.commands.status import status
from luthien_cli.commands.up import down, up

cli.add_command(claude)
cli.add_command(down)
cli.add_command(status)
cli.add_command(up)


if __name__ == "__main__":
    cli()
