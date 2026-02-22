"""Account router (v2): accounts and API keys under /account."""
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
import logging
import random

from app.db.session import get_db
from app.models import User, RefExchange, RefWallet
from app.middleware.auth import get_current_user
from app.repositories.api_repository import APIRepository
from app.repositories.account_repository import AccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.api_service import ApiService, NetworkError, ExchangeError

router = APIRouter(prefix="/account", tags=["account-v2"])

# --- API key models ---
class APIKeyCreate(BaseModel):
    """API key creation request model."""
    exch_id: int
    api_type: str = "HMAC"
    api_key: str
    api_secret: str
    passphrase: Optional[str] = None
    parent_uid: Optional[int] = None

class APIKeyResponse(BaseModel):
    """API key response model."""
    id: int
    user_id: int
    exchange: str
    uid: int
    name: str
    api_type: str
    api_key: str
    api_secret: str
    passphrase: str = ""
    parent_uid: int
    created_at: str

    class Config:
        from_attributes = True

class RenameAPIKeyRequest(BaseModel):
    """Request body for renaming an API key."""
    id: int
    name: str

# --- Account models ---
class AccountResponse(BaseModel):
    """Account response model."""
    id: int
    uid: int
    exch_id: int
    exchange: str
    name: str
    parent_uid: int

    class Config:
        from_attributes = True

class RenameAccountRequest(BaseModel):
    """Request body for renaming an account."""
    id: int
    name: str

# --- Wallet models ---
class WalletCreateRequest(BaseModel):
    """Request body for creating a wallet under an account."""
    acct_id: int
    category_id: int  # ref_wallet id (wallet type)
    name: Optional[str] = None  # optional; default: "{account.name}_{category.slug}"

class RenameWalletRequest(BaseModel):
    """Request body for renaming a wallet."""
    id: int
    name: str

class WalletResponse(BaseModel):
    """Wallet response model."""
    id: int
    name: str
    category_id: int
    acct_id: int
    user_id: int
    is_active: bool

    class Config:
        from_attributes = True


