"""Scheduled open-order poller, one per exchange.

Polling is the source of truth for order state (ARCHITECTURE.md "Order
Management"). To avoid spending the rate budget on idle accounts, only accounts
that currently have a local open order are polled; each is synced in its own
session so one account's failure never aborts the run. (Discovering orders
placed entirely outside the panel is left to reconciliation in Phase 8.)
"""
import logging

from sqlalchemy import distinct, select

from app.connectors.base import Capability
from app.connectors.registry import connectors
from app.core.db import get_session_factory
from app.models.account import Account
from app.models.order import Order
from app.services.accounts import AccountError
from app.services.orders import OPEN_STATES, sync_open_orders

logger = logging.getLogger(__name__)


def make_open_order_poll_job(exchange: str):
    async def job() -> int:
        connector = connectors.get(exchange)
        if not connector.supports(Capability.ORDERS):
            return 0
        factory = get_session_factory()

        async with factory() as db:
            account_ids = list(
                (
                    await db.execute(
                        select(distinct(Order.account_id))
                        .join(Account, Account.id == Order.account_id)
                        .where(
                            Account.exchange == exchange.upper(),
                            Account.status == "active",
                            Order.status.in_(OPEN_STATES),
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
                    total += await sync_open_orders(db, connector, account)
            except AccountError:
                continue
            except Exception:
                logger.exception(
                    "Open-order sync failed for %s account %s", exchange, account_id
                )
        return total

    return job
