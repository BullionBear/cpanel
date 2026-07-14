# CPanel — Accounts Page Redesign

Living design spec for the Accounts feature, designed exchange-by-exchange.
Endpoint capabilities that justify these choices live in
[ACCOUNT.md](ACCOUNT.md). This document records **decisions**, not endpoints.

Status: in progress. Confirmed decisions are marked ✅. Sections marked
🔲 are not yet designed.

---

## Cross-cutting decisions (all exchanges) ✅

These apply system-wide; only the per-venue provisioning capabilities differ.

1. **Nickname = the account label.** The existing `account.label` is
   repurposed as a **nickname**: a system-only, human-friendly name used
   everywhere in the UI to recognize and operate an account. It carries no
   meaning on the venue. (Kept unique per exchange, as today.)

2. **API keys carry free-form tags.** Each API credential can have multiple
   free-form text tags, chosen by the operator for organization and filtering
   (e.g. `spot`, `hedge`, `readonly`, `strategy-x`). Stored as a list on the
   credential.

3. **Combined account + key registration.** An account is registered
   *together* with an API key in a single step — no more "create the account,
   then separately attach a key". This is the universal registration flow for
   both main accounts and (where applicable) subaccounts.

4. **Venue identifiers are system-derived metadata.** The venue-native id
   (Bybit `uid`, Binance subaccount email) is **not** entered by the operator.
   The system derives it when needed — from credential verification
   (`query-api` / `apiRestrictions`) or the subaccount listing — and stores it
   on the account. In the UI it is display-only metadata attached to the
   account; it is never a required input and nothing filters or is organized by
   it. Accounts are identified everywhere by their **nickname**.

5. **Secrets are retrievable, on demand and audited.** Any stored key —
   including keys the operator injected (e.g. a Binance key or the Bybit
   bootstrap master) — can have its secret revealed in the panel at any time.
   There is no one-time "copy it now" banner. Every reveal is written to the
   audit log. This **supersedes** the ARCHITECTURE.md rule that "secrets are
   never returned by any API"; secrets remain envelope-encrypted at rest and
   are decrypted only for signing and for an explicit, audited reveal.

### Data-model impact (not yet implemented)

- `account.label` — semantically becomes the nickname (system-only). No column
  change strictly required; treated as nickname in the UI/API.
- `api_credential` — **add `tags`** (list of strings / JSON array).
- Registration API — a combined "register account with its credential"
  operation replacing the two-step create-then-add-credential flow.
- **Secret reveal endpoint** — an audited, authenticated operation that
  decrypts and returns one credential's secret. Update the ARCHITECTURE.md
  credential rule to match when implemented.

---

## Navigation ✅

Exchange nodes in the sidebar may expand into sub-pages. The structure is
per-exchange, driven by how much the venue's API supports:

```
Accounts
├─ Binance
│   ├─ Readme      (why Binance is registration-only)
│   └─ Account     (list + combined registration)
└─ Bybit
    ├─ Readme      (how self-service + bootstrap works)
    └─ Account     (bootstrap + full self-service lifecycle)
```

Both exchanges lead with a Readme explaining their model (registration-only vs.
self-service), followed by the Account page.

---

## Binance page ✅

**Philosophy: registration-only.** Binance's API cannot create or revoke
subaccount API keys for a normal (non-broker) master account (see
[ACCOUNT.md](ACCOUNT.md) → Binance → "API-key lifecycle"). So the panel does
**no** provisioning for Binance: it never creates, rotates, revokes, or syncs
keys or subaccounts. The operator does all of that on Binance's own site and
**injects** the resulting keys into CPanel.

### Binance → Readme

A static explainer page stating the model in plain terms:

- Create your API keys and subaccounts on Binance directly.
- Register each key here so CPanel can read balances/orders/history and trade.
- CPanel will never create, modify, or delete Binance keys or subaccounts —
  the panel only *uses* the keys you give it.

### Binance → Account

- Account list showing each registered account by **nickname**, its keys, key
  **tags**, and verified permissions/status.
- **Combined registration**: one form that registers an account *and* its API
  key at once (nickname + key + tags), verifying the key against Binance before
  saving.
- **Subaccounts are fully manual.** A Binance subaccount is registered exactly
  like a main account — the operator creates it on Binance, then registers it
  in CPanel (nickname + its key + tags) and links it to its parent. No venue
  subaccount sync, create, or listing is exposed.

### Connector

Binance connector stays verification + read only. Provisioning capabilities
(`SUBACCOUNT_CREATE`, and the absent `API_KEY_CREATE`/`API_KEY_REVOKE`) are not
surfaced in the Binance UI. `CREDENTIAL_VERIFY` is used at registration time.

---

## Bybit page ✅

**Philosophy: full self-service.** Bybit's API covers the whole account
lifecycle (see [ACCOUNT.md](ACCOUNT.md) → Bybit), so the operator does
everything from the CPanel page. The panel — not the operator on Bybit's site —
creates subaccounts, mints keys, changes their permissions, revokes them, and
freezes subaccounts.

### Bootstrap (the one manual step)

The **first master account and its master API key** are injected manually,
exactly like Binance (combined registration: nickname + master key + tags,
verified against Bybit). Everything after that is done in-page. Bybit does not
allow creating additional *master*-account keys via API, so key creation always
targets **subaccounts only**; the page states this rather than offering a
disabled control.

### In-page operations (full lifecycle)

From the registered master, the operator can, all within the page:

- **Create subaccount** — `POST /v5/user/create-sub-member`.
- **Create sub API key** — `POST /v5/user/create-sub-api`. CPanel stores the
  returned key **and secret** immediately (envelope-encrypted).
- **Modify sub API key** — permissions / read-only / IP binding, via
  `POST /v5/user/update-sub-api`. *(new connector capability)*
- **Revoke sub API key** — `POST /v5/user/delete-sub-api`.
- **Freeze / unfreeze subaccount** — `POST /v5/user/frozen-sub-member`.
  *(new connector capability)*
- **Sync / list subaccounts** — `GET /v5/user/query-sub-members`.

### Secret handling

Because CPanel creates the keys, it always holds the secret. No one-time
display: keys and secrets are stored on creation, and the operator can
**reveal any secret on demand** (audited — see cross-cutting decision 5).

### Connector

Bybit connector gains two capabilities beyond today's set: **modify API key**
(`update-sub-api`) and **freeze/unfreeze subaccount** (`frozen-sub-member`).
Existing: verify, subaccount list/create, key create/revoke. HMAC only.

---

## Tag UX ✅

Tags render as chips **next to the account / API key** they belong to (inline
in the list row), for at-a-glance context. No dedicated tag filter/grouping
view for now.