# --- API key endpoints ---
@router.get("/apikey", response_model=List[APIKeyResponse])
async def get_api_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    exch_id: Optional[int] = Query(None, description="Filter by exchange ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Get all API keys for the current user. Query params: exch_id (optional), skip, limit."""
    api_repo = APIRepository(db)
    account_repo = AccountRepository(db)
    apis = api_repo.query(user_id=current_user.id, exch_id=exch_id, skip=skip, limit=limit)
    result = []
    for api in apis:
        account = account_repo.get(api.acct_id)
        if not account or account.user_id != current_user.id:
            continue
        exchange = db.query(RefExchange).filter(RefExchange.id == api.exch_id).first()
        exchange_name = exchange.name if exchange else ""
        parent_uid_value = account.parent_uid if account.parent_uid is not None else account.uid
        result.append(
            APIKeyResponse(
                id=api.id,
                user_id=api.user_id,
                exchange=exchange_name,
                uid=account.uid,
                name=api.name,
                api_type=api.api_type,
                api_key=api.api_key,
                api_secret=api.api_secret,
                passphrase=api.passphrase or "",
                parent_uid=parent_uid_value,
                created_at=api.created_at.isoformat() if api.created_at else "",
            )
        )
    return result


@router.delete("/apikey/{api_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    api_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an API key by ID. Verifies ownership."""
    api_repo = APIRepository(db)
    deleted = api_repo.delete_by_user_and_id(current_user.id, api_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or does not belong to you",
        )
    return None


@router.put("/apikey/rename", response_model=APIKeyResponse)
async def rename_apikey(
    body: RenameAPIKeyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename an API key by ID."""
    api_repo = APIRepository(db)
    account_repo = AccountRepository(db)
    api = api_repo.get_by_user_and_id(current_user.id, body.id)
    if not api:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or does not belong to you",
        )
    api.name = body.name
    api_repo.update(api)
    account = account_repo.get(api.acct_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    exchange = db.query(RefExchange).filter(RefExchange.id == api.exch_id).first()
    exchange_name = exchange.name if exchange else ""
    parent_uid_value = account.parent_uid if account.parent_uid is not None else account.uid
    return APIKeyResponse(
        id=api.id,
        user_id=api.user_id,
        exchange=exchange_name,
        uid=account.uid,
        name=api.name,
        api_type=api.api_type,
        api_key=api.api_key,
        api_secret=api.api_secret,
        passphrase=api.passphrase or "",
        parent_uid=parent_uid_value,
        created_at=api.created_at.isoformat() if api.created_at else "",
    )


@router.post("/apikey", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def register_api_key(
    api_data: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register a new API key. Verifies credentials with exchange and fetches account UID."""
    exchange = db.query(RefExchange).filter(RefExchange.id == api_data.exch_id).first()
    if not exchange:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Exchange with ID {api_data.exch_id} not found",
        )
    api_service = ApiService()
    try:
        api_service.verify_api(
            exchange=exchange.name.upper(),
            api_key=api_data.api_key,
            api_secret=api_data.api_secret,
            passphrase=api_data.passphrase,
        )
    except NetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Network error while verifying API: {e.message}",
        )
    except ExchangeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Exchange error while verifying API: {e.message}",
        )
    uid = api_service.get_account_uid(
        exchange=exchange.name.upper(),
        api_key=api_data.api_key,
        api_secret=api_data.api_secret,
        passphrase=api_data.passphrase,
    )

    account_repo = AccountRepository(db)
    account = account_repo.get_by_user_and_uid(current_user.id, api_data.exch_id, uid)
    if account is None:
        parent_uid_for_account = api_data.parent_uid if api_data.parent_uid is not None else uid
        acct_name = f"acct_{exchange.name.upper()}_{uid}_{random.randint(1000, 9999)}"
        account = account_repo.create(
            user_id=current_user.id,
            exch_id=api_data.exch_id,
            uid=uid,
            name=acct_name,
            parent_uid=parent_uid_for_account,
        )
        logging.info(f"Created account {account.id} for user {current_user.id} and exchange {exchange.name}")

    if api_data.parent_uid is not None:
        parent_uid = api_data.parent_uid
        if account_repo.get_by_user_and_uid(current_user.id, api_data.exch_id, parent_uid) is None:
            parent_acct_name = f"acct_{exchange.name.upper()}_{parent_uid}_{random.randint(1000, 9999)}"
            account_repo.create(
                user_id=current_user.id,
                exch_id=api_data.exch_id,
                uid=parent_uid,
                name=parent_acct_name,
                parent_uid=parent_uid,
            )
            logging.info(f"Created parent account for user {current_user.id}, exchange {exchange.name}, parent_uid={parent_uid}")

    api_repo = APIRepository(db)
    result = api_repo.create(
        acct_id=account.id,
        user_id=current_user.id,
        exch_id=exchange.id,
        name=f"api_{exchange.name.upper()}_{uid}_{random.randint(1000, 9999)}",
        api_type=api_data.api_type,
        api_key=api_data.api_key,
        api_secret=api_data.api_secret,
        passphrase=api_data.passphrase,
    )
    parent_uid_value = account.parent_uid if account.parent_uid is not None else account.uid
    return APIKeyResponse(
        id=result.id,
        user_id=result.user_id,
        exchange=exchange.name,
        uid=account.uid,
        name=result.name,
        api_type=result.api_type,
        api_key=result.api_key,
        api_secret=result.api_secret,
        passphrase=result.passphrase or "",
        parent_uid=parent_uid_value,
        created_at=result.created_at.isoformat() if result.created_at else "",
    )


