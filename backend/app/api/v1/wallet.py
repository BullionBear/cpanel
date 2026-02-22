"""Wallet router."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db.session import get_db
from app.models import User
from app.middleware.auth import get_current_user
from app.repositories.wallet_repository import WalletArchivedRepository
from app.repositories.api_repository import APIArchivedRepository

router = APIRouter(prefix="/wallet", tags=["wallets"])

# Exchange -> allowed wallet type slugs (ref_wallet.slug). Blocks e.g. Bybit SPOT.
EXCHANGE_ALLOWED_WALLET_SLUGS: dict[str, list[str]] = {
    "BINANCE": ["SPOT", "UMARGIN", "CMARGIN", "LEVERAGE"],
    "BYBIT": ["UNIFIED"],
}


class WalletCreate(BaseModel):
    """Wallet creation request model."""
    name: str
    api_key_id: int
    wallet_type_id: int


class WalletResponse(BaseModel):
    """Wallet response model."""
    id: int
    name: str
    api_id: int | None
    wallet_type_id: int
    is_active: bool

    class Config:
        from_attributes = True


class WalletTypeResponse(BaseModel):
    """Ref wallet type (for dropdowns)."""
    id: int
    name: str
    slug: str


def _is_wallet_type_allowed_for_exchange(exchange: str, wallet_slug: str) -> bool:
    """True if this wallet type (slug) is allowed for the exchange."""
    allowed = EXCHANGE_ALLOWED_WALLET_SLUGS.get((exchange or "").upper(), [])
    return (wallet_slug or "").upper() in [s.upper() for s in allowed]


@router.post("", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
async def create_wallet(
    body: WalletCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Create a new wallet with name, API key ID, and wallet type ID.
    The API key must belong to the current user.
    """
    api_repo = APIArchivedRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, body.api_key_id)
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key with ID {body.api_key_id} not found or does not belong to you",
        )
    wallet_repo = WalletArchivedRepository(db)
    ref_wallet = wallet_repo.get_ref_wallet(body.wallet_type_id)
    if not ref_wallet:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wallet type with ID {body.wallet_type_id} not found",
        )
    if not _is_wallet_type_allowed_for_exchange(api.exchange, ref_wallet.slug):
        allowed = EXCHANGE_ALLOWED_WALLET_SLUGS.get((api.exchange or "").upper(), [])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wallet type '{ref_wallet.slug}' is not allowed for exchange {api.exchange}. Allowed: {allowed}",
        )
    wallet = wallet_repo.create_wallet(
        name=body.name,
        wallet_type_id=body.wallet_type_id,
        api_id=body.api_key_id,
    )
    return WalletResponse(
        id=wallet.id,
        name=wallet.name,
        api_id=wallet.api_id,
        wallet_type_id=wallet.wallet_type_id,
        is_active=wallet.is_active,
    )


@router.get("/types", response_model=List[WalletTypeResponse])
async def get_wallet_types(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    List all wallet types (ref_wallet) for use in create-wallet forms.
    Declared before /{wallet_id} so GET /wallet/types is not matched as wallet_id='types'.
    """
    wallet_repo = WalletArchivedRepository(db)
    ref_wallets = wallet_repo.query_ref_wallets()
    return [
        WalletTypeResponse(id=w.id, name=w.name, slug=w.slug)
        for w in ref_wallets
    ]


@router.get("", response_model=List[WalletResponse])
async def get_wallets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get all wallets for the current user (wallets linked to the user's API keys).
    Only returns wallets whose exchange allows that wallet type (e.g. Bybit SPOT is excluded).
    """
    wallet_repo = WalletArchivedRepository(db)
    api_repo = APIArchivedRepository(db)
    wallets = wallet_repo.query_wallets_by_user_id(current_user.id)
    result = []
    for w in wallets:
        if w.api_id is None:
            continue
        api = api_repo.get(w.api_id)
        if not api:
            continue
        ref_wallet = wallet_repo.get_ref_wallet(w.wallet_type_id)
        if not ref_wallet or not _is_wallet_type_allowed_for_exchange(api.exchange, ref_wallet.slug):
            continue
        result.append(
            WalletResponse(
                id=w.id,
                name=w.name,
                api_id=w.api_id,
                wallet_type_id=w.wallet_type_id,
                is_active=w.is_active,
            )
        )
    return result


@router.delete("/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wallet(
    wallet_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Delete a wallet by ID.
    The wallet must be linked to an API key owned by the current user.
    """
    wallet_repo = WalletArchivedRepository(db)
    deleted = wallet_repo.delete_wallet_by_user_and_id(current_user.id, wallet_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Wallet with ID {wallet_id} not found or does not belong to you",
        )
    return None
