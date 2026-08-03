"""Sweep Bybit master + subaccount funds into the master's Funding wallet.

Reads a JSON list of master credentials, flattens each account hierarchy, and
pulls every subaccount's Funding balance up to the master Unified wallet, then
parks the master's Unified balance in Funding.

Must run on the IP bound to temporary sub API keys (default 47.128.168.131):

    cd backend
    python scripts/sweep_bybit_funds.py --input masters.json
    python scripts/sweep_bybit_funds.py --input masters.json --dry-run

Input JSON shape:

    [
      {"venue": "bybit", "api_key": "...", "api_secret": "..."},
      {"venue": "bybit_eu", "api_key": "...", "api_secret": "..."}
    ]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from decimal import Decimal
from pathlib import Path

from app.connectors.base import Credential
from app.connectors.bybit import BybitConnector, resolve_venue_endpoint
from app.connectors.types import (
    KeyScheme,
    OrderRequest,
    OrderSide,
    OrderType,
    PositionSide,
    WalletType,
)
from app.core.ratelimit import TokenBucket

logger = logging.getLogger("sweep_bybit_funds")

DEFAULT_BIND_IP = "47.128.168.131"
SUB_KEY_PERMISSIONS = ["spot", "derivatives", "wallet"]
# Sub UIDs skipped during sweep (exclusion list).
DEFAULT_EXCLUDE_SUB_UIDS = frozenset({"540005691", "540005748"})
# Brief pause after flatten so wallet balances catch up before transfers.
_POST_FLATTEN_SLEEP_S = 0.5


def _cred(api_key: str, api_secret: str) -> Credential:
    return {"api_key": api_key, "secret": api_secret, "scheme": "HMAC"}


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def load_masters(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list) or not raw:
        raise SystemExit("input JSON must be a non-empty list of master credentials")
    masters: list[dict[str, str]] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise SystemExit(f"entry {i}: expected object")
        venue = str(row.get("venue") or "").strip()
        api_key = str(row.get("api_key") or "").strip()
        api_secret = str(row.get("api_secret") or "").strip()
        if not venue or not api_key or not api_secret:
            raise SystemExit(f"entry {i}: venue, api_key, api_secret are required")
        # Validate venue early so a typo fails before any live call.
        resolve_venue_endpoint(venue)
        masters.append({"venue": venue, "api_key": api_key, "api_secret": api_secret})
    return masters


async def cancel_open_orders(
    connector: BybitConnector, cred: Credential, prefix: str, dry_run: bool
) -> int:
    orders = await connector.fetch_open_orders(cred)
    _log(prefix, f"open_orders={len(orders)}")
    cancelled = 0
    for order in orders:
        label = f"{order.universal_symbol} id={order.native_order_id}"
        if dry_run:
            _log(prefix, f"dry-run cancel {label}")
            cancelled += 1
            continue
        try:
            await connector.cancel_order(cred, order.universal_symbol, order.native_order_id)
            _log(prefix, f"cancelled {label}")
            cancelled += 1
        except Exception as exc:  # noqa: BLE001
            _log(prefix, f"cancel FAILED {label}: {type(exc).__name__}: {exc}")
    return cancelled


async def close_positions(
    connector: BybitConnector, cred: Credential, prefix: str, dry_run: bool
) -> int:
    positions = await connector.fetch_positions(cred)
    _log(prefix, f"positions={len(positions)}")
    closed = 0
    for pos in positions:
        close_side = OrderSide.SELL if pos.side == "long" else OrderSide.BUY
        # One-way mode (typical UTA): positionIdx=0 via BOTH.
        position_side = PositionSide.BOTH
        label = f"{pos.universal_symbol} {pos.side} size={pos.size}"
        if dry_run:
            _log(prefix, f"dry-run close {label} side={close_side.value}")
            closed += 1
            continue
        req = OrderRequest(
            universal_symbol=pos.universal_symbol,
            side=close_side,
            type=OrderType.MARKET,
            qty=pos.size,
            reduce_only=True,
            position_side=position_side,
        )
        try:
            placed = await connector.place_order(cred, req)
            _log(prefix, f"closed {label} order={placed.native_order_id}")
            closed += 1
        except Exception as exc:  # noqa: BLE001
            # Hedge-mode accounts need LONG/SHORT idx; retry once.
            hedge_side = PositionSide.LONG if pos.side == "long" else PositionSide.SHORT
            retry = OrderRequest(
                universal_symbol=pos.universal_symbol,
                side=close_side,
                type=OrderType.MARKET,
                qty=pos.size,
                reduce_only=True,
                position_side=hedge_side,
            )
            try:
                placed = await connector.place_order(cred, retry)
                _log(
                    prefix,
                    f"closed {label} order={placed.native_order_id} (hedge {hedge_side.value})",
                )
                closed += 1
            except Exception as exc2:  # noqa: BLE001
                _log(
                    prefix,
                    f"close FAILED {label}: {type(exc).__name__}: {exc}; "
                    f"retry {type(exc2).__name__}: {exc2}",
                )
    return closed


async def transfer_wallet_coins(
    connector: BybitConnector,
    cred: Credential,
    from_wallet: WalletType,
    to_wallet: WalletType,
    prefix: str,
    dry_run: bool,
    *,
    from_subaccount: str | None = None,
    to_subaccount: str | None = None,
    coins: list[tuple[str, Decimal]] | None = None,
) -> list[tuple[str, Decimal]]:
    """Move free balances from ``from_wallet`` to ``to_wallet``.

    When ``coins`` is None, amounts are read via ``fetch_balances`` on ``cred``.
    Returns the (asset, amount) pairs that were attempted.
    """
    if coins is None:
        balances = await connector.fetch_balances(cred)
        coins = [
            (b.asset, b.free)
            for b in balances
            if b.wallet == from_wallet and b.free > 0
        ]
    _log(prefix, f"transfer {from_wallet.value}->{to_wallet.value} coins={len(coins)}")
    moved: list[tuple[str, Decimal]] = []
    for asset, amount in coins:
        if amount <= 0:
            continue
        label = f"{amount} {asset}"
        if dry_run:
            _log(prefix, f"dry-run transfer {label}")
            moved.append((asset, amount))
            continue
        try:
            result = await connector.transfer(
                cred,
                asset,
                amount,
                from_wallet=from_wallet,
                to_wallet=to_wallet,
                from_subaccount=from_subaccount,
                to_subaccount=to_subaccount,
            )
            _log(
                prefix,
                f"transferred {label} id={result.native_transfer_id} status={result.status}",
            )
            moved.append((asset, amount))
        except Exception as exc:  # noqa: BLE001
            _log(prefix, f"transfer FAILED {label}: {type(exc).__name__}: {exc}")
    return moved


async def funding_coins(
    connector: BybitConnector, cred: Credential
) -> list[tuple[str, Decimal]]:
    balances = await connector.fetch_balances(cred)
    return [
        (b.asset, b.free)
        for b in balances
        if b.wallet == WalletType.FUNDING and b.free > 0
    ]


async def flatten_account(
    connector: BybitConnector, cred: Credential, prefix: str, dry_run: bool
) -> None:
    await cancel_open_orders(connector, cred, prefix, dry_run)
    await close_positions(connector, cred, prefix, dry_run)
    if not dry_run:
        await asyncio.sleep(_POST_FLATTEN_SLEEP_S)


async def sweep_subaccount(
    connector: BybitConnector,
    master_cred: Credential,
    sub_uid: str,
    sub_label: str,
    bind_ip: str,
    prefix: str,
    dry_run: bool,
) -> None:
    sub_prefix = f"{prefix} sub={sub_label}({sub_uid})"
    sub_api_key: str | None = None
    coins: list[tuple[str, Decimal]] = []

    if dry_run:
        _log(sub_prefix, f"dry-run create_api_key ips=[{bind_ip}] perms={SUB_KEY_PERMISSIONS}")
        _log(sub_prefix, "dry-run flatten + UNIFIED->FUNDING (requires live sub key)")
        _log(sub_prefix, "dry-run revoke_api_key")
        _log(sub_prefix, "dry-run FUNDING->master UNIFIED (amounts unknown in dry-run)")
        return

    try:
        key = await connector.create_api_key(
            master_cred,
            subaccount=sub_uid,
            scheme=KeyScheme.HMAC,
            permissions=SUB_KEY_PERMISSIONS,
            read_only=False,
            ips=[bind_ip],
            note="cpanel fund sweep (temporary)",
        )
        sub_api_key = key.api_key
        _log(sub_prefix, f"created api_key={sub_api_key[:6]}…")
        sub_cred = _cred(key.api_key, key.secret)

        await flatten_account(connector, sub_cred, sub_prefix, dry_run=False)
        await transfer_wallet_coins(
            connector,
            sub_cred,
            WalletType.UNIFIED,
            WalletType.FUNDING,
            sub_prefix,
            dry_run=False,
        )
        coins = await funding_coins(connector, sub_cred)
        _log(sub_prefix, f"funding_snapshot coins={len(coins)}")
    finally:
        if sub_api_key is not None:
            try:
                await connector.revoke_api_key(master_cred, subaccount=sub_uid, api_key=sub_api_key)
                _log(sub_prefix, f"revoked api_key={sub_api_key[:6]}…")
            except Exception as exc:  # noqa: BLE001
                _log(sub_prefix, f"revoke FAILED: {type(exc).__name__}: {exc}")

    # Captured before revoke; transfer with master key.
    if coins:
        await transfer_wallet_coins(
            connector,
            master_cred,
            WalletType.FUNDING,
            WalletType.UNIFIED,
            sub_prefix,
            dry_run=False,
            from_subaccount=sub_uid,
            to_subaccount=None,
            coins=coins,
        )
    else:
        _log(sub_prefix, "no funding coins to pull to master")


async def sweep_master(
    row: dict[str, str],
    bind_ip: str,
    dry_run: bool,
    exclude_uids: frozenset[str],
) -> None:
    venue = row["venue"]
    prefix = f"[{venue}] key={row['api_key'][:6]}…"
    connector = BybitConnector(
        bucket=TokenBucket(rate=5, capacity=10),
        venue=venue,
    )
    master_cred = _cred(row["api_key"], row["api_secret"])

    _log(prefix, f"endpoint=api.{connector._domain}.{connector._tld} dry_run={dry_run}")
    await connector.fetch_instruments()

    info = await connector.verify_credential(master_cred)
    if not info.valid:
        _log(prefix, f"INVALID credential: {info.reason}")
        return
    if not info.is_master:
        _log(prefix, f"SKIP not a master key (uid={info.native_uid})")
        return
    _log(prefix, f"master uid={info.native_uid}")

    await flatten_account(connector, master_cred, prefix, dry_run)

    try:
        subs = await connector.list_subaccounts(master_cred)
    except Exception as exc:  # noqa: BLE001
        _log(prefix, f"list_subaccounts FAILED: {type(exc).__name__}: {exc}")
        return
    _log(prefix, f"subaccounts={len(subs)} exclude={sorted(exclude_uids)}")

    for sub in subs:
        if sub.native_uid in exclude_uids:
            _log(prefix, f"SKIP excluded sub={sub.label}({sub.native_uid})")
            continue
        try:
            await sweep_subaccount(
                connector,
                master_cred,
                sub.native_uid,
                sub.label,
                bind_ip,
                prefix,
                dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            _log(
                prefix,
                f"sub={sub.label}({sub.native_uid}) FAILED: {type(exc).__name__}: {exc}",
            )

    if not dry_run:
        await asyncio.sleep(_POST_FLATTEN_SLEEP_S)
    await transfer_wallet_coins(
        connector,
        master_cred,
        WalletType.UNIFIED,
        WalletType.FUNDING,
        prefix,
        dry_run,
    )
    _log(prefix, "done")


async def run(
    masters: list[dict[str, str]],
    bind_ip: str,
    dry_run: bool,
    exclude_uids: frozenset[str],
) -> None:
    _log(
        "[sweep]",
        f"masters={len(masters)} bind_ip={bind_ip} dry_run={dry_run} "
        f"exclude_uids={sorted(exclude_uids)}",
    )
    for row in masters:
        try:
            await sweep_master(row, bind_ip, dry_run, exclude_uids)
        except Exception as exc:  # noqa: BLE001
            _log(
                f"[{row['venue']}] key={row['api_key'][:6]}…",
                f"FAILED: {type(exc).__name__}: {exc}",
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep Bybit master/sub funds to Funding")
    parser.add_argument("--input", required=True, type=Path, help="JSON list of master credentials")
    parser.add_argument(
        "--ip",
        default=DEFAULT_BIND_IP,
        help=f"IP bound on temporary sub API keys (default {DEFAULT_BIND_IP})",
    )
    parser.add_argument(
        "--exclude-uid",
        action="append",
        default=[],
        metavar="UID",
        help=(
            "Sub UID to skip (repeatable). Defaults to "
            f"{sorted(DEFAULT_EXCLUDE_SUB_UIDS)} when omitted"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List actions without cancelling, trading, transferring, or creating keys",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if not args.input.is_file():
        raise SystemExit(f"input file not found: {args.input}")
    masters = load_masters(args.input)
    exclude = (
        frozenset(str(u).strip() for u in args.exclude_uid if str(u).strip())
        if args.exclude_uid
        else DEFAULT_EXCLUDE_SUB_UIDS
    )
    asyncio.run(
        run(masters, bind_ip=args.ip, dry_run=args.dry_run, exclude_uids=exclude)
    )


if __name__ == "__main__":
    main()
