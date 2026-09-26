"""One-shot bounded backfill for passthrough materialization."""

from __future__ import annotations

import logging

from luthien_proxy.passthrough_materialize.materialize_types import ReconcileStats
from luthien_proxy.passthrough_materialize.reconcile import reconcile_passthrough
from luthien_proxy.utils.db import DatabasePool

logger = logging.getLogger(__name__)


async def drain_passthrough_backfill(db_pool: DatabasePool, *, limit: int) -> ReconcileStats:
    """Run bounded sweeps until a sweep adds no new materialized transactions."""
    totals = ReconcileStats()
    while True:
        sweep = await reconcile_passthrough(db_pool, limit=limit)
        totals = ReconcileStats(
            materialized=totals.materialized + sweep.materialized,
            already_materialized=totals.already_materialized + sweep.already_materialized,
            skipped_ineligible=totals.skipped_ineligible + sweep.skipped_ineligible,
            failed=totals.failed + sweep.failed,
        )
        logger.info(
            "Passthrough backfill progress: materialized=%d already_materialized=%d skipped_ineligible=%d failed=%d",
            totals.materialized,
            totals.already_materialized,
            totals.skipped_ineligible,
            totals.failed,
        )
        if sweep.materialized == 0:
            return totals


__all__ = ["drain_passthrough_backfill"]
