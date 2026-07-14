"""Scheduled asset-snapshot job, one per exchange.

The job snapshots every active account on its exchange. Accounts are isolated
from one another — each captures in its own session, and one account's failure
(e.g. a missing/expired key) is logged and skipped rather than aborting the
whole run. Per-exchange scoping keeps a slow or rate-limited venue from
blocking the others, exactly like the symbol jobs.
"""
import logging

from sqlalchemy import select

from app.connectors.registry import connectors
from app.core.db import get_session_factory
from app.models.account import Account
from app.services.accounts import AccountError
from app.services.snapshots import capture_snapshots

logger = logging.getLogger(__name__)


def make_snapshot_job(exchange: str):
    async def job() -> int:
        connector = connectors.get(exchange)
        factory = get_session_factory()

        async with factory() as db:
            account_ids = list(
                (
                    await db.execute(
                        select(Account.id).where(
                            Account.exchange == exchange.upper(),
                            Account.status == "active",
                        )
                    )
                ).scalars()
            )

        total = 0
        for account_id in account_ids:
            try:
                async with factory() as db:
                    account = await db.get(Account, account_id)
                    if account is None:
                        continue
                    total += await capture_snapshots(db, connector, account)
            except AccountError:
                # Account has no usable credential yet — nothing to snapshot.
                continue
            except Exception:
                logger.exception(
                    "Snapshot failed for %s account %s", exchange, account_id
                )
        return total

    return job
