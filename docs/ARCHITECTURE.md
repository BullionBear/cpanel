# CPanel — Architecture

This document describes *how* the goals in [DESIGN.md](DESIGN.md) are achieved
conceptually: the moving parts, how data flows between them, and the
per-exchange strategies needed where venues behave differently. It stays at
the level of components and data concepts, not code.

## System Overview

```
                        ┌─────────────────────────────┐
   Operator ──► UI ──►  │          API layer          │ ◄── Agent (LLM runtime)
                        └──────────────┬──────────────┘
                                       │ reads / commands
                        ┌──────────────▼──────────────┐
                        │          Database            │
                        │  (system of record + queue)  │
                        └──────────────▲──────────────┘
                                       │ writes
                        ┌──────────────┴──────────────┐
                        │   Scheduler + sync workers   │
                        └──────────────┬──────────────┘
                                       │ via connectors
                 ┌─────────────┬───────┴──────┬─────────────┐
                 │  Binance    │    Bybit     │   (more…)   │
                 │  connector  │   connector  │             │
                 └─────────────┴──────────────┴─────────────┘
                              Exchange APIs
```

Five kinds of components:

1. **API layer** — serves the UI and the agent. Handles synchronous commands
   (place order, start transfer, create subaccount) and queries against the
   local database.
2. **Exchange connectors** — one adapter per exchange implementing a common
   interface. All exchange-specific quirks (naming, pagination, rate limits,
   endpoint shapes) live here and nowhere else.
3. **Scheduler + sync workers** — background machinery that keeps the local
   database in sync with the exchanges: instrument refresh, history ingestion,
   snapshots, order-state polling, transfer progress tracking.
4. **Database** — the system of record for everything synced or initiated
   through the panel, plus the sync bookkeeping (cursors, job state).
5. **Agent runtime** — an LLM loop whose tools are the same API operations the
   UI uses.

The guiding rule: **reads are served from the local database; writes go
through connectors to the exchange and are then confirmed back into the
database by sync.** The UI never queries an exchange directly in the request
path, which keeps the panel fast and insulates it from exchange rate limits
and outages.

## Exchange Connector Layer

Every connector implements the same conceptual interface:

- `fetch_instruments()` — all tradable instruments with native filters
- `fetch_balances(account)`, `fetch_positions(account)`
- `place_order / amend_order / cancel_order`
- `fetch_open_orders(account)`, `fetch_order(account, id)`
- `fetch_trades(account, cursor)` — incremental trade history
- `fetch_orders_history(account, cursor)` — incremental order history
- `create_subaccount / create_api_key / revoke_api_key`
- `transfer(account, …)`, `withdraw / fetch_deposits / fetch_withdrawals`
- `fetch_reference_prices()`

Two important properties:

- **Normalization at the boundary.** Connectors accept and return universal
  symbols and normalized enums (side, order type, order status, wallet type).
  Translation to native symbols/enums happens inside the connector. Nothing
  above the connector layer knows Bybit calls perps "linear".
- **Capability declaration.** Not every venue supports every operation (e.g.
  not all exchanges allow API-driven key creation). Each connector declares
  its capabilities so the API layer and the agent can refuse unsupported
  requests up front instead of failing at the venue.

Each connector also owns a **rate-limit budget** for its venue: all outgoing
requests for that exchange pass through a shared limiter so background sync
never starves interactive operations. Interactive requests (order placement)
get priority over sync traffic.

## Scheduler

A single scheduler triggers recurring jobs; workers execute them. Jobs are
defined per `(job type, exchange, account)` so that one slow exchange or one
account with a huge backlog never blocks the others.

Representative schedule (frequencies are tunable):

| Job | Frequency | Scope |
|---|---|---|
| Instrument refresh | hourly | per exchange |
| Trade history sync | minutes | per account (strategy varies, see below) |
| Order history sync | minutes | per account |
| Open-order poll | seconds–minutes | per account with open orders |
| Balance snapshot | hourly + on demand | per account |
| Position snapshot | hourly + on demand | per account |
| Transfer progress check | minutes while a transfer is pending | per transfer |
| Reference price refresh | minutes | per exchange |
| Reconciliation pass | daily + on demand | per account |

Job runs are recorded (started, finished, error, items ingested) so sync
health is observable in the panel: the operator can see "Bybit trade sync for
account X last succeeded 2 minutes ago" and be alerted when a job keeps
failing.

All sync jobs are **idempotent**: re-running one must never duplicate data.
This falls out of the upsert-by-native-ID rule described below.

## Symbol Unification

### Data concept

A `universal_ticker` record per instrument:

- `universal_symbol` (e.g. `BYBIT_PERP_ETHUSDT`) — stable primary identifier
- `exchange`, `market_type`, `native_symbol` — the mapping back to the venue
- Normalized filters: price tick / decimals, quantity step / decimals,
  min/max quantity, min notional
