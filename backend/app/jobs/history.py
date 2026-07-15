"""Scheduled history sync, one per exchange.

Every active account on the exchange gets its streams synced each run. Like
the snapshot job, accounts are isolated in their own sessions so one broken
credential never aborts the rest; within an account, stream failures are
already isolated by the service (recorded on the stream's cursor row). A
freshly registered account has no cursors yet, so its first run backfills the
past three days before incremental sync takes over.
"""
import logging

from sqlalchemy import select

from app.connectors.base import Capability
from app.connectors.registry import connectors
from app.core.db import get_session_factory
from app.models.account import Account
from app.services.accounts import AccountError
from app.services.history import sync_account_history

logger = logging.getLogger(__name__)


def make_history_sync_job(exchange: str):
    async def job() -> int:
        connector = connectors.get(exchange)
        if not (
            connector.supports(Capability.TRADE_HISTORY)
            or connector.supports(Capability.ORDER_HISTORY)
        ):
            return 0
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
                    total += await sync_account_history(db, connector, account)
            except AccountError:
                # Account has no usable credential yet — nothing to sync.
                continue
            except Exception:
                logger.exception(
                    "History sync failed for %s account %s", exchange, account_id
                )
        return total

    return job
