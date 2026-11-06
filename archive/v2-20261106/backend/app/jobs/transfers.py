"""Transfer progress tracking.

One global job (transfers span two exchanges, so per-exchange scoping does
not fit): every unfinished transfer is advanced — waiting steps poll the
venue once per tick — in its own session so one broken transfer never blocks
the rest, then transfers with no progress past the stuck threshold are
flagged for the operator.
"""
import logging

from sqlalchemy import select

from app.connectors.registry import connectors
from app.core.db import get_session_factory
from app.models.transfer import Transfer
from app.services.transfers import advance_transfer, flag_stuck_transfers, get_transfer

logger = logging.getLogger(__name__)


def make_transfer_progress_job():
    async def job() -> int:
        factory = get_session_factory()

        async with factory() as db:
            transfer_ids = list(
                (
                    await db.execute(
                        select(Transfer.id).where(Transfer.status == "in_progress")
                    )
                ).scalars()
            )

        progressed = 0
        for transfer_id in transfer_ids:
            try:
                async with factory() as db:
                    transfer = await get_transfer(db, transfer_id)
                    await advance_transfer(db, transfer, connectors.get)
                    progressed += 1
            except Exception:
                logger.exception("Advancing transfer %s failed", transfer_id)

        async with factory() as db:
            flagged = await flag_stuck_transfers(db)
            if flagged:
                logger.warning("%d transfer(s) flagged stuck", flagged)
        return progressed

    return job