- Contract metadata where applicable: contract size, settlement asset,
  expiry, option strike/type
- `status`: active, halted, delisted
- `first_seen_at`, `last_seen_at` — maintained by the refresh job

### Refresh flow

The instrument refresh job runs hourly per exchange:

1. Connector fetches the venue's full instrument list and normalizes it.
2. New instruments are inserted; changed filters are updated (filters do
   change — exchanges retune tick sizes and minimums).
3. Instruments that disappear from the venue's list are **not deleted** — they
   are marked delisted. History and old orders still reference them, so
   universal symbols are never removed or reused.

Everything downstream (orders, trades, snapshots, the agent) references
instruments by universal symbol, so a single refresh job keeps the whole
system's view of tradable markets current.

## Accounts and Credentials

### Data concept

- `account` — one exchange account: exchange, label, main-or-subaccount, and
  parent linkage for subaccounts.
- `api_credential` — a key attached to an account: scheme (HMAC, Ed25519),
  permissions, status (active, revoked), and the encrypted secret material.
  Secrets are encrypted at rest with a key held outside the database and are
  decrypted only at request-signing time or through an explicit, **audited**
  reveal endpoint (the operator can retrieve a stored secret on demand; every
  reveal is written to the audit log). Secrets are never returned by any
  routine read (account tree, credential listing).

### Provisioning flow

Subaccount and key creation are connector operations like any other: the API
layer validates the request, the connector calls the venue, and the resulting
account/credential rows are stored. For the newly created key this is the one
moment the secret exists in plaintext; it is encrypted and stored immediately.
Bulk operations ("create 10 subaccounts, each with two keys") are executed as
a sequence of these primitives with per-item success/failure reporting, so a
partial failure is visible and resumable rather than all-or-nothing.

## Order Management

### Placement path (synchronous)

1. API layer receives the order in universal terms (universal symbol, side,
   type, price, quantity).
2. **Local validation** against the `universal_ticker` filters: round/reject
   on price tick, quantity step, min quantity, min notional. Guardrails
   (max notional, symbol allow-list) are enforced here too.
3. An `order` row is written first with a panel-generated **client order ID**
   and state `submitting`. The client order ID is sent to the exchange, which
   makes placement idempotent: if the response is lost, the order can be
   found again by that ID instead of accidentally re-sent.
4. Connector places the order; the exchange's native order ID and initial
   status are recorded.

### State tracking (asynchronous)

Order rows advance through a normalized lifecycle
(`submitting → open → partially_filled → filled / cancelled / rejected`)
driven by two complementary sources:

- **Polling** of open orders per account — the baseline that always works.
- **Exchange event streams** (user-data websockets) where available — lower
  latency, but treated as an optimization: the poller remains the source of
  truth-catch-up, so a dropped stream never causes lost updates.

Orders placed *outside* the panel are discovered by the same polling/history
sync and appear in the panel marked as externally originated.

## History Ingestion and Cursors

This is the part where exchanges differ the most, so the design separates the
*generic* cursor mechanism from *per-exchange* query strategies.

### Generic mechanism

Two append-style tables, `trade_history` and `order_history`, both keyed by
`(exchange, account, native ID)` and written by **upsert** — re-fetching an
overlapping window can never create duplicates.

A `sync_cursor` table records ingestion progress:

- `account`, `data kind` (trades / orders), `stream key` (see below)
- `cursor value` — whatever the venue paginates by: a trade ID, a timestamp,
  or an opaque pagination token
- `last_synced_at`, error state

Each sync job run: load cursor → fetch the next page(s) through the connector
→ upsert rows → advance the cursor **only after** the rows are stored. A crash
between fetch and store simply re-fetches the same page next run.

### Per-exchange query strategies

The `stream key` exists because venues shard their history endpoints
differently:

- **Binance-style (per-symbol queries).** Trade history must be queried one
  symbol at a time. One cursor per `(account, symbol)` stream. The set of
  symbols to sync is not "every listed instrument" (that would waste the rate
  budget on thousands of empty queries) — it is derived from activity: symbols
  the account has ordered through the panel, symbols seen in order history,
  and symbols with open positions. A low-frequency discovery pass widens the
  set if activity appears elsewhere.
- **Bybit-style (account-wide queries).** Trade history for the whole account
  comes from a single paginated endpoint. One cursor per account, paginated by
  the venue's token or by time window.
- **Time-window venues.** Some endpoints only accept bounded time ranges
  (e.g. max 7 days per request). The strategy walks fixed windows from the
  cursor timestamp forward, and the cursor is the end of the last fully
  ingested window.

The connector declares which strategy it uses; the sync worker is generic and
just executes "fetch next page for this stream" until it reaches the present.

### Backfill vs. incremental

