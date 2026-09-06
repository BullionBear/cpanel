# CPanel — Exchange Account Management Endpoints

Reference for the **Accounts page redesign**. It catalogs the account,
credential, and provisioning endpoints each venue exposes over its API, the
identifiers and permission models involved, and — importantly — what each
venue **cannot** do via API (so the panel must do it a different way).

Scope is deliberately narrow: account structure (master/subaccount),
credential verification, subaccount lifecycle, and API-key lifecycle. Balance,
order, and transfer endpoints are out of scope except where they define the
account structure.

Legend: endpoints marked ✅ are wired into the current connector; ⬜ are
available on the venue but not yet used by CPanel; 🚫 are **not offered** by the
venue's API at all.

---

## Concept mapping

| Concept | Binance | Bybit |
|---|---|---|
| API surfaces | 3 base URLs (spot `api`, USD-M `fapi`, COIN-M `dapi`); account mgmt lives on spot `/sapi` | 1 base URL, v5 `/v5/user/*` |
| Account identifier | Master: numeric `uid`; **subaccount: a virtual email** | Master & sub: numeric `uid` |
| Key schemes via API | HMAC, Ed25519, RSA (Ed25519/RSA only via Broker key creation) | **HMAC only** |
| Create subaccount via API | ✅ virtual subaccount | ✅ |
| **Create subaccount API key via API** | 🚫 regular master (web console only); ✅ **only** under Binance Link / Broker program | ✅ master key creates sub keys |
| Revoke/modify API key via API | 🚫 regular master; ✅ Broker only | ✅ |
| Freeze / unfreeze subaccount | 🚫 (no direct API; managed via web) | ✅ |
| Verify a key + read its permissions | ✅ `GET /sapi/v1/account/apiRestrictions` | ✅ `GET /v5/user/query-api` |

The single most consequential difference for the UI: **on Binance you cannot
mint a subaccount API key over the API unless you are a Broker/Link partner.**
For a normal master account the flow is "create subaccount via API → operator
creates the key in the Binance web console → operator pastes it into CPanel".
On Bybit the whole loop (create sub → create key → revoke key) is API-driven.

---

## Binance

Account management is served from the **spot** base (`https://api.binance.com`,
`/sapi/...`). All endpoints below are `USER_DATA` (signed; require
`timestamp`, optional `recvWindow`, and the `X-MBX-APIKEY` header). Subaccounts
are addressed by their **virtual email**, not a uid.

### Credential verification & account info

| Op | Method / Path | Notes | Status |
|---|---|---|---|
| Get API Key Permission | `GET /sapi/v1/account/apiRestrictions` | The verification workhorse. Returns `enableReading`, `enableSpotAndMarginTrading`, `enableFutures`, `enableMargin`, `enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer`, `enablePortfolioMarginTrading`, `ipRestrict`, `createTime`. | ✅ |
| Account info | `GET /sapi/v1/account/info` | `vipLevel`, `isMarginEnabled`, `isFutureEnabled`, `isOptionsEnabled`, `isPortfolioMarginRetailEnabled`. | ⬜ |
| Account Status | `GET /sapi/v1/account/status` | `"Normal"` or a restriction reason. | ⬜ |
| Account API Trading Status | `GET /sapi/v1/account/apiTradingStatus` | Trading-lock / dust indicators. | ⬜ |
| Spot account (uid + balances) | `GET /api/v3/account` | Used today only to read `uid`; futures-only keys can't call it. | ✅ |

**Verification model.** There is no "is this a master or sub" flag on the
permission response; CPanel infers structure from the subaccount list. A key's
capabilities are the boolean flags above — map `enableSpotAndMarginTrading ||
enableFutures` → can-trade, `enableWithdrawals` → can-withdraw.

### Subaccount lifecycle (master account)

