"""Balance router (v2): balance by wallet id. Resolves wallet -> account -> API -> fetch balance."""
from typing import List, Dict, Any, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models import User, RefExchange
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIRepository
from app.repositories.account_repository import AccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.api_service import ApiService, NetworkError, ExchangeError

router = APIRouter(prefix="/balance", tags=["balance-v2"])

# API type priority: HMAC > ED25519 > RSA (lower number = higher priority)
API_TYPE_PRIORITY = {"HMAC": 0, "ED25519": 1, "RSA": 2}


def _select_best_api_per_uid(
    api_exchange_uid_list: List[Tuple[Any, str, int]],
) -> List[Tuple[Any, str]]:
    """
    Select the best API for each unique (exchange, uid) based on api_type priority.
    Input: list of (api, exchange_name, uid). Output: list of (api, exchange_name).
    """
    key_to_apis: Dict[tuple, List[Tuple[Any, str]]] = {}
    for api, exchange_name, uid in api_exchange_uid_list:
        key = (exchange_name, uid)
        if key not in key_to_apis:
            key_to_apis[key] = []
        key_to_apis[key].append((api, exchange_name))
    best = []
    for api_list in key_to_apis.values():
        api_list.sort(key=lambda t: API_TYPE_PRIORITY.get(t[0].api_type, 999))
        best.append(api_list[0])
    return best


class TokenBalance(BaseModel):
    """Token balance model."""
    token: str
    total: str
    frozen: str
    available: str


class WalletBalance(BaseModel):
    """Wallet balance model."""
    wallet: str  # SPOT, MARGIN, FUTURES, UNIFIED, FUNDING
    balance: List[TokenBalance]


class BalanceResponse(BaseModel):
    """Balance response model."""
    api_id: int
    name: str
    exchange: str
    wallets: List[WalletBalance]


def _get_balance_for_api(api: Any, exchange_name: str, api_service: ApiService) -> BalanceResponse:
    """Get balance for a single API and convert to response model."""
    balance_data = api_service.get_balance(
        exchange=exchange_name,
        api_key=api.api_key,
        api_secret=api.api_secret,
        passphrase=api.passphrase,
        api_type=api.api_type,
    )
    wallets = [
        WalletBalance(
            wallet=wallet["wallet"],
            balance=[
                TokenBalance(
                    token=token["token"],
                    total=token["total"],
                    frozen=token["frozen"],
                    available=token["available"],
                )
                for token in wallet["balance"]
            ],
        )
        for wallet in balance_data
    ]
    return BalanceResponse(
        api_id=api.id,
        name=api.name,
        exchange=exchange_name,
        wallets=wallets,
    )


@router.get("", response_model=List[BalanceResponse])
async def get_balances(
    id: Optional[str] = Query(None, description="Comma-separated list of wallet IDs (e.g., '1,2,4')"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get balances for wallets. Resolves each wallet to its API key, then fetches balance as v1.

    Accepts a comma-separated list of wallet IDs. Only wallets owned by the current user are used.
    Multiple wallets may share the same API; APIs are deduplicated before requesting balance.

    Example: /api/v2/balance?id=1,2,4
    """
    if not id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'id' is required (e.g., ?id=1,2,4)",
        )
    try:
        wallet_ids = [int(wid.strip()) for wid in id.split(",") if wid.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid wallet ID format. IDs must be integers separated by commas (e.g., '1,2,4')",
        )
    if not wallet_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one valid wallet ID is required",
        )

    wallet_repo = WalletRepository(db)
    api_repo = APIRepository(db)
    account_repo = AccountRepository(db)
    api_service = ApiService()

    # Resolve wallets -> account -> APIs (only wallets owned by user)
    api_exchange_uid_list: List[Tuple[Any, str, int]] = []
    for wallet_id in wallet_ids:
        wallet = wallet_repo.get_wallet_by_user_and_id(current_user.id, wallet_id)
        if not wallet:
            continue
        account = account_repo.get(wallet.acct_id)
        if not account or account.user_id != current_user.id:
            continue
        apis = api_repo.get_by_acct(wallet.acct_id)
        exchange = db.query(RefExchange).filter(RefExchange.id == apis[0].exch_id).first() if apis else None
        exchange_name = exchange.name if exchange else ""
        for api in apis:
            if exchange_name:
                api_exchange_uid_list.append((api, exchange_name, account.uid))

    deduplicated = _select_best_api_per_uid(api_exchange_uid_list)
    results = []
    for api, exchange_name in deduplicated:
        try:
            results.append(_get_balance_for_api(api, exchange_name, api_service))
        except NetworkError:
            continue
        except ExchangeError:
            continue

    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No valid wallets found for the provided IDs or all balance requests failed",
        )
    return results


@router.get("/{wallet_id}", response_model=BalanceResponse)
async def get_balance_by_wallet_id(
    wallet_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get balance for a single wallet. Resolves wallet -> API key -> fetch balance as v1.

    Verifies that the wallet belongs to the current user (via its API key) before fetching.
    """
    wallet_repo = WalletRepository(db)
    api_repo = APIRepository(db)
    account_repo = AccountRepository(db)
    wallet = wallet_repo.get_wallet_by_user_and_id(current_user.id, wallet_id)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wallet with ID {wallet_id} not found or does not belong to you",
        )
    account = account_repo.get(wallet.acct_id)
    if not account or account.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account for this wallet not found or does not belong to you",
        )
    apis = api_repo.get_by_acct(wallet.acct_id)
    if not apis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key found for this wallet's account",
        )
    # Pick best API by type priority
    apis_sorted = sorted(apis, key=lambda a: API_TYPE_PRIORITY.get(a.api_type, 999))
    api = apis_sorted[0]
    exchange = db.query(RefExchange).filter(RefExchange.id == api.exch_id).first()
    exchange_name = exchange.name if exchange else ""

    api_service = ApiService()
    try:
        return _get_balance_for_api(api, exchange_name, api_service)
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while fetching balance: {e.message}",
        )
    except ExchangeError as e:
        error_detail = f"Exchange error: {e.message}"
        if e.error_code is not None:
            error_detail += f" (Error code: {e.error_code})"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail,
        )
