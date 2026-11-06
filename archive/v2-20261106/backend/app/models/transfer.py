"""Transfers as persisted state machines (ARCHITECTURE.md "Transfer
Orchestration").

A ``transfer`` row is one fund movement — intra-exchange (main↔sub or
wallet↔wallet, a single venue call), cross-exchange (withdrawal on the
source venue matched to a deposit on the destination), or a plain withdrawal
to an external address (no destination account). Its ``transfer_step``
rows are the ordered step records: each step transition is persisted, so an
interrupted transfer resumes from its last confirmed step after a restart,
and a money-moving step found mid-flight is surfaced to the operator instead
of being blindly re-executed.

``status`` is coarse (in_progress / completed / failed); the step rows carry
the granular state. ``stuck`` flags a transfer needing operator attention —
either a possibly-in-flight submission of unknown outcome, or no progress for
longer than TRANSFER_STUCK_MINUTES — without stopping progress polling.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.audit_log import BigIntPk, JsonDict
from app.models.base import Base, PreciseDecimal

Num = PreciseDecimal()


class Transfer(Base):
    __tablename__ = "transfer"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # intra | cross | withdrawal
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", index=True
    )  # in_progress | completed | failed
    stuck: Mapped[bool] = mapped_column(nullable=False, default=False)

    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Num, nullable=False)

    from_account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"), nullable=False, index=True
    )
    # None for plain withdrawals: the destination is an external address, not
    # a registered account (to_exchange is then "EXTERNAL").
    to_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id"), index=True
    )
    from_exchange: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    to_exchange: Mapped[str] = mapped_column(String(32), nullable=False)

    # Intra-exchange wallet legs (normalized WalletType values).
    from_wallet: Mapped[str | None] = mapped_column(String(16))
    to_wallet: Mapped[str | None] = mapped_column(String(16))

    # Cross-exchange plumbing. The address is resolved fresh per transfer.
    network: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    address_tag: Mapped[str | None] = mapped_column(String(64))

    native_transfer_id: Mapped[str | None] = mapped_column(String(64))    # intra
    native_withdrawal_id: Mapped[str | None] = mapped_column(String(64))  # cross
    tx_id: Mapped[str | None] = mapped_column(String(128))
    deposit_native_id: Mapped[str | None] = mapped_column(String(128))

    # What actually arrived / was charged, recorded at completion.
    received_amount: Mapped[Decimal | None] = mapped_column(Num)
    fee: Mapped[Decimal | None] = mapped_column(Num)
    fee_asset: Mapped[str | None] = mapped_column(String(32))

    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Any step transition bumps this; stuck detection keys off it.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["TransferStep"]] = relationship(
        back_populates="transfer",
        cascade="all, delete-orphan",
        order_by="TransferStep.seq",
    )


class TransferStep(Base):
    __tablename__ = "transfer_step"
    __table_args__ = (
        UniqueConstraint("transfer_id", "seq", name="uq_transfer_step_transfer_seq"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    transfer_id: Mapped[int] = mapped_column(
        ForeignKey("transfer.id"), nullable=False, index=True
    )

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    # pending | running | succeeded | failed. Waiting steps (withdrawal /
    # deposit polls) stay "running" between polls.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    detail: Mapped[dict | None] = mapped_column(JsonDict)
    error: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transfer: Mapped[Transfer] = relationship(back_populates="steps")