| Op | Method / Path | Params | Status |
|---|---|---|---|
| Create virtual subaccount | `POST /sapi/v1/sub-account/virtualSubAccount` | `subAccountString` → returns `{email}`. Requires **trade**-enabled key. | ✅ |
| Query subaccount list | `GET /sapi/v1/sub-account/list` | `email?`, `isFreeze?`, `page?`, `limit≤200` → `subUserId`, `email`, `isFreeze`, `createTime`, `isManagedSubAccount`, `isAssetManagementSubAccount`. | ✅ |
| Query subaccount status | `GET /sapi/v1/sub-account/status` | Per-sub `isMarginEnabled` / `isFutureEnabled` / `isSubUserEnabled`. | ⬜ |
| Enable Futures for sub | `POST /sapi/v1/sub-account/futures/enable` | `email`. | ⬜ |
| Enable Margin for sub | `POST /sapi/v1/sub-account/margin/enable` | `email`. | ⬜ |
| Enable Options for sub | `POST /sapi/v1/sub-account/eoptions/enable` | `email`. | ⬜ |

> Note: Binance has **no** "freeze subaccount" or "delete subaccount" API for
> regular masters — those are console-only operations.

### Subaccount assets & transfers (master account)

| Op | Method / Path | Notes |
|---|---|---|
| Subaccount assets | `GET /sapi/v3/sub-account/assets` | Balances for one sub (`email`). |
| Spot assets summary | `GET /sapi/v1/sub-account/spotSummary` | BTC-valued summary across subs. |
| Universal transfer | `POST /sapi/v1/sub-account/universalTransfer` | `fromAccountType`, `toAccountType`, `asset`, `amount`, `fromEmail?`, `toEmail?`. Master↔sub, wallet↔wallet. |
| Query universal transfer history | `GET /sapi/v1/sub-account/universalTransfer` | Paginated by `page`/`limit`, `clientTranId`. |
| Margin transfer for sub | `POST /sapi/v1/sub-account/margin/transfer` | `email`, `asset`, `amount`, `type` (1 spot→margin, 2 margin→spot). |
| Futures transfer for sub | `POST /sapi/v1/sub-account/futures/transfer` | Sub's spot↔futures wallet. |

### API-key lifecycle — the hard part

**Regular master account: 🚫 no key creation over API.** You can only restrict
IPs on an *already-created* subaccount key:

| Op | Method / Path | Notes |
|---|---|---|
| Add IP restriction (v2) | `POST /sapi/v2/sub-account/subAccountApi/ipRestriction` | `email`, `subAccountApiKey`, `status` (1 unrestricted / 2 trusted-IP-only), `ipAddress?`. |
| Get IP restriction | `GET /sapi/v1/sub-account/subAccountApi/ipRestriction` | `email`, `subAccountApiKey`. |
| Delete IP list | `DELETE /sapi/v1/sub-account/subAccountApi/ipRestriction/ipList` | Remove specific IPs. |

**Binance Link / Broker program only** (requires a broker-enabled master;
these are the *only* Binance endpoints that mint/revoke keys):

| Op | Method / Path | Notes |
|---|---|---|
| Create API key for sub | `POST /sapi/v1/broker/subAccountApi` | `subAccountId`, `canTrade`, `marginTrade?`, `futuresTrade?`, `publicKey?` (for Ed25519/RSA). Returns `apiKey` + `secretKey` (secret shown **once**). ≤1 key/sub/second. |
| Query sub API key | `GET /sapi/v1/broker/subAccountApi` | Lists keys and their trade flags per sub. |
| Delete sub API key | `DELETE /sapi/v1/broker/subAccountApi` | |
| Change sub API permission | `POST /sapi/v1/broker/subAccountApi/permission` | Toggle spot/margin/futures. |

> There is **no API to create a *master*-account API key on Binance** under any
> program — always the web console.

### Current connector capabilities (Binance)

`INSTRUMENTS`, `REFERENCE_PRICES`, `CREDENTIAL_VERIFY`, `SUBACCOUNT_LIST`,
`SUBACCOUNT_CREATE`. Deliberately **no** `API_KEY_CREATE` / `API_KEY_REVOKE`
(Broker-only). Ed25519 keys are supported for *signing* (the operator brings
an Ed25519 key created in the console).

---

## Bybit

One base URL (`https://api.bybit.com`), v5. All account management lives under
`/v5/user/*`. Signed with `X-BAPI-API-KEY` / `X-BAPI-SIGN` /
`X-BAPI-TIMESTAMP` / `X-BAPI-RECV-WINDOW`. Master and sub are both numeric
`uid`. Bybit issues **HMAC keys only** via API.

### Credential verification