# --- Account endpoints ---
@router.get("/acct", response_model=List[AccountResponse])
async def get_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    exch_id: Optional[int] = Query(None, description="Filter by exchange ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Get all accounts for the current user. Query params: exch_id (optional), skip, limit."""
    account_repo = AccountRepository(db)
    accounts = account_repo.query(user_id=current_user.id, exch_id=exch_id, skip=skip, limit=limit)
    result = []
    for acct in accounts:
        exchange = db.query(RefExchange).filter(RefExchange.id == acct.exch_id).first()
        exchange_name = exchange.name if exchange else ""
        parent_uid_value = acct.parent_uid if acct.parent_uid is not None else acct.uid
        result.append(
            AccountResponse(
                id=acct.id,
                uid=acct.uid,
                exch_id=acct.exch_id,
                exchange=exchange_name,
                name=acct.name,
                parent_uid=parent_uid_value,
            )
        )
    return result


@router.put("/acct/rename", response_model=AccountResponse)
async def rename_account(
    body: RenameAccountRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename an account by ID."""
    account_repo = AccountRepository(db)
    account = account_repo.get_by_user_and_id(current_user.id, body.id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to you",
        )
    account.name = body.name
    account_repo.update(account)
    exchange = db.query(RefExchange).filter(RefExchange.id == account.exch_id).first()
    exchange_name = exchange.name if exchange else ""
    parent_uid_value = account.parent_uid if account.parent_uid is not None else account.uid
    return AccountResponse(
        id=account.id,
        uid=account.uid,
        exch_id=account.exch_id,
        exchange=exchange_name,
        name=account.name,
        parent_uid=parent_uid_value,
    )


# --- Wallet endpoints ---
@router.get("/wallet", response_model=List[WalletResponse])
async def get_wallets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    acct_id: Optional[int] = Query(None, description="Filter by account ID"),
    category_id: Optional[int] = Query(None, description="Filter by wallet type (category) ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Get wallets for the current user. Query params: acct_id, category_id (optional), skip, limit."""
    wallet_repo = WalletRepository(db)
    wallets = wallet_repo.query_wallets(
        user_id=current_user.id,
        acct_id=acct_id,
        category_id=category_id,
        skip=skip,
        limit=limit,
    )
    return [
        WalletResponse(
            id=w.id,
            name=w.name,
            category_id=w.category_id,
            acct_id=w.acct_id,
            user_id=w.user_id,
            is_active=w.is_active,
        )
        for w in wallets
    ]


@router.post("/wallet", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
async def create_wallet(
    body: WalletCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a wallet under an account by wallet type (category_id = ref_wallet id)."""
    account_repo = AccountRepository(db)
    account = account_repo.get_by_user_and_id(current_user.id, body.acct_id)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account not found or does not belong to you",
        )
    wallet_repo = WalletRepository(db)
    ref_wallet = wallet_repo.get_ref_wallet(body.category_id)
    if not ref_wallet:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wallet type (category) with ID {body.category_id} not found",
        )
    name = body.name
    if not name or not name.strip():
        name = f"{account.name}_{ref_wallet.slug}"
    wallet = wallet_repo.create_wallet(
        name=name.strip(),
        category_id=body.category_id,
        user_id=current_user.id,
        acct_id=body.acct_id,
        is_active=True,
    )
    return WalletResponse(
        id=wallet.id,
        name=wallet.name,
        category_id=wallet.category_id,
        acct_id=wallet.acct_id,
        user_id=wallet.user_id,
        is_active=wallet.is_active,
    )


@router.put("/wallet/rename", response_model=WalletResponse)
async def rename_wallet(
    body: RenameWalletRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rename a wallet by ID."""
    wallet_repo = WalletRepository(db)
    wallet = wallet_repo.get_wallet_by_user_and_id(current_user.id, body.id)
    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found or does not belong to you",
        )
    wallet.name = body.name
    wallet_repo.update_wallet(wallet)
    return WalletResponse(
        id=wallet.id,
        name=wallet.name,
        category_id=wallet.category_id,
        acct_id=wallet.acct_id,
        user_id=wallet.user_id,
        is_active=wallet.is_active,
    )


@router.delete("/wallet/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wallet(
    wallet_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a wallet by ID. Verifies ownership."""
    wallet_repo = WalletRepository(db)
    deleted = wallet_repo.delete_wallet_by_user_and_id(current_user.id, wallet_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found or does not belong to you",
        )
    return None
