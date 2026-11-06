# CPanel — Implementation Plan

This plan turns [DESIGN.md](DESIGN.md) and [ARCHITECTURE.md](ARCHITECTURE.md)
into an ordered build sequence. Modules are dependent, so phases are arranged
so that each one only relies on what earlier phases delivered, and each phase
ends in something usable and verifiable on its own.

## Ground Rules

### Exchange client policy

For each exchange, use the **official Python package** if one exists; only
implement our own client when there is none (or the official one is
unusable for a required endpoint):

| Exchange | Client |
|---|---|
| Binance | `binance-connector` (official) |
| Bybit | `pybit` (official) |
| Others (as added) | Official package if available; otherwise a thin in-house REST client |

Two consequences of this policy:

- The official package is an implementation detail *inside* the connector.
  Nothing outside a connector imports the exchange package, so a venue whose
  official SDK is later abandoned can be swapped to an in-house client
  without touching any other module.
- Official packages return native shapes and enums. Each connector owns the
  normalization to universal symbols and normalized enums, exactly as
  ARCHITECTURE.md prescribes — the SDK does the transport, we do the meaning.

When we must implement a client ourselves, it stays deliberately thin:
request signing, rate-limit awareness, pagination — no abstraction beyond
what the connector interface needs.

### Starting point

The repo already has a FastAPI backend scaffold with Discord OAuth
(`backend/app`), a SvelteKit frontend with login flow (`frontend/src`), an
Alembic pipeline with an empty model registry (`migration/models`), and
CI/CD to production. The plan builds inside this structure:

```
backend/app/
├── api/           routers (one per module)
├── connectors/    base interface + one package per exchange
├── models/        SQLAlchemy models (mirrored into migration/models)
├── services/      module logic (symbols, accounts, orders, …)
├── jobs/          scheduler + job implementations
└── core/          config, security, db session, encryption
```

Each phase includes its Alembic migration, API endpoints, and the minimal
frontend page to operate it. Tests: connectors get recorded-response tests
(no live keys in CI); services get unit tests against a test database;
anything touching real venues is verified manually on testnet/small amounts
first.

## Phase 0 — Foundations

Everything later depends on this plumbing, none of which is exchange-specific.

- **Database layer** in the backend: async SQLAlchemy session, model base,
  and the convention for keeping `migration/models` in sync so Alembic
  autogenerates from the same metadata.
- **Connector interface**: the abstract base class with the full method
  surface from ARCHITECTURE.md (instruments, balances, orders, trades,
  transfers, provisioning) plus the **capability declaration** so partial
  implementations are honest from day one. Ship it with a fake in-memory
  connector used by tests.
- **Scheduler skeleton**: an async job runner inside the backend process —
  job registry, per-`(job, exchange, account)` scoping, run records
  (started / finished / error / items), and a panel endpoint to view job
  health. Individual jobs arrive in later phases.
- **Rate limiter**: per-exchange budget shared by all outgoing calls, with
  interactive-over-sync priority.
- **Audit trail**: append-only `audit_log` table and a decorator/dependency
  that every state-changing endpoint uses from the start. Retrofitting audit
  later never happens; it goes in first.
- **Secret encryption**: envelope encryption for credential material with the
  key from environment/config, plus the "decrypt only at signing time" rule
  encoded in the credential service.

**Done when:** migrations run in CI, the fake connector passes the interface
test suite, a demo job runs on schedule and its runs are visible via API.

## Phase 1 — Symbol Unification

First real module because every other module references universal symbols.

- `universal_ticker` model with normalized filters, contract metadata,
  status, first/last seen.
- Universal symbol codec: parse/build `<EXCHANGE>_<MARKET_TYPE>_<PAIR>`,
  the normalized `market_type` enum (SPOT, PERP, CMARGIN, DELIVERY, OPTION),
  and validation helpers.