| Op | Method / Path | Notes | Status |
|---|---|---|---|
| Get API Key Information | `GET /v5/user/query-api` | The verification workhorse; any permission can call it. Returns `apiKey`, `readOnly` (0/1), `permissions{}`, `ips`, `type`, `isMaster`, `parentUid` (`"0"` when called by master), `userID`, `uta`, `vipLevel`, `expiredAt`/`deadlineDay`, `createdAt`, `kycLevel`. | ✅ |
| Get UID wallet type | `GET /v5/user/get-member-type` | `memberIds?` (master can query up to 200 subs). Returns per-uid `accountType[]` = `FUND` / `UNIFIED`. | ⬜ |

**Verification model.** `isMaster` and `parentUid` give account structure
directly. `readOnly` gives read-vs-trade. Withdraw capability = `"Withdraw"`
present in `permissions.Wallet` (master keys only).

### Subaccount lifecycle (master key)

| Op | Method / Path | Params | Status |
|---|---|---|---|
| Create sub UID | `POST /v5/user/create-sub-member` | `username` (6–16, letters+digits, unique forever), `memberType` (1 normal / 6 custodial), `password?`, `note?`, `switch?` → `{uid, username, memberType, status}`. | ✅ |
| Get sub UID list (≤1000) | `GET /v5/user/query-sub-members` | → `subMembers[uid, username, memberType, status (1 normal/2 login-banned/4 frozen), accountMode, remark]`. | ✅ |
| Get sub UID list (unlimited) | `GET /v5/user/submembers` | `pageSize`, `nextCursor` — for >10k subs. | ⬜ |
| Freeze / unfreeze sub UID | `POST /v5/user/frozen-sub-member` | `subuid`, `frozen` (0 unfreeze / 1 freeze). | ⬜ |

> Requires the master key to hold "Account Transfer", "Subaccount Transfer",
> or "Withdrawal" permission. Usernames can never be reused, even after
> deletion.

### API-key lifecycle (fully API-driven)

| Op | Method / Path | Params | Status |
|---|---|---|---|
| Create sub API key | `POST /v5/user/create-sub-api` | `subuid`, `readOnly` (0/1), `permissions{}`, `ips?`, `note?`. Returns `apiKey` + `secret` (**once**). Master key only. | ✅ |
| Modify sub API key | `POST /v5/user/update-sub-api` | `apikey` (required when master manages a sub key), `readOnly?`, `ips?`, `permissions?`. | ⬜ |
| Delete sub API key | `POST /v5/user/delete-sub-api` | `apikey?` (invalidates immediately). | ✅ |
| Modify master API key | `POST /v5/user/update-api` | Modifies **only the calling key** — `readOnly?`, `permissions?`. | ⬜ |
| Delete master API key | `POST /v5/user/delete-api` | Invalidates the **calling** key immediately. | ⬜ |

**Permission object** (shared by create/modify/query):

```
ContractTrade : ["Order", "Position"]
Spot          : ["SpotTrade"]
Wallet        : ["AccountTransfer",
                 "SubMemberTransfer"      (master)
                 "SubMemberTransferList"  (sub),
                 "Withdraw"               (master only)]
Options       : ["OptionsTrade"]
Derivatives   : ["DerivativesTrade"]
Exchange      : ["ExchangeHistory"]
Earn          : ["Earn"]
```

CPanel currently grants, for panel-created sub keys:
- trade: `ContractTrade:[Order,Position]`, `Spot:[SpotTrade]`, `Wallet:[AccountTransfer, SubMemberTransferList]`
- read : `ContractTrade:[Position]`, `Wallet:[AccountTransfer]`

**Key expiry gotcha:** a Bybit key with **no IP bound** expires after 90 days;
any key expires 7 days after the account password changes. Worth surfacing in
the UI from `expiredAt` / `deadlineDay`.

### Current connector capabilities (Bybit)

`INSTRUMENTS`, `REFERENCE_PRICES`, `CREDENTIAL_VERIFY`, `SUBACCOUNT_LIST`,
`SUBACCOUNT_CREATE`, `API_KEY_CREATE`, `API_KEY_REVOKE`. HMAC only.

---

## Deribit

Account management is served from `https://www.deribit.com/api/v2` (JSON-RPC).
Credentials are **Client ID + Client Secret** (HMAC, no passphrase). The
connector signs each private REST call with `Authorization: deri-hmac-sha256`.
Subaccounts are numeric `id`s; a key minted on a sub is created after
`public/auth` + `public/exchange_token` because `create_api_key` always
targets the authenticated subject.

