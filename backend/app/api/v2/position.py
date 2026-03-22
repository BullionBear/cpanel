"""Position router (v2): fetch open futures positions by account ID."""
import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.middleware.auth import get_current_user
from app.models import User, RefExchange
from app.repositories.account_repository import AccountRepository
from app.repositories.api_repository import APIRepository
from app.services.api_service import APIService, ExchangeError, NetworkError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/position", tags=["position-v2"])

API_TYPE_PRIORITY = {"HMAC": 0, "ED25519": 1, "RSA": 2}


class PositionItem(BaseModel):
    symbol: str
    side: str
    size: str
    entry_price: str
    mark_price: str
    liq_price: str
    unrealized_pnl: str
    leverage: str
    margin_type: str
    notional: str
    update_time_ms: int


class PositionResponse(BaseModel):
    acct_id: int
    account_name: str
    exchange: str
    positions: List[PositionItem]


def _best_api_for_account(
    apis: list, api_type_priority: Dict[str, int] = API_TYPE_PRIORITY
) -> Any:
    if not apis:
        return None
    return sorted(apis, key=lambda a: api_type_priority.get(a.api_type, 999))[0]


def _fetch_positions(
    api: Any, exchange_name: str, acct_id: int, account_name: str, api_service: APIService
) -> PositionResponse:
    client = api_service.get_exchange_client_from_api_info(exchange_name, api)
    raw_positions = client.get_positions()
    items = [
        PositionItem(
            symbol=p["symbol"],
            side=p["side"],
            size=p["size"],
            entry_price=p["entry_price"],
            mark_price=p["mark_price"],
            liq_price=p["liq_price"],
            unrealized_pnl=p["unrealized_pnl"],
            leverage=p["leverage"],
            margin_type=p["margin_type"],
            notional=p["notional"],
            update_time_ms=p["update_time_ms"],
        )
        for p in raw_positions
    ]
    return PositionResponse(
        acct_id=acct_id,
        account_name=account_name,
        exchange=exchange_name,
        positions=items,
    )


@router.get("", response_model=List[PositionResponse])
async def get_positions(
    acct_id: Optional[str] = Query(
        None, description="Comma-separated account IDs (e.g., '1,2'). Omit to fetch all accounts."
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Fetch open futures/derivatives positions for the given accounts.

    Resolves each account to its best API key and calls the exchange.
    Only accounts owned by the current user are queried.
    """
    account_repo = AccountRepository(db)
    api_repo = APIRepository(db)
    api_service = APIService()

    if acct_id:
        try:
            acct_ids = [int(a.strip()) for a in acct_id.split(",") if a.strip()]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid acct_id format. Must be comma-separated integers.",
            )
    else:
        accounts_all = account_repo.query(user_id=current_user.id)
        acct_ids = [a.id for a in accounts_all]

    if not acct_ids:
        return []

    seen_uids: set = set()
    results: List[PositionResponse] = []

    for aid in acct_ids:
        account = account_repo.get_by_user_and_id(current_user.id, aid)
        if not account:
            continue

        uid_key = (account.exch_id, account.uid)
        if uid_key in seen_uids:
            continue
        seen_uids.add(uid_key)

        apis = api_repo.get_by_acct(account.id)
        best_api = _best_api_for_account(apis)
        if not best_api:
            continue

        exchange = db.query(RefExchange).filter(RefExchange.id == account.exch_id).first()
        exchange_name = (exchange.slug or exchange.name or "").upper() if exchange else ""
        if not exchange_name:
            continue

        try:
            resp = _fetch_positions(best_api, exchange_name, account.id, account.name, api_service)
            if resp.positions:
                results.append(resp)
        except (NetworkError, ExchangeError) as e:
            logger.warning("Failed to fetch positions for account %s: %s", account.id, e)
            continue

    return results