- **Binance connector v1** (`binance-connector`) and **Bybit connector v1**
  (`pybit`): public endpoints only — `fetch_instruments` and
  `fetch_reference_prices`, with full native→universal mapping (Bybit
  "linear"→PERP, "inverse"→CMARGIN, Binance spot/USD-M/COIN-M surfaces).
- Instrument refresh job (hourly, per exchange): upsert, filter-change
  update, delist-marking. Reference price refresh job.
- API: list/search/get symbols with filters. Frontend: symbol browser page.

**Done when:** both venues' full instrument lists are in the database with
correct filters spot-checked against the venues' own UIs, refresh runs
hourly, and a delisted test case is marked rather than deleted.

## Phase 2 — Accounts and Credentials

Private API access; prerequisite for every remaining phase.

- `account` and `api_credential` models (hierarchy, scheme, permissions,
  status, encrypted secret).
- Register existing accounts/keys through the panel; connectors gain
  authenticated request support (HMAC first; Ed25519 for venues that use it).
- Credential verification: on registration, call a harmless authenticated
  endpoint to confirm the key works and detect its actual permissions.
- **Provisioning**: subaccount creation and API key create/rotate/revoke via
  connector methods, capability-gated per venue. Bulk provisioning as a
  sequence of primitives with per-item outcome reporting.
- Frontend: accounts page — hierarchy view, key status, provisioning forms.

**Done when:** a real (testnet or low-value) Binance and Bybit account is
registered, its key verified, and a subaccount + key pair can be created from
the panel end to end.

## Phase 3 — Asset Snapshots

Deliberately before orders: it exercises authenticated reads across every
account with zero risk, proving Phase 2 plumbing before we let anything trade.

- `balance_snapshot` and `position_snapshot` models (append-only, capture
  batch stamp).
- `fetch_balances` / `fetch_positions` in both connectors across wallet
  types (spot, margin, derivatives).
- Snapshot jobs (hourly + on-demand endpoint), valuation in a common quote
  asset via reference prices.
- Frontend: portfolio page — current holdings per account/exchange, position
  table, simple history view from past snapshots.

**Done when:** hourly snapshots accumulate for all registered accounts and
the panel's totals match the venues' own dashboards.

## Phase 4 — Order Management

The first state-changing trading feature.

- `order` model with normalized lifecycle and panel-generated client order
  IDs; externally-originated flag.
- Placement path: validation against `universal_ticker` filters →
  guardrail checks (max notional, symbol allow-list — the guardrail config
  lands here) → write `submitting` row → connector call → record native ID.
- Cancel and amend, single and bulk.
- Open-order polling job per account with open orders; status transitions
  driven from poll results. (User-data websockets are explicitly deferred —
  polling is the source of truth per the architecture; streams are a later
  optimization.)
- Frontend: order ticket (with live filter validation), open orders table
  across all accounts, cancel controls.

**Done when:** a limit order placed from the panel on testnet reaches the
venue, fills or cancels, and the panel's lifecycle matches the venue's; a
crash between submit and response does not double-place (client order ID
recovery verified).

## Phase 5 — History Ingestion

Needs orders (Phase 4) to have something to reconcile against, and accounts
(Phase 2) for access.

- `trade_history` / `order_history` models keyed by
  `(exchange, account, native_id)`, upsert-only writes.
- `sync_cursor` model with the stream-key concept.
- Generic sync worker: load cursor → fetch page via connector → upsert →
  advance cursor after storage.
- Per-exchange strategies:
  - **Binance**: per-`(account, symbol)` streams; active-symbol set derived
    from panel orders, order history, and open positions; low-frequency
    discovery pass.
  - **Bybit**: account-wide paginated stream per data kind.
- Backfill as a separate backward-walking stream per account, throttled below
  incremental sync, converging with the incremental cursor.
- Frontend: history explorer — filter by exchange, account, symbol, time
  range; per-stream sync health.

**Done when:** for a test account with prior activity, backfill completes,
incremental sync keeps up within its scheduled cadence, re-running any job
produces zero duplicates, and fills for panel-placed orders from Phase 4
appear linked to their orders.

