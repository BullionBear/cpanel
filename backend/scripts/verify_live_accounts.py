"""One-off Phase 2 live verification against archived exchange keys.

Reads keys from the archived table, exercises the real connectors
(verify_credential + list_subaccounts), and prints only non-secret,
normalized results. Secrets never leave the process.

Run on the IP-bound host:
    DB_DSN=... python verify_live_accounts.py
"""
import asyncio
import os

import psycopg
from app.connectors.binance import BinanceConnector
from app.connectors.bybit import BybitConnector
from app.core.ratelimit import TokenBucket

DSN = os.environ["DB_DSN"]

CONNECTORS = {
    "binance": lambda: BinanceConnector(bucket=TokenBucket(rate=5, capacity=10)),
    "bybit": lambda: BybitConnector(bucket=TokenBucket(rate=5, capacity=10)),
}


def load_keys():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, a.name, a.api_type, e.slug, a.api_key, a.api_secret,
                   acct.uid, acct.parent_uid
            FROM api_archived_20260713 a
            JOIN ref_exchange_archived_20260713 e ON e.id = a.exch_id
            JOIN account_archived_20260713 acct ON acct.id = a.acct_id
            WHERE e.slug IN ('binance', 'bybit')
            ORDER BY e.slug, a.id
            """
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


async def main():
    rows = load_keys()
    print(f"Loaded {len(rows)} binance/bybit keys\n")
    connectors = {slug: factory() for slug, factory in CONNECTORS.items()}

    for row in rows:
        slug = row["slug"]
        connector = connectors[slug]
        is_master = row["uid"] == row["parent_uid"]
        cred = {
            "api_key": row["api_key"],
            "secret": row["api_secret"],
            "scheme": row["api_type"],
        }
        label = f"[{slug}] {row['name']} ({row['api_type']})"
        try:
            info = await connector.verify_credential(cred)
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: ERROR {type(exc).__name__}: {exc}")
            continue

        if not info.valid:
            print(f"{label}: INVALID (venue rejected)")
            continue

        perms = ",".join(sorted(info.permissions or {})) if isinstance(info.permissions, dict) else info.permissions
        print(
            f"{label}: VALID uid={info.native_uid} master={info.is_master} "
            f"read_only={info.read_only} trade={info.can_trade} withdraw={info.can_withdraw}"
        )

        if is_master:
            try:
                subs = await connector.list_subaccounts(cred)
                names = ", ".join(f"{s.label}({s.native_uid})" for s in subs[:10])
                print(f"    subaccounts ({len(subs)}): {names}")
            except Exception as exc:  # noqa: BLE001
                print(f"    subaccount list ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
