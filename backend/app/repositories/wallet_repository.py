"""Wallet repository for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.models import Wallet, WalletAccount, API


class WalletRepository:
    """Repository for Wallet (ref_wallet) and WalletAccount (wallet) model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    # --- ref_wallet (Wallet) ---

    def get_ref_wallet(self, wallet_type_id: int) -> Optional[Wallet]:
        """
        Get ref_wallet by ID.

        Args:
            wallet_type_id: ref_wallet ID

        Returns:
            Wallet object if found, None otherwise
        """
        return self.db.query(Wallet).filter(Wallet.id == wallet_type_id).first()

    def get_ref_wallet_by_slug(self, slug: str) -> Optional[Wallet]:
        """
        Get ref_wallet by slug.

        Args:
            slug: ref_wallet slug (e.g. 'SPOT', 'FUTURES')

        Returns:
            Wallet object if found, None otherwise
        """
        return self.db.query(Wallet).filter(Wallet.slug == slug).first()

    def query_ref_wallets(self, skip: int = 0, limit: int = 100) -> List[Wallet]:
        """
        List all ref_wallet records.

        Returns:
            List of Wallet objects
        """
        return self.db.query(Wallet).offset(skip).limit(limit).all()

    # --- wallet (WalletAccount) ---

    def query_wallets_by_user_id(
        self,
        user_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> List[WalletAccount]:
        """
        Query wallets (WalletAccount) for a user (via API ownership).

        Args:
            user_id: User ID (wallets are joined with API where API.user_id = user_id)
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)

        Returns:
            List of WalletAccount objects
        """
        return (
            self.db.query(WalletAccount)
            .join(API, WalletAccount.api_id == API.id)
            .filter(API.user_id == user_id)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def query_wallets(
        self,
        wallet_type_id: Optional[int] = None,
        api_id: Optional[int] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[WalletAccount]:
        """
        Query wallet (WalletAccount) with optional filters.

        Args:
            wallet_type_id: Filter by ref_wallet ID
            api_id: Filter by API key ID
            is_active: Filter by is_active
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)

        Returns:
            List of WalletAccount objects
        """
        query = self.db.query(WalletAccount)

        if wallet_type_id is not None:
            query = query.filter(WalletAccount.wallet_type_id == wallet_type_id)
        if api_id is not None:
            query = query.filter(WalletAccount.api_id == api_id)
        if is_active is not None:
            query = query.filter(WalletAccount.is_active == is_active)

        return query.offset(skip).limit(limit).all()

    def get_wallet(self, wallet_id: int) -> Optional[WalletAccount]:
        """
        Get wallet (WalletAccount) by ID.

        Args:
            wallet_id: wallet table ID

        Returns:
            WalletAccount object if found, None otherwise
        """
        return self.db.query(WalletAccount).filter(WalletAccount.id == wallet_id).first()

    def create_wallet(
        self,
        name: str,
        wallet_type_id: int,
        api_id: Optional[int] = None,
        is_active: bool = True,
    ) -> WalletAccount:
        """
        Create a new wallet (WalletAccount).

        Args:
            name: Wallet name
            wallet_type_id: ref_wallet ID
            api_id: API key ID (optional)
            is_active: Whether the wallet is active (default True)

        Returns:
            Created WalletAccount object
        """
        wallet = WalletAccount(
            name=name,
            wallet_type_id=wallet_type_id,
            api_id=api_id,
            is_active=is_active,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def update_wallet(self, wallet: WalletAccount) -> WalletAccount:
        """
        Update wallet (commit changes to database).

        Args:
            wallet: WalletAccount object with updated fields

        Returns:
            Updated WalletAccount object
        """
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def delete_wallet(self, wallet_id: int) -> bool:
        """
        Delete wallet (WalletAccount) by ID.

        Args:
            wallet_id: wallet table ID to delete

        Returns:
            True if deleted, False if not found
        """
        wallet = self.get_wallet(wallet_id)
        if wallet:
            self.db.delete(wallet)
            self.db.commit()
            return True
        return False

    def delete_wallet_by_user_and_id(self, user_id: int, wallet_id: int) -> bool:
        """
        Delete wallet (WalletAccount) by ID if it belongs to the user (via API).

        Args:
            user_id: User ID (wallet must be linked to an API owned by this user)
            wallet_id: wallet table ID to delete

        Returns:
            True if deleted, False if not found or not owned by user
        """
        wallet = self.get_wallet(wallet_id)
        if not wallet or wallet.api_id is None:
            return False
        api = self.db.query(API).filter(API.id == wallet.api_id, API.user_id == user_id).first()
        if not api:
            return False
        self.db.delete(wallet)
        self.db.commit()
        return True