## Phase 6 — Transferring

Last of the "money moves" features because it is the most dangerous and
builds on accounts, balances, and history (deposit matching).

- `transfer` model as a persisted state machine with step records.
- **Intra-exchange first**: main↔sub and wallet↔wallet via single connector
  calls; confirm against balance changes.
- **Cross-exchange second**: deposit-address resolution (fetched per
  transfer, never long-cached), asset/network validation on both sides,
  withdrawal submission, withdrawal-status polling, deposit matching by
  asset/amount/network/tx-reference, fee recording.
- Stuck-transfer detection and operator surfacing; resume-from-last-step on
  restart.
- Guardrails: max transfer size, confirmation requirement, withdrawal-enabled
  keys flagged as high-risk in the credentials UI.
- Frontend: transfer form with validation preview, transfer list with live
  step status.

**Done when:** an intra-exchange transfer and one real small cross-exchange
transfer complete end to end with every step recorded, and a deliberately
interrupted transfer resumes correctly.

## Phase 7 — Agent

Last because its tools *are* the preceding modules; nothing here touches an
exchange directly.

- Tool layer: one tool per existing API operation (query symbols/balances/
  orders/history, place order, provision accounts, start transfer), sharing
  the exact service code paths — guardrails and audit come for free.
- Deterministic helpers as ordinary code the agent must call: ladder
  computation (price levels, per-level sizing, tick/step rounding, filter
  checks), bulk-provisioning expansion. The LLM chooses parameters, never
  does the arithmetic.
- Plan / confirm / execute loop: state-changing requests produce a concrete
  itemized plan requiring user confirmation; read-only requests execute
  directly. Per-item outcome reporting and retry-failures-only for bulk
  plans.
- Attribution: agent conversation ID as actor in every audit record,
  alongside the confirming user.
- Frontend: chat panel with plan-confirmation cards.
- Validation with the two canonical scenarios from DESIGN.md: the 10–15%
  ladder order and the 10-subaccounts-with-two-key-schemes provisioning.

**Done when:** both canonical scenarios execute end to end on testnet from a
natural-language prompt, with correct plans shown before execution and full
audit attribution.

## Phase 8 — Reconciliation and Hardening

Continuous-operation quality; several pieces are deferred hooks from earlier
phases.

- Reconciliation job per account: open orders vs. venue, snapshot vs. fresh
  balances, history gap detection with targeted re-sync; exchange always
  wins, corrections logged.
- Alerting on failing jobs, stuck transfers, and reconciliation drift
  (Discord webhook already exists in the deployment for CI — reuse the
  channel pattern).
- Optional optimization pass: user-data websocket streams for order updates
  where the official packages support them, layered on top of polling.
- Operational review: rate-limit budget tuning from observed job telemetry,
  backfill throttles, snapshot retention policy.

**Done when:** the system runs for a week unattended with all jobs green, and
an order placed directly on an exchange's own UI shows up in the panel via
reconciliation/history sync without manual intervention.

## Dependency Summary

```
Phase 0  Foundations
   └─► Phase 1  Symbols            (needs: scheduler, connector base)
          └─► Phase 2  Accounts    (needs: symbols for context, encryption)
                 ├─► Phase 3  Snapshots      (needs: authenticated reads)
                 ├─► Phase 4  Orders         (needs: symbols, accounts, guardrails)
                 │      └─► Phase 5  History (needs: orders to link fills)
                 └─► Phase 6  Transfers      (needs: balances, history for matching)
                        └─► Phase 7  Agent   (needs: all tools it orchestrates)
                               └─► Phase 8  Reconciliation & hardening
```

Phases 3 and 4 can proceed in parallel once Phase 2 lands; everything else is
sequential. Each phase merges to `main` behind the existing CI/CD pipeline,
so the panel grows capability by capability in production rather than in one
big cutover.
