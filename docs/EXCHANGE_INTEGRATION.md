# CPanel — Integrating a New Exchange

This is the playbook for adding an exchange to the panel. It walks through the
four stages of an integration: analyzing the venue's API, making the design
decisions, breaking the work into ordered tasks, and verifying the result.
[ARCHITECTURE.md](ARCHITECTURE.md) explains *why* the system is shaped this
way; this document explains *what to do*.

The one rule that makes integrations tractable: **all venue-specific behavior
lives in a single connector module** (`backend/app/connectors/<exchange>.py`).
The rest of the system — services, jobs, API, frontend — only speaks the
normalized vocabulary in `backend/app/connectors/types.py` and the
`ExchangeConnector` interface in `backend/app/connectors/base.py`. If you find
yourself adding an `if exchange == ...` branch outside the connector, the
design is wrong.

---

## Stage 1 — API Schema Analysis

Before writing code, produce a **venue profile**: a written survey of how the
exchange's API answers each question below. Every question maps to a
capability or a design decision later, so gaps found here are cheap and gaps
found in production are not. Study the official API docs and, if one exists,
the official Python SDK (both Binance and Bybit connectors wrap official SDKs
rather than raw HTTP).

### 1.1 Authentication and credentials

- Signing scheme(s): HMAC-SHA256? Ed25519? RSA? (`KeyScheme` supports all
  three; Bybit is HMAC-only, Binance registers whatever the operator made.)
- What identifies a credential — api key + secret only, or also a passphrase?
- Is there a "who am I" endpoint that reports the key's permissions, its
  account uid, and whether it is read-only / can trade / can withdraw? This
  backs `verify_credential` and the `CredentialInfo` flags the UI shows.
- IP allowlisting, key expiry, and any signature timestamp/recv-window rules.

### 1.2 Market structure

- Which market surfaces exist and which do we care about? Map each onto
  `MarketType`: `SPOT`, `PERP` (USDT/USDC-margined perpetual), `CMARGIN`
  (coin-margined/inverse perpetual). Delivery futures and options are out of
  scope so far.
- Are the surfaces separate APIs with separate hosts (Binance: api/fapi/dapi)
  or one API with a `category` parameter (Bybit v5)?
- Instruments endpoint: symbol naming, tick/step/min-notional filters,
  trading status values, contract size and settle asset for inverse contracts.
- Reference prices: is there a bulk ticker endpoint per surface?

### 1.3 Wallet structure

Venues disagree here more than anywhere else, and the panel models it
explicitly: each connector declares `wallets: tuple[WalletType, ...]` — the
wallets that actually exist on that venue, in display order.

- What wallets does the venue have, and which subset does the panel need?
  (Binance: `SPOT`, `FUNDING`, `FUTURES` out of its six-plus wallets. Bybit
  UTA: just `UNIFIED` and `FUNDING`.) Extend `WalletType` only if the venue
  truly has a wallet kind no existing value describes.
- Which endpoint reports balances for each wallet, and what do "free" vs
  "locked" mean in each response?
- Which wallet do on-chain withdrawals draw from? (Bybit: FUND only.)

### 1.4 Trading

- Order placement per surface: parameter names, time-in-force values, client
  order id support (needed for idempotent recovery — see
  `fetch_order(client_order_id=...)`).
- Order status vocabulary → map to `OrderStatus` (`OPEN`,
  `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`).
- Open-order listing and single-order lookup.
- Position endpoint(s): side conventions (signed size vs side field), mark
  price, unrealized PnL, isolated vs cross margin reporting.

### 1.5 History pagination model

This decides the connector's history strategy, so nail it down precisely:

- Are trades/orders queried **per symbol** (Binance style) or **account-wide**
  (Bybit style)? This decides `history_stream_keys()`: one stream per active
  symbol, or a single `"account"` stream.
- What is the pagination primitive — `fromId`, a native `cursor` token, or
  time windows? What is the max page size and the max look-back window?
- Cursors in the panel are opaque strings owned by the connector. Confirm you
  can build one from a timestamp for backfill seeding
  (`initial_history_cursor`; default is epoch-ms, which time-windowed venues
  understand — token-cursor venues must override it).

### 1.6 Accounts and provisioning

- Can subaccounts be listed / created / frozen via API? With what credential
  (master only)?
- Can API keys be minted, modified, revoked via API — and for a *normal*
  account or only broker/institutional programs? Binance is
  **registration-only** (the operator brings keys made on the venue); Bybit is
  full self-service. This decides which provisioning capabilities you declare
  and how the accounts UI treats the venue.
- Permission model for created keys: what venue scopes exist, so panel scope
  tokens (`spot`, `derivatives`, `wallet`, …) can be mapped.

### 1.7 Transfers, withdrawals, deposits

Keep these separate: **transfer is intra-exchange only**. Moving funds off a
venue (to another exchange or an external address) is **withdrawal**, not
transfer. The panel may orchestrate a withdraw→deposit sequence when the
destination is another registered account, but the connector never has a
cross-exchange `transfer`.

