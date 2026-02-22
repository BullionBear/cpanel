"""Balance router (v2): balance by wallet id. Resolves wallet -> API -> fetch balance as v1."""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIArchivedRepository
from app.repositories.wallet_repository import WalletArchivedRepository
from app.services.api_service import ApiService, NetworkError, ExchangeError

router = APIRouter(prefix="/balance", tags=["balance-v2"])

# API type priority: HMAC > ED25519 > RSA (lower number = higher priority)
API_TYPE_PRIORITY = {"HMAC": 0, "ED25519": 1, "RSA": 2}


def _select_best_api_per_uid(apis: List[Any]) -> List[Any]:
    """
    Select the best API for each unique UID based on api_type priority.
    Priority: HMAC > ED25519 > RSA
    """
    uid_to_apis: Dict[tuple, List[Any]] = {}
    for api in apis:
        key = (api.exchange, api.uid)
        if key not in uid_to_apis:
            uid_to_apis[key] = []
        uid_to_apis[key].append(api)
    best_apis = []
    for key, api_list in uid_to_apis.items():
        api_list.sort(key=lambda a: API_TYPE_PRIORITY.get(a.api_type, 999))
        best_apis.append(api_list[0])
    return best_apis


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


def _get_balance_for_api(api: Any, api_service: ApiService) -> BalanceResponse:
    """Get balance for a single API and convert to response model (same as v1)."""
    balance_data = api_service.get_balance(
        exchange=api.exchange,  # type: ignore
        api_key=api.api_key,
        api_secret=api.api_secret,
        passphrase=api.passphrase,
        api_type=api.api_type,  # type: ignore
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
        exchange=api.exchange,
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

    wallet_repo = WalletArchivedRepository(db)
    api_repo = APIArchivedRepository(db)
    api_service = ApiService()

    # Resolve wallets to APIs (only wallets owned by user, with valid api_id)
    valid_apis = []
    for wallet_id in wallet_ids:
        wallet = wallet_repo.get_wallet_by_user_and_id(current_user.id, wallet_id)
        if not wallet or wallet.api_id is None:
            continue
        api = api_repo.get(wallet.api_id)
        if api:
            valid_apis.append(api)

    deduplicated_apis = _select_best_api_per_uid(valid_apis)
    results = []
    for api in deduplicated_apis:
        try:
            results.append(_get_balance_for_api(api, api_service))
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
    wallet_repo = WalletArchivedRepository(db)
    api_repo = APIArchivedRepository(db)
    wallet = wallet_repo.get_wallet_by_user_and_id(current_user.id, wallet_id)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wallet with ID {wallet_id} not found or does not belong to you",
        )
    if wallet.api_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet has no associated API key",
        )
    api = api_repo.get(wallet.api_id)
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key for this wallet not found",
        )

    api_service = ApiService()
    try:
        return _get_balance_for_api(api, api_service)
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
