"""CLI entry point."""

import click


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Luthien — manage and interact with luthien-proxy gateways."""


if __name__ == "__main__":
    cli()