- Intra-exchange transfer endpoints: wallet↔wallet on one account, and
  main↔sub universal transfers. What identifies the counterparty — email, uid?
  Does an omitted side mean "the credential's own account"?
- Is the transfer answer synchronous (confirmed on response) or does it need
  status polling?
- Asset/network catalog: per-chain withdraw/deposit enablement, minimums,
  fees (backs `fetch_asset_networks` and withdrawal validation).
- Deposit address lookup — including for subaccounts via the master key.
- Withdrawal submit + status lifecycle → map to `WithdrawalStatus`
  (`PENDING`/`SENT`/`COMPLETED`/`FAILED`); deposit records → `DepositStatus`.

### 1.8 Rate limits and errors

- Request-weight budget and per-endpoint weights; ban/backoff behavior.
- Error shape: numeric codes? String retCodes? Which codes mean "invalid
  credential" vs "insufficient balance" vs "rate limited"? These need to map
  onto the panel's credential-error handling.

**Deliverable:** the venue profile — one page per section above, with concrete
endpoint names and one captured sample response per endpoint. Those samples
become the test fixtures in Stage 4.

---

## Stage 2 — Design

With the profile in hand, make these decisions and write them down as the
module docstring of the new connector (see the tops of `binance.py` /
`bybit.py` for the expected shape).

1. **Capability set.** The exact `Capability` values the connector declares.
   Undeclared capabilities cost nothing: every base method raises
   `NotSupported`, the API layer capability-gates operations, and the UI hides
   them. Declare only what Stage 1 proved works.

2. **Wallet vocabulary.** The `wallets` tuple, and per-wallet:
   - which endpoint reports its balances,
   - how it maps to the venue's transfer `accountType`/leg codes
     (a small module-level dict, like `_UNIVERSAL_TRANSFER_LEG` /
     `_WALLET_ACCOUNT_TYPE`).

3. **Symbol mapping.** How native symbols become universal symbols
   (`EXCHANGE_MARKETTYPE_BASEQUOTE`, built by
   `connectors/symbols.build_universal_symbol`). Decide which instruments are
   skipped (delivery futures, weird quotes) and how the reverse map is warmed
   (both existing connectors build `(market_type, native_symbol) →
   universal_symbol` maps in `fetch_instruments`).

4. **History strategy.** Per-symbol streams or account stream; cursor format
   (document it in the connector — e.g. Binance uses `fromId`-based cursors,
   Bybit uses time+token); page limits; and the `initial_history_cursor`
   override if the default epoch-ms seed does not apply.

5. **Normalization tables.** Every venue enum → panel enum map as a
   module-level dict (`_WITHDRAWAL_STATUS`, `_DEPOSIT_STATUS`, order status
   maps, …). Pure functions named `normalize_*` take raw response rows and
   return normalized types — they hold all parsing logic and are what unit
   tests exercise without any network.

6. **Provisioning posture.** Full self-service, partial (list-only), or
   registration-only — and the panel scope-token → venue permission map for
   `create_api_key` if applicable.

7. **Rate budget.** Token-bucket rate/capacity for
   `rate_limiters.configure(...)` in `main.py`, derived from the venue's
   documented limits with headroom for the recurring jobs (instrument refresh
   hourly, prices every 5 min, snapshots hourly, open-order poll every 30 s,
   history sync every 2 min).

Keep the design reviewable: a short doc or PR description listing the seven
decisions is enough. Anything that later surprises you belongs back in the
connector docstring.

---

## Stage 3 — Task Breakdown

Implement in this order; each step is testable on its own and the system runs
(with fewer capabilities) after every step. File paths are the actual touch
points.

### Step 0 — Skeleton and registration

- `backend/app/connectors/<exchange>.py`: class with `exchange`, an initially
  small `capabilities` set, `wallets`, and the rate-limited `_call` plumbing
  (copy the shape from an existing connector).
- `backend/app/main.py`: add the exchange to `EXCHANGES`, configure its rate
  bucket in `setup_connectors()`, register the connector instance.
- Frontend hardcoded exchange lists (until these are driven by the
  capabilities endpoint): the filter dropdowns in
  `frontend/src/routes/history/+page.svelte` and
  `frontend/src/routes/symbols/+page.svelte`, and the per-exchange sub-nav in
  `frontend/src/routes/accounts/+layout.svelte`.

### Step 1 — Market data (`INSTRUMENTS`, `REFERENCE_PRICES`)

- `fetch_instruments` + `normalize_instrument` per surface; warm the
  native↔universal symbol maps.
- `fetch_reference_prices` from bulk tickers.
- This alone makes the symbols page and price-dependent guardrails work.

### Step 2 — Credentials and accounts (`CREDENTIAL_VERIFY`, `SUBACCOUNT_LIST`, …)

- `verify_credential` → `CredentialInfo` with `can_trade` / `can_withdraw` /
  `read_only` flags (the UI marks withdrawal-enabled keys high-risk).
- Subaccount listing (and create/freeze + key provisioning if the venue
  supports it, with the scope-token map).
