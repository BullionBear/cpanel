"""Transfer state machine tests against the fake connector."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.connectors.fake import FakeConnector
from app.connectors.types import AssetNetwork, WalletType
from app.core.config import settings
from app.models.snapshot import BalanceSnapshot
from app.models.transfer import Transfer, TransferStep
from app.services import accounts as accounts_svc
from app.services import snapshots as snapshots_svc
from app.services.transfers import (
    CROSS_STEPS,
    TransferGuardrailError,
    TransferValidationError,
    advance_transfer,
    create_cross_transfer,
    create_intra_transfer,
    create_withdrawal,
    fail_transfer,
    flag_stuck_transfers,
    get_deposit_address,
    get_transfer,
    list_available_networks,
    list_recent_deposits,
    preview_cross_transfer,
    preview_withdrawal,
    retry_transfer,
)


def make_fake(exchange: str = "FAKE") -> FakeConnector:
    connector = FakeConnector()
    connector.exchange = exchange
    return connector


@pytest_asyncio.fixture
async def fake():
    return make_fake()


@pytest_asyncio.fixture
async def main_account(db, fake):
    account = await accounts_svc.register_account(db, "FAKE", "main", "main")
    await accounts_svc.add_credential(db, fake, account, "HMAC", "key-1", "secret-1")
    return account


@pytest_asyncio.fixture
async def sub_account(db, main_account):
    return await accounts_svc.register_account(
        db, "FAKE", "sub-1", "sub", parent_id=main_account.id, native_uid="uid-77"
    )


def step_status(transfer, name):
    return next(s.status for s in transfer.steps if s.name == name)


# -- intra-exchange -----------------------------------------------------------

async def test_wallet_move_completes(db, fake, main_account):
    transfer = await create_intra_transfer(
        db, fake, main_account, main_account, "USDT", Decimal("100"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.FUNDING,
    )
    assert transfer.status == "completed"
    assert [s.status for s in transfer.steps] == ["succeeded", "succeeded"]
    assert transfer.native_transfer_id
    assert transfer.completed_at is not None
    assert fake.transfers[0]["from_wallet"] == WalletType.SPOT
    assert fake.transfers[0]["to_wallet"] == WalletType.FUNDING


async def test_wallet_move_refreshes_balance_snapshot(db, fake, main_account):
    """Completed intra transfer writes a new snapshot reflecting venue balances."""
    await snapshots_svc.capture_snapshots(db, fake, main_account)
    before = await snapshots_svc.latest_batch(db, main_account.id)
    assert before is not None

    transfer = await create_intra_transfer(
        db, fake, main_account, main_account, "USDT", Decimal("100"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.FUNDING,
    )
    assert transfer.status == "completed"

    after = await snapshots_svc.latest_batch(db, main_account.id)
    assert after is not None
    assert after[0] != before[0]

    rows = {
        (b.wallet, b.asset): b
        for b in (
            await db.execute(
                select(BalanceSnapshot).where(BalanceSnapshot.batch_id == after[0])
            )
        ).scalars()
    }
    assert rows[("SPOT", "USDT")].free == Decimal("9900")
    assert rows[("FUNDING", "USDT")].free == Decimal("100")


async def test_post_transfer_snapshot_failure_keeps_completed(
    db, fake, main_account, monkeypatch
):
    """A failed post-transfer snapshot must not reopen or fail the transfer."""
    monkeypatch.setattr(
        "app.services.transfers.capture_and_publish",
        AsyncMock(side_effect=RuntimeError("venue balance timeout")),
    )
    transfer = await create_intra_transfer(
        db, fake, main_account, main_account, "USDT", Decimal("25"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.FUNDING,
    )
    assert transfer.status == "completed"
    assert transfer.completed_at is not None
    assert transfer.error is None


async def test_post_transfer_retries_when_source_balance_lags(
    db, fake, main_account, monkeypatch
):
    """One retry when the first balance fetch still shows the pre-transfer amount."""
    await snapshots_svc.capture_snapshots(db, fake, main_account)
    fake.transfer_balance_lag_fetches = 1
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.services.transfers.asyncio.sleep", _fake_sleep)

    transfer = await create_intra_transfer(
        db, fake, main_account, main_account, "USDT", Decimal("100"),
        from_wallet=WalletType.SPOT, to_wallet=WalletType.FUNDING,
    )
    assert transfer.status == "completed"
    assert slept == [0.5]

    batch = await snapshots_svc.latest_batch(db, main_account.id)
    assert batch is not None
    rows = {
        (b.wallet, b.asset): b
        for b in (
            await db.execute(
                select(BalanceSnapshot).where(BalanceSnapshot.batch_id == batch[0])
            )
        ).scalars()
    }
    assert rows[("SPOT", "USDT")].free == Decimal("9900")
    assert rows[("FUNDING", "USDT")].free == Decimal("100")


async def test_main_to_sub_uses_master_credential(db, fake, main_account, sub_account):
    transfer = await create_intra_transfer(
        db, fake, main_account, sub_account, "USDT", Decimal("50")
    )
    assert transfer.status == "completed"
    record = fake.transfers[0]
    assert record["from_subaccount"] is None       # master side
    assert record["to_subaccount"] == "uid-77"


async def test_same_account_needs_two_wallets(db, fake, main_account):
    with pytest.raises(TransferValidationError):
        await create_intra_transfer(
            db, fake, main_account, main_account, "USDT", Decimal("1"),
            from_wallet=WalletType.SPOT, to_wallet=WalletType.SPOT,
        )


async def test_cross_hierarchy_rejected(db, fake, main_account):
    other = await accounts_svc.register_account(db, "FAKE", "other-main", "main")
    await accounts_svc.add_credential(db, fake, other, "HMAC", "key-2", "secret-2")
    with pytest.raises(TransferValidationError):
        await create_intra_transfer(
            db, fake, main_account, other, "USDT", Decimal("1")
        )


async def test_max_value_guardrail(db, fake, main_account, sub_account, monkeypatch):
    monkeypatch.setattr(settings, "TRANSFER_MAX_VALUE", 100)
    with pytest.raises(TransferGuardrailError):
        await create_intra_transfer(
            db, fake, main_account, sub_account, "USDT", Decimal("200")
        )
    # At or below the cap passes (USDT is a stable, valued 1:1).
    transfer = await create_intra_transfer(
        db, fake, main_account, sub_account, "USDT", Decimal("100")
    )
    assert transfer.status == "completed"


async def test_ambiguous_submit_marks_stuck_then_retry(db, fake, main_account, sub_account):
    fake.transfer_failure = RuntimeError("connection reset mid-call")
    transfer = await create_intra_transfer(
        db, fake, main_account, sub_account, "USDT", Decimal("5")
    )
    assert transfer.status == "in_progress"
    assert transfer.stuck is True
    assert step_status(transfer, "submit") == "running"
    assert "ambiguously" in transfer.error

    # Another advance pass never re-executes the money-moving step.
    transfer = await advance_transfer(db, transfer, lambda _: fake)
    assert transfer.stuck is True
    assert fake.transfers == []

    # Operator verified nothing happened on the venue and retries.
    transfer = await retry_transfer(db, transfer, lambda _: fake)
    assert transfer.status == "completed"
    assert len(fake.transfers) == 1


async def test_interrupted_money_step_resumes_stuck(db, fake, main_account, sub_account):
    """A submit step found running after a restart is surfaced, not re-run."""
    transfer = Transfer(
        kind="intra", status="in_progress", asset="USDT", amount=Decimal("5"),
        from_account_id=main_account.id, to_account_id=sub_account.id,
        from_exchange="FAKE", to_exchange="FAKE",
        updated_at=datetime.now(timezone.utc),
    )
    transfer.steps.append(TransferStep(seq=0, name="submit", status="running"))
    transfer.steps.append(TransferStep(seq=1, name="confirm", status="pending"))
    db.add(transfer)
    await db.commit()

    transfer = await get_transfer(db, transfer.id)
    transfer = await advance_transfer(db, transfer, lambda _: fake)
    assert transfer.stuck is True
    assert "interrupted mid-call" in transfer.error
    assert fake.transfers == []


# -- cross-exchange -----------------------------------------------------------

@pytest_asyncio.fixture
async def cross(db, fake, main_account):
    """Source FAKE main account + destination FAKE2 account/connector."""
    fake2 = make_fake("FAKE2")
    dest = await accounts_svc.register_account(db, "FAKE2", "main", "main")
    await accounts_svc.add_credential(db, fake2, dest, "HMAC", "key-9", "secret-9")

    def get_connector(exchange):
        return fake if exchange == "FAKE" else fake2

    return fake2, dest, get_connector


async def test_cross_transfer_end_to_end(db, fake, main_account, cross):
    fake2, dest, get_connector = cross
    transfer = await create_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("100"), "TESTNET"
    )
    # Synchronous part ran: address resolved, validated, withdrawal submitted.
    assert transfer.status == "in_progress"
    assert transfer.address == "fake-USDT-TESTNET-self"
    assert transfer.native_withdrawal_id in fake.withdrawals
    assert step_status(transfer, "submit_withdrawal") == "succeeded"
    assert step_status(transfer, "await_withdrawal") == "running"

    # Poll again: withdrawal still pending on the venue → no change.
    transfer = await advance_transfer(db, transfer, get_connector)
    assert step_status(transfer, "await_withdrawal") == "running"

    # Venue broadcasts the withdrawal.
    fake.complete_withdrawal(transfer.native_withdrawal_id, tx_id="0xabc", fee=Decimal("0.5"))
    transfer = await advance_transfer(db, transfer, get_connector)
    assert transfer.tx_id == "0xabc"
    assert transfer.fee == Decimal("0.5")
    assert step_status(transfer, "await_withdrawal") == "succeeded"
    assert step_status(transfer, "await_deposit") == "running"

    # Deposit credits on the destination with the matching tx hash.
    fake2.credit_deposit("USDT", Decimal("99.5"), tx_id="0xabc")
    transfer = await advance_transfer(db, transfer, get_connector)
    assert transfer.status == "completed"
    assert transfer.received_amount == Decimal("99.5")
    assert transfer.deposit_native_id
    assert [s.status for s in transfer.steps] == ["succeeded"] * len(CROSS_STEPS)


async def test_cross_validate_rejects_disabled_network(db, fake, main_account, cross):
    fake2, dest, _ = cross
    fake.networks["USDT"] = [
        AssetNetwork(
            network="TESTNET",
            deposit_enabled=True,
            withdraw_enabled=False,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("0.5"),
        )
    ]
    transfer = await create_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("100"), "TESTNET"
    )
    assert transfer.status == "failed"
    assert step_status(transfer, "resolve_address") == "succeeded"
    assert step_status(transfer, "validate") == "failed"
    assert fake.withdrawals == {}


async def test_cross_below_minimum_fails_validate(db, fake, main_account, cross):
    fake2, dest, _ = cross
    transfer = await create_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("0.2"), "TESTNET"
    )
    assert transfer.status == "failed"
    assert "minimum" in transfer.error


async def test_deposit_matching_by_amount(db, fake, main_account, cross):
    """Without a shared tx hash the match falls back to amount-within-fee."""
    fake2, dest, get_connector = cross
    transfer = await create_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("100"), "TESTNET"
    )
    fake.complete_withdrawal(transfer.native_withdrawal_id, fee=Decimal("0.5"))
    transfer = await advance_transfer(db, transfer, get_connector)

    # Wrong amount: not our deposit.
    fake2.credit_deposit("USDT", Decimal("42"), tx_id=None)
    transfer = await advance_transfer(db, transfer, get_connector)
    assert transfer.status == "in_progress"

    # Amount within [amount - fee, amount] and no conflicting tx id: matched.
    fake2.deposits.clear()
    deposit = fake2.credit_deposit("USDT", Decimal("99.5"), tx_id=None)
    transfer = await advance_transfer(db, transfer, get_connector)
    assert transfer.status == "completed"
    assert transfer.deposit_native_id == deposit.native_id


async def test_preview_reports_problems_without_moving(db, fake, main_account, cross):
    fake2, dest, _ = cross
    preview = await preview_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("100"), "TESTNET"
    )
    assert preview["ok"] is True
    assert preview["expected_fee"] == "0.5"

    below_min = await preview_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("0.2"), "TESTNET"
    )
    assert below_min["ok"] is False
    assert any("minimum" in p for p in below_min["problems"])
    assert fake.withdrawals == {}  # nothing moved


async def test_operator_fail_marks_running_step(db, fake, main_account, cross):
    fake2, dest, get_connector = cross
    transfer = await create_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("100"), "TESTNET"
    )
    transfer = await fail_transfer(db, transfer, "operator cancelled after venue check")
    assert transfer.status == "failed"
    assert step_status(transfer, "await_withdrawal") == "failed"
    # Terminal: further advances are no-ops.
    transfer = await advance_transfer(db, transfer, get_connector)
    assert transfer.status == "failed"


async def test_intra_rejects_wallet_the_exchange_lacks(db, fake, main_account, sub_account):
    # FakeConnector's wallet structure is SPOT/FUNDING/FUTURES — no UNIFIED.
    with pytest.raises(TransferValidationError, match="no UNIFIED wallet"):
        await create_intra_transfer(
            db, fake, main_account, sub_account, "USDT", Decimal("10"),
            from_wallet=WalletType.UNIFIED, to_wallet=WalletType.SPOT,
        )
    assert fake.transfers == []


async def test_withdrawal_to_external_address(db, fake, main_account):
    transfer = await create_withdrawal(
        db, fake, main_account, "USDT", Decimal("100"),
        address="TExternal123", network="TESTNET",
    )
    assert transfer.kind == "withdrawal"
    assert transfer.to_account_id is None
    assert transfer.to_exchange == "EXTERNAL"
    assert [s.name for s in transfer.steps] == [
        "validate", "submit_withdrawal", "await_withdrawal",
    ]
    assert step_status(transfer, "submit_withdrawal") == "succeeded"
    assert step_status(transfer, "await_withdrawal") == "running"
    assert fake.withdrawals[transfer.native_withdrawal_id].address == "TExternal123"

    # Venue broadcasts it: the transfer completes — no deposit to match.
    fake.complete_withdrawal(transfer.native_withdrawal_id, tx_id="0xw", fee=Decimal("0.5"))
    transfer = await advance_transfer(db, transfer, lambda _: fake)
    assert transfer.status == "completed"
    assert transfer.tx_id == "0xw"
    assert transfer.fee == Decimal("0.5")


async def test_withdrawal_below_minimum_fails(db, fake, main_account):
    transfer = await create_withdrawal(
        db, fake, main_account, "USDT", Decimal("0.2"),
        address="TExternal123", network="TESTNET",
    )
    assert transfer.status == "failed"
    assert "minimum" in transfer.error
    assert fake.withdrawals == {}


async def test_withdrawal_requires_address(db, fake, main_account):
    with pytest.raises(TransferValidationError):
        await create_withdrawal(
            db, fake, main_account, "USDT", Decimal("10"),
            address="  ", network="TESTNET",
        )


async def test_preview_withdrawal_source_side(db, fake, main_account):
    ok = await preview_withdrawal(
        db, fake, main_account, "USDT", Decimal("100"), "TESTNET"
    )
    assert ok["ok"] is True
    assert ok["expected_fee"] == "0.5"

    bad = await preview_withdrawal(
        db, fake, main_account, "USDT", Decimal("100"), "NOPE"
    )
    assert bad["ok"] is False
    assert fake.withdrawals == {}


async def test_list_available_networks_suggests_cheapest(db, fake, main_account):
    fake.networks["USDT"] = [
        AssetNetwork(
            network="ETH",
            deposit_enabled=True,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("5"),
        ),
        AssetNetwork(
            network="TRX",
            deposit_enabled=True,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("1"),
        ),
        AssetNetwork(
            network="OFF",
            deposit_enabled=True,
            withdraw_enabled=False,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("0.1"),
        ),
    ]
    result = await list_available_networks(
        db, "usdt", from_account=main_account, source_connector=fake
    )
    assert [n["network"] for n in result["networks"]] == ["TRX", "ETH"]
    assert result["suggested_network"] == "TRX"


async def test_list_available_networks_intersects_dest(db, fake, main_account, cross):
    fake2, dest, _ = cross
    fake.networks["USDT"] = [
        AssetNetwork(
            network="TRX",
            deposit_enabled=True,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("1"),
        ),
        AssetNetwork(
            network="ETH",
            deposit_enabled=True,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("2"),
        ),
    ]
    fake2.networks["USDT"] = [
        AssetNetwork(
            network="ETH",
            deposit_enabled=True,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("2"),
        ),
        AssetNetwork(
            network="TRX",
            deposit_enabled=False,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("1"),
        ),
    ]
    result = await list_available_networks(
        db,
        "USDT",
        from_account=main_account,
        source_connector=fake,
        to_account=dest,
        dest_connector=fake2,
    )
    assert [n["network"] for n in result["networks"]] == ["ETH"]
    assert result["suggested_network"] == "ETH"


async def test_list_available_networks_deposit_only(db, fake, main_account):
    fake.networks["USDT"] = [
        AssetNetwork(
            network="TRX",
            deposit_enabled=True,
            withdraw_enabled=False,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("1"),
        ),
        AssetNetwork(
            network="ETH",
            deposit_enabled=False,
            withdraw_enabled=True,
            withdraw_min=Decimal("1"),
            withdraw_fee=Decimal("2"),
        ),
    ]
    result = await list_available_networks(
        db, "USDT", to_account=main_account, dest_connector=fake
    )
    assert [n["network"] for n in result["networks"]] == ["TRX"]
    assert result["suggested_network"] == "TRX"


async def test_deposit_address_and_recent_deposits(db, fake, main_account):
    resolved = await get_deposit_address(db, fake, main_account, "usdt", "testnet")
    assert resolved.address == "fake-USDT-TESTNET-self"

    fake.credit_deposit("USDT", Decimal("25"), tx_id="0xd")
    deposits = await list_recent_deposits(db, fake, main_account, asset="USDT")
    assert len(deposits) == 1
    assert deposits[0].amount == Decimal("25")


async def test_stuck_flagging_by_age(db, fake, main_account, cross):
    fake2, dest, _ = cross
    transfer = await create_cross_transfer(
        db, fake, fake2, main_account, dest, "USDT", Decimal("100"), "TESTNET"
    )
    assert await flag_stuck_transfers(db) == 0  # fresh: not stuck

    transfer.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
    await db.commit()
    assert await flag_stuck_transfers(db) == 1
    transfer = await get_transfer(db, transfer.id)
    assert transfer.stuck is True
    assert transfer.status == "in_progress"  # still polled, just surfaced
