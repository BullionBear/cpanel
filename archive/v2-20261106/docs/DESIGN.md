# CPanel — Design

## Purpose

CPanel is a unified trading operations panel for algorithmic trading. Running an
algo trading operation means dealing with many exchanges, each with its own
symbol conventions, account hierarchies, API key schemes, order semantics, and
transfer rules. CPanel consolidates all of that behind one consistent interface
so that an operator — or an AI agent acting on the operator's behalf — can
manage symbols, accounts, orders, assets, and fund movement across every venue
from a single place.

The panel is the operational control layer that sits alongside trading
strategies: strategies decide *what* to trade, CPanel handles everything around
*how* trading happens — account plumbing, order routing, bookkeeping, and fund
logistics.

## Core Modules

### 1. Symbol Unification

Every exchange names and structures its markets differently: Bybit calls
USDT-margined perpetuals "linear", Binance splits COIN-margined contracts into
its own API surface, options venues use their own conventions again. Symbol
Unification defines a single universal ticker format that identifies any
tradable instrument unambiguously across venues:

```
<EXCHANGE>_<MARKET_TYPE>_<PAIR>

BINANCE_SPOT_BTCUSDT      Binance spot BTC/USDT
BYBIT_PERP_ETHUSDT        Bybit USDT-margined perpetual (Bybit "linear")
BINANCE_CMARGIN_SOLUSD    Binance COIN-margined SOL/USD
```

The scheme covers spot, USDT/USDC-margined perpetuals, COIN-margined
contracts, delivery futures, and options.

Each universal symbol carries its normalized trading filters and metadata:

- Price precision (tick size / price decimals)
- Quantity precision (step size / quantity decimals)
- Minimum and maximum order quantity
- Minimum notional value
- Contract specifics where applicable (contract size, settlement asset, expiry)
- Trading status (active, delisted, in maintenance)

All other modules — orders, history, assets, the agent — speak in universal
symbols. Exchange-native names only exist at the boundary with each venue.

### 2. Account Management

Connects the panel to exchange accounts and manages the full account
hierarchy:

- Register exchange accounts (main accounts and subaccounts) in the panel
- Create and manage subaccounts directly from the panel
- Create, rotate, and revoke API keys, including different key schemes
  (e.g. HMAC and Ed25519 keys)
- Track key permissions (read-only, trade, withdraw) and their scope
- Store credentials securely; keys are usable by the panel without being
  re-exposed to the operator

The goal is that account provisioning — which is normally a tedious manual
process across several exchange web consoles — becomes a single panel
operation.

### 3. Order Management

A single order interface across all connected venues:

- Place orders (limit, market, and venue-supported advanced types) against any
  account and any universal symbol
- View open orders across all exchanges and accounts in one consolidated view
- Amend and cancel orders, individually or in bulk
- Validate orders against the symbol's filters (precision, min quantity, min
  notional) before they reach the exchange

### 4. History

The system of record for everything that has happened:

- **Order history** — every order placed through the panel or observed on the
  connected accounts, with its full lifecycle (created, partially filled,
  filled, cancelled, rejected)
- **Trade history** — individual fills with price, quantity, fee, and fee asset

History is queryable across exchanges, accounts, symbols, and time ranges, so
questions like "all ETH perp fills across every venue last week" have one
answer in one place.

### 5. Asset Management

A consolidated view of what the operation holds and where:

- **Balance snapshots** — per-asset balances for every account and wallet type
  (spot, margin, derivatives), captured over time so balance evolution can be
  reviewed, not just the current state
- **Position snapshots** — open derivative positions with size, direction,
  entry price, mark price, unrealized PnL, and margin usage, likewise captured
  over time

Snapshots make it possible to see the whole operation's exposure at a glance
and to reconstruct what the book looked like at any past moment.

### 6. Transferring

Moves funds between any two points in the operation, not just within one
exchange:

- Main account ↔ subaccount transfers within an exchange
- Wallet-to-wallet transfers within an account (e.g. spot → derivatives)
- **Cross-exchange transfers** — moving funds from an account on one exchange
  to an account on another, handled as a single tracked operation even though
  it involves a withdrawal and a deposit under the hood

Every transfer is tracked from initiation to confirmation, with its status and
full history visible in the panel.

### 7. Agent

An LLM-powered operator interface. Instead of clicking through forms, the user
describes an operation in natural language and the agent translates it into
concrete, executable actions using the modules above:

- *"Help me send limit buy orders from 10% to 15% below evenly"* — the agent
  resolves the reference price, computes the price ladder, sizes and validates
  each order against symbol filters, and places them
- *"Create 10 subaccounts with HMAC API keys and Ed25519 API keys in Binance"*
  — the agent provisions the subaccounts and both key types through Account
  Management

Design principles for the agent:

- The agent can only act through the same operations the panel exposes — it
  has no privileged side channel
- The agent shows its interpreted plan (the concrete orders, accounts, or
  transfers it is about to execute) so the user can confirm before anything
  irreversible happens
- Every agent action is attributed and logged like any manual action

## Cross-Cutting Concerns

These apply across all modules rather than belonging to any single one.

### Safety and Risk Guardrails

Configurable limits that apply regardless of whether an action comes from the
UI or the agent: maximum order notional, maximum transfer size, per-account
allow-lists of symbols, and confirmation requirements for destructive or
irreversible actions (withdrawals, key revocation, bulk cancellation). The
agent is subject to the same guardrails as a human operator.

### Audit Trail

Every state-changing action — order placement, key creation, transfer,
subaccount creation, agent execution — is recorded with who (or which agent
conversation) initiated it, when, with what parameters, and what the outcome
was. The audit trail is append-only.

### Reconciliation

The exchange is always the source of truth. The panel periodically reconciles
its view of orders, balances, and positions against each venue and surfaces
discrepancies rather than silently trusting local state. This also covers
activity that happens outside the panel (e.g. an order placed directly on the
exchange).

### Credential Security

Exchange API keys and secrets are the most sensitive data in the system. They
are stored encrypted, never displayed after creation, and scoped to the
minimum permissions each use case needs. Withdrawal-enabled keys are treated
as a distinct, higher-risk class.

### Reference Market Data

Several features need current market prices — the agent's percentage-based
orders, position mark prices, and portfolio valuation. The panel maintains a
normalized reference price per universal symbol for these purposes. This is
operational reference data, not a market data product.

### Extensibility

Each exchange integration is isolated behind a common internal interface
(symbols, accounts, orders, transfers). Adding a new venue means implementing
that interface once; every module — including the agent — gains support for
the new venue without changes.

## What CPanel Is Not

- **Not a strategy engine.** It does not generate trading signals or run
  strategies; it executes and administers operations that a human or agent
  requests.
- **Not a market data platform.** It keeps only the reference data its own
  features need, not full order books or historical candles.
- **Not a public multi-tenant product.** It is an internal operations panel
  for a trading operation's own accounts.