When an account is first connected, a **backfill** walks history backwards (or
from the earliest date the venue retains) using the same mechanism, tracked as
its own stream so it can run slowly in the background without blocking the
forward incremental sync. Once backfill meets the incremental cursor, the
account's history is complete and only incremental sync remains.

## Asset Snapshots

Balance and position snapshots are deliberately simple: on schedule (and on
demand), fetch the current state through the connector and append it.

- `balance_snapshot`: account, wallet type, asset, free/locked/total, taken_at
- `position_snapshot`: account, universal symbol, direction, size, entry
  price, mark price, unrealized PnL, margin, taken_at

Append-only history answers both "what do we hold now" (latest snapshot per
account) and "what did the book look like last Tuesday" (snapshot at time).
Valuation in a common quote asset uses reference prices captured close to the
snapshot time. All snapshots for an account are stamped with the same capture
batch so a cross-exchange portfolio view compares like with like.

## Transfer Orchestration

Transfers are modeled as a **state machine per transfer**, because unlike an
order, a transfer can span multiple venue operations and minutes-to-hours of
settlement.

- **Intra-exchange** (main ↔ sub, wallet ↔ wallet): usually a single connector
  call; states are `requested → submitted → confirmed / failed`.
- **Cross-exchange**: decomposed into steps executed and tracked in order:
  1. Resolve the deposit address for the destination account (asset +
     network), through the destination connector.
  2. Validate the pair: matching network, amount above the source venue's
     withdrawal minimum, expected fee.
  3. Submit the withdrawal on the source venue.
  4. Poll withdrawal status until the venue reports it sent (and records the
     transaction hash where applicable).
  5. Poll the destination venue's deposit history until the matching deposit
     (by asset, amount, network, and tx reference) is credited.
  6. Mark the transfer complete, recording fees actually charged.

Every step transition is persisted, so a transfer interrupted by a restart
resumes from its last confirmed step. A transfer that stalls (e.g. deposit
never credits) surfaces as *stuck* for operator attention rather than
silently pending forever. Deposit-address resolution deserves care: addresses
are re-fetched per transfer rather than cached long-term, and the
asset/network pair is validated on both sides — this is the one flow where a
mistake loses funds outright.

## Agent

The agent is an LLM tool-calling loop layered on the same API surface:

- **Tools = panel operations.** Each tool corresponds to an existing API
  operation (query symbols, query balances, place order, create subaccount,
  start transfer, …). The agent has no other channel, so guardrails, audit,
  and validation apply automatically.
- **Plan / confirm / execute.** For state-changing requests the agent first
  produces a concrete plan — the literal list of orders with prices and
  quantities, or the list of subaccounts and keys to create — shown to the
  user for confirmation. Read-only requests execute directly.
- **Deterministic math outside the model.** For requests like "limit buys
  from 10% to 15% below, evenly", the LLM chooses parameters (reference
  price source, ladder count, total size) but the ladder itself — price
  levels, per-level quantity, rounding to tick/step, filter checks — is
  computed by ordinary code the agent calls. The model never does the
  arithmetic that determines what is sent to an exchange.
- **Bulk execution with per-item reporting.** Multi-step plans (10
  subaccounts × 2 keys) execute item by item; the agent reports exactly
  which succeeded and which failed, and can retry only the failures.
- **Attribution.** Every action carries the agent conversation as its actor
  in the audit trail, alongside the user who confirmed the plan.

## Reconciliation

A periodic pass per account compares local state with the venue:

- Open orders the panel thinks exist vs. the venue's open-order list —
  mismatches are corrected from the venue and flagged.
- Latest balance snapshot vs. a fresh fetch — a drift beyond tolerance
  triggers an immediate snapshot and an alert.
- Gaps in history streams (e.g. a trade referencing an order the panel has
  never seen) trigger a targeted re-sync of the affected window.

The exchange always wins; reconciliation updates local state and records that
it did so, it never "corrects" the exchange.

## Audit Trail

Every state-changing command through the API layer writes an append-only
audit record before and after execution: actor (user or agent conversation),
operation, parameters, target account, outcome, and timestamps. Background
sync does not audit routine ingestion, but does record job runs (see
Scheduler), so both "who did what" and "what did the system do on its own"
are answerable.

## Failure Model — Summary

The recurring principles that make the system safe to run unattended:

- **Idempotency everywhere**: client order IDs for placement, upsert-by-native
  ID for ingestion, cursors advanced only after storage, transfer steps
  persisted before execution.
- **Poll as truth, stream as optimization**: no data path depends on a
  websocket staying up.
- **Per-exchange isolation**: one venue's outage or rate-limit exhaustion
  degrades only that venue's sync; jobs are scoped per exchange/account.
- **Fail visible, not silent**: stuck transfers, failing sync jobs, and
  reconciliation drift surface in the panel instead of being retried
  invisibly forever.
