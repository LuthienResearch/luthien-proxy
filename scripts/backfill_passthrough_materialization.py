"""Drain the passthrough materialization backfill from configured storage."""

from __future__ import annotations

import asyncio
import logging

import click

from luthien_proxy.passthrough_materialize.backfill import drain_passthrough_backfill
from luthien_proxy.settings import get_settings
from luthien_proxy.telemetry import configure_logging
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


async def _run_backfill() -> None:
    settings = get_settings()
    if not settings.database_url:
        raise click.ClickException("DATABASE_URL must be configured")
    db_pool = DatabasePool(settings.database_url)
    try:
        await db_pool.get_pool()
        totals = await drain_passthrough_backfill(
            db_pool,
            limit=settings.passthrough_materialize_batch_size,
        )
    finally:
        await db_pool.close()
    logger.info(
        "Passthrough backfill complete: materialized=%d already_materialized=%d skipped_ineligible=%d failed=%d",
        totals.materialized,
        totals.already_materialized,
        totals.skipped_ineligible,
        totals.failed,
    )


@click.command()
def main() -> None:
    """Drain configured passthrough materialization backfill."""
    asyncio.run(_run_backfill())


if __name__ == "__main__":
    configure_logging()
    main()
