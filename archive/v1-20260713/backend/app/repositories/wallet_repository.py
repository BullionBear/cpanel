"""Wallet and WalletArchived repositories for database operations."""
from typing import Optional, List
from sqlalchemy.orm import Session
from app.models import RefWallet, Wallet, WalletArchived, APIArchived


class WalletArchivedRepository:
    """Repository for RefWallet (ref_wallet) and WalletArchived (wallet_archived) model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    # --- ref_wallet (RefWallet) ---

    def get_ref_wallet(self, wallet_type_id: int) -> Optional[RefWallet]:
        """
        Get ref_wallet by ID.

        Args:
            wallet_type_id: ref_wallet ID

        Returns:
            RefWallet object if found, None otherwise
        """
        return self.db.query(RefWallet).filter(RefWallet.id == wallet_type_id).first()

    def get_ref_wallet_by_slug(self, slug: str) -> Optional[RefWallet]:
        """
        Get ref_wallet by slug.

        Args:
            slug: ref_wallet slug (e.g. 'SPOT', 'FUTURES')

        Returns:
            RefWallet object if found, None otherwise
        """
        return self.db.query(RefWallet).filter(RefWallet.slug == slug).first()

    def query_ref_wallets(self, skip: int = 0, limit: int = 100) -> List[RefWallet]:
        """
        List all ref_wallet records.

        Returns:
            List of RefWallet objects
        """
        return self.db.query(RefWallet).offset(skip).limit(limit).all()

    # --- wallet (WalletArchived) ---

    def query_wallets_by_user_id(
        self,
        user_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> List[WalletArchived]:
        """
        Query wallets (WalletArchived) for a user (via API ownership).

        Args:
            user_id: User ID (wallets are joined with APIArchived where APIArchived.user_id = user_id)
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)

        Returns:
            List of WalletArchived objects
        """
        return (
            self.db.query(WalletArchived)
            .join(APIArchived, WalletArchived.api_id == APIArchived.id)
            .filter(APIArchived.user_id == user_id)
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
    ) -> List[WalletArchived]:
        """
        Query wallet (WalletArchived) with optional filters.

        Args:
            wallet_type_id: Filter by ref_wallet ID
            api_id: Filter by API key ID
            is_active: Filter by is_active
            skip: Number of records to skip (pagination)
            limit: Maximum number of records to return (pagination)

        Returns:
            List of WalletArchived objects
        """
        query = self.db.query(WalletArchived)

        if wallet_type_id is not None:
            query = query.filter(WalletArchived.wallet_type_id == wallet_type_id)
        if api_id is not None:
            query = query.filter(WalletArchived.api_id == api_id)
        if is_active is not None:
            query = query.filter(WalletArchived.is_active == is_active)

        return query.offset(skip).limit(limit).all()

    def get_wallet(self, wallet_id: int) -> Optional[WalletArchived]:
        """
        Get wallet (WalletArchived) by ID.

        Args:
            wallet_id: wallet table ID

        Returns:
            WalletArchived object if found, None otherwise
        """
        return self.db.query(WalletArchived).filter(WalletArchived.id == wallet_id).first()

    def get_wallet_by_user_and_id(self, user_id: int, wallet_id: int) -> Optional[WalletArchived]:
        """
        Get wallet (WalletArchived) by ID if it belongs to the user (via API).

        Args:
            user_id: User ID (wallet must be linked to an API owned by this user)
            wallet_id: wallet table ID

        Returns:
            WalletArchived object if found and owned by user, None otherwise
        """
        wallet = self.get_wallet(wallet_id)
        if not wallet or wallet.api_id is None:
            return None
        api = self.db.query(APIArchived).filter(APIArchived.id == wallet.api_id, APIArchived.user_id == user_id).first()
        if not api:
            return None
        return wallet

    def create_wallet(
        self,
        name: str,
        wallet_type_id: int,
        api_id: Optional[int] = None,
        is_active: bool = True,
    ) -> WalletArchived:
        """
        Create a new wallet (WalletArchived).

        Args:
            name: Wallet name
            wallet_type_id: ref_wallet ID
            api_id: API key ID (optional)
            is_active: Whether the wallet is active (default True)

        Returns:
            Created WalletArchived object
        """
        wallet = WalletArchived(
            name=name,
            wallet_type_id=wallet_type_id,
            api_id=api_id,
            is_active=is_active,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def update_wallet(self, wallet: WalletArchived) -> WalletArchived:
        """
        Update wallet (commit changes to database).

        Args:
            wallet: WalletArchived object with updated fields

        Returns:
            Updated WalletArchived object
        """
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def delete_wallet(self, wallet_id: int) -> bool:
        """
        Delete wallet (WalletArchived) by ID.

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
        Delete wallet (WalletArchived) by ID if it belongs to the user (via API).

        Args:
            user_id: User ID (wallet must be linked to an API owned by this user)
            wallet_id: wallet table ID to delete

        Returns:
            True if deleted, False if not found or not owned by user
        """
        wallet = self.get_wallet(wallet_id)
        if not wallet or wallet.api_id is None:
            return False
        api = self.db.query(APIArchived).filter(APIArchived.id == wallet.api_id, APIArchived.user_id == user_id).first()
        if not api:
            return False
        self.db.delete(wallet)
        self.db.commit()
        return True


class WalletRepository:
    """Repository for RefWallet (ref_wallet) and Wallet (wallet) model operations."""

    def __init__(self, db: Session):
        """Initialize repository with database session."""
        self.db = db

    # --- ref_wallet (RefWallet) ---

    def get_ref_wallet(self, wallet_type_id: int) -> Optional[RefWallet]:
        """Get ref_wallet by ID."""
        return self.db.query(RefWallet).filter(RefWallet.id == wallet_type_id).first()

    def get_ref_wallet_by_slug(self, slug: str) -> Optional[RefWallet]:
        """Get ref_wallet by slug (e.g. 'SPOT', 'FUTURES')."""
        return self.db.query(RefWallet).filter(RefWallet.slug == slug).first()

    def query_ref_wallets(self, skip: int = 0, limit: int = 100) -> List[RefWallet]:
        """List all ref_wallet records."""
        return self.db.query(RefWallet).offset(skip).limit(limit).all()

    # --- wallet (Wallet) ---

    def query_wallets_by_user_id(
        self,
        user_id: int,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Wallet]:
        """Query wallets for a user."""
        return (
            self.db.query(Wallet)
            .filter(Wallet.user_id == user_id)
            .offset(skip)
            .limit(limit)
            .all()
        )

    def query_wallets(
        self,
        category_id: Optional[int] = None,
        acct_id: Optional[int] = None,
        user_id: Optional[int] = None,
        is_active: Optional[bool] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Wallet]:
        """Query wallets with optional filters."""
        query = self.db.query(Wallet)
        if category_id is not None:
            query = query.filter(Wallet.category_id == category_id)
        if acct_id is not None:
            query = query.filter(Wallet.acct_id == acct_id)
        if user_id is not None:
            query = query.filter(Wallet.user_id == user_id)
        if is_active is not None:
            query = query.filter(Wallet.is_active == is_active)
        return query.offset(skip).limit(limit).all()

    def get_wallet(self, wallet_id: int) -> Optional[Wallet]:
        """Get wallet by ID."""
        return self.db.query(Wallet).filter(Wallet.id == wallet_id).first()

    def get_wallet_by_user_and_id(self, user_id: int, wallet_id: int) -> Optional[Wallet]:
        """Get wallet by ID if it belongs to the user."""
        wallet = self.get_wallet(wallet_id)
        if not wallet or wallet.user_id != user_id:
            return None
        return wallet

    def create_wallet(
        self,
        name: str,
        category_id: int,
        user_id: int,
        acct_id: int,
        is_active: bool = True,
    ) -> Wallet:
        """Create a new wallet."""
        wallet = Wallet(
            name=name,
            category_id=category_id,
            user_id=user_id,
            acct_id=acct_id,
            is_active=is_active,
        )
        self.db.add(wallet)
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def update_wallet(self, wallet: Wallet) -> Wallet:
        """Update wallet (commit changes to database)."""
        self.db.commit()
        self.db.refresh(wallet)
        return wallet

    def delete_wallet(self, wallet_id: int) -> bool:
        """Delete wallet by ID."""
        wallet = self.get_wallet(wallet_id)
        if wallet:
            self.db.delete(wallet)
            self.db.commit()
            return True
        return False

    def delete_wallet_by_user_and_id(self, user_id: int, wallet_id: int) -> bool:
        """Delete wallet by ID if it belongs to the user."""
        wallet = self.get_wallet_by_user_and_id(user_id, wallet_id)
        if not wallet:
            return False
        self.db.delete(wallet)
        self.db.commit()
        return True