- Registration flows in the accounts UI now work end to end.

### Step 3 — Account state (`BALANCES`, `POSITIONS`)

- One `_fetch_*` + `normalize_*` pair per wallet/surface; `fetch_balances`
  concatenates them. Every balance carries its `WalletType`.
- `fetch_positions` across derivative surfaces.
- The snapshot job (`jobs/snapshots.py`) and portfolio page now cover the
  venue with no further changes.

### Step 4 — Orders (`ORDERS`)

- `place_order`, `cancel_order`, `fetch_open_orders`, `fetch_order`
  (including `client_order_id` lookup — the order service relies on it for
  crash recovery).
- Add an order-ticket spec for the venue's surfaces in
  `frontend/src/lib/orderTickets/registry.ts`.

### Step 5 — History (`TRADE_HISTORY`, `ORDER_HISTORY`)

- `fetch_trades` / `fetch_orders_history` implementing the Stage 2 cursor
  design, plus `history_stream_keys` / `initial_history_cursor` overrides as
  decided.
- The history sync job backfills `DEFAULT_HISTORY_BACKFILL` (3 days) for new
  accounts automatically.

### Step 6 — Money movement (`TRANSFERS`, `WITHDRAWALS`, `DEPOSITS`)

- `transfer` for **intra-exchange** moves only (wallet-leg maps and main↔sub
  semantics). Do not implement or declare anything cross-venue here.
- `fetch_asset_networks`, `fetch_deposit_address` (master-for-sub included),
  `withdraw`, `fetch_withdrawal`, `fetch_deposits` — these are what get funds
  off / onto the venue (including when the panel later matches a deposit on
  another exchange).
- The transfer state machine (`services/transfers.py`), the progress job, and
  the Transfers UI need **no changes** — they only consume the interface.

### Step 7 — Migration (only if needed)

Connectors normally need no schema changes. You need an Alembic migration in
`migration/alembic/versions/` only if the integration adds vocabulary (a new
`WalletType` value that old rows should be relabeled to, a new column, …).

---

## Stage 4 — Verification

Three layers, cheapest first. All of it must pass before the exchange is
enabled for real accounts.

### 4.1 Unit tests (no network)

`backend/tests/test_<exchange>_connector.py`, mirroring the existing files:

- **Normalization tests** — feed the captured Stage 1 sample responses to
  every `normalize_*` function and assert the normalized output: zero
  balances dropped, locked/free math, status maps for every enum value,
  skipped instruments, position sides.
- **Request-shaping tests** — monkeypatch the connector's `_call` (or the
  private `_fetch_*`/`_submit_*` helpers) and assert the venue call is built
  correctly: transfer type codes, wallet accountTypes, main↔sub id
  resolution, cursor round-trips across pages, foreign wallet types rejected.
- Run: `python -m pytest tests/test_<exchange>_connector.py -q`.

Service- and API-level behavior (transfer state machine, order lifecycle,
snapshot capture) is already covered by the `FakeConnector` suites — do not
duplicate it per venue. If the new venue forced an interface change, extend
`connectors/fake.py` and the shared suites instead.

### 4.2 Testnet / paper smoke test

If the venue has a testnet, point the connector's hosts at it (config, not
code edits, where possible) and run through the panel UI:

1. Register the account + credential; confirm verify shows correct flags.
2. Wait one scheduler cycle (or trigger manually): instruments and prices
   appear on the symbols page.
3. Snapshot: balances show under the right wallets; positions correct.
4. Place a tiny limit order, see it in open orders, cancel it; check the
   audit log entries.
5. History page shows the trade/order after the next sync.
6. Intra-exchange transfer between two wallets; then main→sub if supported.

### 4.3 Live verification with minimal funds

Testnets do not cover withdrawals/deposits realistically. With a real account
holding a token amount of funds and `TRANSFER_MAX_VALUE` set low:

1. `POST /transfers/withdraw/preview` — validation output sane (networks,
   minimums, fees). Optionally preview a panel-orchestrated move into another
   registered account (`POST /transfers/preview`); that path is still a
   withdrawal on this venue, not a transfer.
2. One small withdrawal end to end with `confirm=true`; verify fee/tx
   recorded. If the destination is another panel account, watch the step
   records (`resolve_address → validate → submit_withdrawal →
   await_withdrawal → await_deposit`) and the deposit match on the receiving
   venue.
3. Leave the scheduler running for a day and check `job_run` for rate-limit
   errors; adjust the token bucket if the venue complains.

### 4.4 Sign-off checklist

- [ ] Full backend suite green (`python -m pytest -q`), including the new
      connector tests.
- [ ] `npx svelte-check` clean after frontend touch points.
- [ ] Every declared capability exercised at least once against the venue
      (testnet or live); every undeclared one confirmed hidden in the UI.
- [ ] Connector docstring documents the venue quirks discovered on the way
      (the next person will hit them too).
- [ ] Rate budget reviewed after 24 h of scheduled jobs.
- [ ] No `if exchange == ...` leaked outside the connector module.
