"""Debounced balance/position refresh + SSE publish.

Callers that observe balance-affecting activity (fills, deposits, transfers,
manual refresh) schedule a refresh. Multiple schedules for the same account
coalesce into one venue capture after ``debounce_seconds``. After a successful
``capture_snapshots``, a ``balance.updated`` event is published so the UI can
re-GET holdings from the database. Failures publish ``balance.refresh_failed``
so the UI is not left waiting forever on a silent error.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.base import Capability, ExchangeConnector
from app.connectors.registry import connectors
from app.connectors.types import Balance, Position
from app.core.events import (
    BalanceRefreshFailedEvent,
    BalanceUpdatedEvent,
    event_hub,
)
from app.models.account import Account
from app.services.accounts import AccountError
from app.services.snapshots import capture_snapshots, latest_batch

logger = logging.getLogger(__name__)

__all__ = [
    "configure",
    "shutdown",
    "schedule_balance_refresh",
    "capture_and_publish",
    "publish_balance_updated",
    "publish_balance_refresh_failed",
    "DEFAULT_DEBOUNCE_SECONDS",
]

DEFAULT_DEBOUNCE_SECONDS = 3.0

_session_factory: async_sessionmaker[AsyncSession] | None = None
_pending: dict[int, asyncio.Task] = {}
_reasons: dict[int, str] = {}
_lock = asyncio.Lock()


def configure(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = session_factory


async def shutdown() -> None:
    """Cancel pending debounced refreshes (app lifespan teardown)."""
    async with _lock:
        tasks = list(_pending.values())
        _pending.clear()
        _reasons.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def publish_balance_updated(
    account: Account,
    reason: str,
    taken_at: datetime | None,
) -> None:
    event_hub.publish(
        BalanceUpdatedEvent(
            account_id=account.id,
            exchange=account.exchange,
            label=account.label,
            reason=reason,
            taken_at=taken_at,
        )
    )


def publish_balance_refresh_failed(
    account: Account,
    reason: str,
    error: str,
) -> None:
    event_hub.publish(
        BalanceRefreshFailedEvent(
            account_id=account.id,
            exchange=account.exchange,
            label=account.label,
            reason=reason,
            error=error[:500],
        )
    )


async def capture_and_publish(
    db: AsyncSession,
    connector: ExchangeConnector,
    account: Account,
    reason: str,
    *,
    balances: list[Balance] | None = None,
    positions: list[Position] | None = None,
) -> int:
    """Capture a snapshot batch and notify SSE subscribers. Returns rows written."""
    written = await capture_snapshots(
        db, connector, account, balances=balances, positions=positions
    )
    batch = await latest_batch(db, account.id)
    taken_at = batch[1] if batch is not None else None
    publish_balance_updated(account, reason, taken_at)
    return written


def schedule_balance_refresh(
    account_id: int,
    reason: str,
    *,
    debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
) -> None:
    """Coalesce refresh requests for ``account_id`` into one delayed capture."""
    if _session_factory is None:
        logger.warning(
            "schedule_balance_refresh(%s, %s) ignored — not configured",
            account_id,
            reason,
        )
        return

    _reasons[account_id] = reason

    async def _debounced() -> None:
        try:
            await asyncio.sleep(debounce_seconds)
            async with _lock:
                _pending.pop(account_id, None)
                fire_reason = _reasons.pop(account_id, reason)
            await _run_scheduled(account_id, fire_reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Debounced balance refresh failed for account %s (%s)",
                account_id,
                reason,
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "schedule_balance_refresh(%s) ignored — no running event loop",
            account_id,
        )
        return

    existing = _pending.get(account_id)
    if existing is not None and not existing.done():
        existing.cancel()
    _pending[account_id] = loop.create_task(
        _debounced(), name=f"balance_refresh:{account_id}"
    )


async def _run_scheduled(account_id: int, reason: str) -> None:
    assert _session_factory is not None
    async with _session_factory() as db:
        account = await db.get(Account, account_id)
        if account is None or account.status == "removed":
            return
        try:
            connector = connectors.get(account.exchange)
        except KeyError:
            logger.warning(
                "No connector for %s; skip balance refresh", account.exchange
            )
            return
        if not (
            connector.supports(Capability.BALANCES)
            or connector.supports(Capability.POSITIONS)
        ):
            return
        try:
            await capture_and_publish(db, connector, account, reason)
        except AccountError as exc:
            logger.info(
                "Balance refresh skipped for account %s — no usable credential",
                account_id,
            )
            publish_balance_refresh_failed(account, reason, str(exc))
        except Exception as exc:
            logger.exception(
                "Balance refresh failed for account %s (%s)",
                account_id,
                reason,
            )
            publish_balance_refresh_failed(account, reason, str(exc))