### Credential verification & account info

| Op | Method | Notes | Status |
|---|---|---|---|
| Account summaries | `private/get_account_summaries?extended=true` | `id`, `type` (`main` / `subaccount`), per-currency equity. | ✅ |
| List API keys | `private/list_api_keys` | Match `client_id` to read `max_scope` and `ip_whitelist`. | ✅ |

**Verification model.** `type=main` → master. Scopes on the calling key map
`trade:*` → can-trade and `wallet:read_write` → can-withdraw. Missing
`list_api_keys` (narrow key) still verifies as valid from the summaries call.

### Subaccount lifecycle (master account)

| Op | Method | Notes | Status |
|---|---|---|---|
| List subaccounts | `private/get_subaccounts` | `id`, `username`, `login_enabled`. | ✅ |
| Create subaccount | `private/create_subaccount` | No name on create. | ✅ |
| Rename | `private/change_subaccount_name` | `sid` + name (`[A-Za-z0-9_]`, ≤16). Used after create. | ✅ |
| Freeze / unfreeze | — | 🚫 no venue equivalent (login toggle is 2FA-gated and not a freeze). | 🚫 |

### API-key lifecycle

| Op | Method | Notes | Status |
|---|---|---|---|
| Create key | `private/create_api_key` | `max_scope`, `name` ≤16. Secret returned once. Sub keys via `exchange_token`. | ✅ |
| Change scope | `private/change_scope_in_api_key` | Numeric key `id` (looked up from `client_id`). | ✅ |
| Remove key | `private/remove_api_key` | Numeric key `id`. | ✅ |
| List keys | `private/list_api_keys` | No secrets. | ✅ |

Panel scope tokens: `derivatives` / `spot` → `trade:read` (+ `trade:write` when
not read-only); `wallet` → `wallet:read` (+ `wallet:read_write`). `account:read`
is always granted so verify works. Deribit trade is not split by market.

**TFA:** `create_api_key` is documented as TFA-required on some accounts. If
the venue rejects with `security_key_authorization_error`, the operator must
create that key on Deribit and paste it into CPanel.

### Current connector capabilities (Deribit)

`INSTRUMENTS`, `REFERENCE_PRICES`, `CREDENTIAL_VERIFY`, `BALANCES`,
`POSITIONS`, `ORDERS`, `TRADE_HISTORY`, `ORDER_HISTORY`, `SUBACCOUNT_LIST`,
`SUBACCOUNT_CREATE`, `API_KEY_CREATE`, `API_KEY_MODIFY`, `API_KEY_REVOKE`,
`TRANSFERS`, `WITHDRAWALS`, `DEPOSITS`, `FUNDING_HISTORY`,
`INTERNAL_TRANSFER_HISTORY`. HMAC only. No freeze.

---

## Implications for the Accounts-page redesign

1. **Asymmetric provisioning.** The UI can offer the full create-sub →
   create-key → revoke-key loop on Bybit, but on Binance (non-broker) it can
   only create the subaccount; the key step is "register a key you made in the
   console". The current page already gates on capabilities
   (`/accounts/exchanges`) — the redesign should make this asymmetry *legible*
   rather than just hiding buttons (e.g. an explicit "create the key on Binance,
   then paste it here" step for Binance subs).

2. **Identifiers differ.** Binance subs are emails, Bybit subs are uids. A
   generic "uid" column reads oddly for Binance. Consider labeling it
   per-venue.

3. **Verification gives more than we show.** Both venues return rich
   permission + structure data (`isMaster`/`parentUid` on Bybit; the full
   permission booleans on Binance). The redesign can auto-detect master-vs-sub
   and per-key trade/withdraw/margin/futures scopes instead of asking the
   operator.

4. **Key health is available.** Bybit exposes key expiry and IP binding;
   Binance exposes `ipRestrict` and `createTime`. A "key health" surface
   (expiring soon / no IP restriction / withdraw-enabled) is buildable from
   data we already fetch at verify time.

5. **Freeze status.** Bybit reports `status` (normal/banned/frozen) per sub and
   can freeze/unfreeze; Binance reports `isFreeze`. The tree can show a status
   badge, and Bybit can expose a freeze toggle.
