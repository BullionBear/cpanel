# cpanel v3 — 架構

2026-11-06 · 狀態：一期範圍已定；未來帳號流向的一個問題待定（§12 D10）

版本命名：**v1** = 2026-07-13 封存的原始版（`archive/v1-20260713/`，tag `v1-final`）；**v2** = 2026-07-13 起重建的版本（最後一個 commit 2026-09-29 `9ae8629`，2026-11-06 封存到 `archive/v2-20261106/`，tag `v2-final`）；**v3** = 本文件。

---

## 0. 結論

- **v3 是 mftiko 的外部 PMS。** 只吃 mftiko E21-D085 定義的輸入：mftiko API（bearer token，只發 GET）、SYM、PMS 自己的唯讀交易所 key、交易所 REST。不碰 etcd、mftiko 的 Postgres、`td.evt`。
- **已定：** backend 用 Go；不做任何對交易所的寫入（下單、劃轉、提領、開子帳號）；範圍限 SYM 有的 venue（Binance 系列、OKX、Bybit）。
- **一期兩個子模組：**
  - **Account／Key 管理：** PMS 自己的唯讀 key，對應到 mftiko 的交易所帳號。
  - **History：** TradeHistory、OrderHistory（只落已結束且有成交的單）、FundingHistory，加上定時的餘額快照與持倉快照。
- **管線：** `API Server → Account／Key 管理 → DB`；`DB → ReconcileLoop → job 表 → Worker → DB`。ReconcileLoop 只讀 DB，所有對外呼叫（mftiko、SYM、交易所）都是 job，失敗都看得到。
- **帳號系統與 mftiko 分開。** cpanel 的 exchange_account 只由 cpanel 建立；與 mftiko 唯一的接點是 `(exchange, exch_account_uid)` 的 join，用來把 `/scopes` 對到 cpanel 的帳號。未來的走向是帳號先注入 cpanel，mftiko 再向 cpanel 拿（§4.9）。
- **UI 開放公網。** 公網層（cpanel-api）收到 secret 只負責封裝，自己解不開；解密與驗證只在私網的 worker 做（§4.1、§4.8）。
- **二期：** 歸屬（cloid × session 區間）、由成交推導的持倉與 PnL、funding 分配、對帳（§7）。一期先把 cloid 解碼後的欄位存好，二期不必重抓。
- **v2 整個凍結：** 程式碼搬 `archive/v2-20261106/`，正式環境停機，資料庫整顆唯讀保留，v3 用新的 database（§9）。

---

## 1. 現況盤點（v2）

### 1.1 元件

```
 Browser ──► cpanel-frontend (SvelteKit, adapter-static, nginx)
                │  JWT (Discord OAuth → ui_/api_ token)
                ▼
 ┌──────────────────── cpanel-backend（單一 FastAPI 進程）────────────────────┐
 │ api/ 10 個 router        ActivityAuditMiddleware → audit_log               │
 │ jobs/scheduler（asyncio，in-process，per exchange 註冊）                    │
 │   symbols.refresh_instruments 1h · refresh_prices 5m · snapshots 1h        │
 │   orders.poll_open 30s · history.sync 2m · transfers.progress 1m           │
 │ core/events（in-process SSE hub）· services/balance_refresh（debounce）     │
 │ core/ratelimit（per exchange token bucket，5 rps / cap 10）                 │
 │ connectors/ Binance · Bybit · GateIO · Bitget · OKX · Deribit（官方 SDK）  │
 └───────────────────────────────┬───────────────────────────────────────────┘
                                 ▼
                     Postgres（Alembic，migration/ 獨立元件）
```

部署：`172.237.19.123`，共用 Traefik，`capi.lynxlinkage.com`／`cpanel.lynxlinkage.com`。CI：push `main` → build image 推 GHCR → SSH `docker compose up`；`migrate.yml` 同時平行對 prod 跑 `alembic upgrade head`。

### 1.2 模組與資料表（19 張）

| 模組 | 資料表 | 行為 |
|---|---|---|
| symbols | `universal_ticker` | 自有 `<EXCHANGE>_<MARKET>_<PAIR>`（`BINANCE_PERP_BTCUSDT`），字串主鍵，每小時刷新 |
| accounts | `account`、`api_credential` | `(exchange, label)`；key 以 envelope encryption 存，可 audited reveal；Bybit 子帳號／key 全生命週期 |
| snapshots | `snapshot_batch`、`balance_snapshot`、`position_snapshot` | 每小時＋事件觸發，append-only |
| orders | `trade_order` | 面板下單，cloid = `cpanel` + 16 hex |
| history | `trade_history`、`order_history`、`deposit_history`、`withdrawal_history`、`internal_transfer_history`、`funding_fee_history`、`sync_cursor` | 依 native id upsert；新 stream 只回補 3 天 |
| transfers | `transfer`、`transfer_step` | 站內劃轉＋跨所提領／入金的 state machine |
| platform | `job_run`、`audit_log`、`panel_access_token` | |

規模：約 32k 行（不含 tests），6 個 connector 約 9.4k 行；約 480 個 test；56 個 Alembic revision。DESIGN.md 的 Phase 7（agent）、Phase 8（reconciliation）沒有實作。

### 1.3 為什麼不在 v2 上改

1. **兩個商品權威。** v2 的 `BINANCE_PERP_BTCUSDT` 與 SYM 的 `BinanceUM_Perp_BTCUSDT` + UID 文法、粒度都不同；mftiko 的 `/scopes` 只帶 UID。
2. **帳號模型對不上。** v2 是 `(exchange, label)`＋多把 credential；mftiko 是 `api_id`，對帳單位是 `(exchange, exch_account_uid)`（E21-D002、D008）。
3. **沒有歸屬概念。** 只有 `external` 旗標；v2 自己下的單在 mftiko 看來是 manual。
4. **持有寫入權限的 key**，與 mftiko 的 `/accounts` 重疊。
5. **單進程**：API、scheduler、SSE hub 共生死，不能多副本。
6. **history 由活動推 symbol、只回補 3 天、不留原始回覆。**
7. **CI 平行跑 migration 與 deploy。**

留下的是概念：capability 宣告、以 native id upsert 的冪等、cursor 在 commit 後才前進、空帳號也寫 batch、venue 的分頁怪癖（`archive/v2-20261106/docs/EXCHANGE_INTEGRATION.md`、`archive/v2-20261106/docs/ACCOUNT.md`，做 venue integration 時參考）。

---

## 2. 邊界與不變式

| 事情 | 歸屬 | 依據 |
|---|---|---|
| 商品（ticker、filter、UID、上下架） | mftiko SYM | E02-D003、E21-D009、E22-D001 |
| 交易帳號（交易 key、`api_id`、kind、`exch_account_uid`） | mftiko API（現在） | E21-D002、D008 |
| 交易所帳號的身分（`exchange`、經交易所驗證的 `exch_account_uid`）與 PMS 的唯讀 key | cpanel；未來 mftiko 也從這裡拿（§4.9） | 本文件 D9、D10 |
| session、algo、下單 | mftiko STS／TD | E06-D002、E21-D011 |
| scope（哪個 `api_id` 從何時開始交易哪個商品 UID） | mftiko TD agent 寫，API 開出 | E21-D009、D010 |
| 訂單、成交、funding、餘額、持倉的事實 | 交易所 REST | E21-D001 |
| **PMS 的唯讀 key、落庫、cursor、去重、快照**（一期）；**歸屬、PnL、對帳**（二期） | **cpanel v3** | E21-D001、D014 |

用詞：本文的 **UID 一律指 SYM 的商品 UID**；帳號用 `api_id`（mftiko 的交易身分）、`exch_account_uid`（交易所的帳號 id）、`exchange_account`（cpanel 的對帳單位）。

**不變式**（CI 以依賴與 lint 檢查擋）：

- I1 只 import `github.com/BullionBear/mftiko/contract/...`，不 import `internal/`、`exchange/`、etcd、mftiko 的 DB。
- I2 對 mftiko API 只發 GET。PMS 的 token 是單一角色（E21-D010 沿用 E10-D009），實際上有完整寫權限；client wrapper 只開放 GET。
- I3 PMS 的交易所 key 必須是唯讀，登記時驗證，IP 白名單綁 cpanel 的出口 IP。
- I4 寫進 `ledger` 的每一列都能從 `raw` 重建。
- I5 ReconcileLoop 不做 I/O，只讀寫 DB。
- I6 公網層（cpanel-api）不持有任何能解開 secret 的金鑰。

---

## 3. 系統圖

```
            mftiko（private VPC）
   mftiko-api  /accounts?include_deleted · /scopes      mftiko-sym  Revisions · List · GetByUid
        ▲                                                    ▲
        │ GET（mftiko.sync job）                              │ gRPC（sym.sync job）
 ┌──────┼────────────────────────── cpanel v3 ───────────────┼──────────────────────┐
 │      │                                                    │                      │
 │  cpanel-api（無狀態，經 Traefik 對公網；只有 worker 的公鑰）  │                      │
 │   ├─ Account／Key 管理（封裝 secret）┐                      │                      │
 │   └─ History／快照／job 查詢 (GET) │                        │                      │
 │                                   ▼                        │                      │
 │                      ┌────────── Postgres ──────────┐      │                      │
 │                      │ pms.* mirror.* ingest.*      │      │                      │
 │                      │ raw.* ledger.*               │      │                      │
 │                      └──┬───────────────────▲───────┘      │                      │
 │                         │ 讀                 │ 寫（同一個 transaction：           │
 │                         ▼                    │   raw + ledger + cursor + job 結果）│
 │   ReconcileLoop（純 DB，可多副本）             │                                   │
 │     期望狀態 vs 現況 → INSERT job ON CONFLICT DO NOTHING                          │
 │                         ▼                    │                                   │
 │   ingest.job（= Dispatcher + Queue）          │                                   │
 │                         ▼ 租帳號 → 依序跑該帳號的 job                              │
 │   Worker（1..N，每台一個出口 IP）──────────────┘                                   │
 └─────────────────────────┬──────────────────────────────────────────────────────┘
                           ▼  唯讀 key
                 Binance · Bybit · OKX（REST）
```

---

## 4. 一期元件

### 4.1 Account／Key 管理

- **exchange_account 只由 cpanel 建立**（操作者在 UI 或 API 建立），不從 mftiko 自動帶入。兩邊的帳號系統分開。
  - 與 mftiko 的接點只有 join：`mirror.account` 的 `(exchange, exch_account_uid)` 對到 `pms.exchange_account`，一個 cpanel 帳號可對到多個 `api_id`（同一把交易 key 登記多個 kind，或多把 key）。
  - mftiko 有、cpanel 沒有的交易所帳號，列成「mftiko 在交易、PMS 沒涵蓋」的告警，不自動建立。
  - cpanel 有、mftiko 沒有的帳號（純手動交易）照常支援：沒有 scope，stream 只來自 discovery 與 watch list（§4.3）。
- **`exch_account_uid` 以交易所回報為準。** 建立帳號時可以先填，但要等第一把 key 驗證過才轉 `active`；驗證結果與填的值不同就拒絕。mftiko 不驗證這個欄位，E21-D008 列為已知風險（填錯時 PMS 會把它當成另一個帳號），cpanel 這一側補上，也讓 cpanel 的帳號身分可以成為未來 mftiko 的來源（§4.9）。
- **登記 key 的流程（公網層解不開 secret，I6）：**
  1. cpanel-api 收到 key 與 secret，立刻以 worker 的公鑰封裝（§4.8），寫入 `pms.credential`，狀態 `unverified`，回 `202`。明文只在這個請求的記憶體裡。
  2. ReconcileLoop 派 `credential.verify` job。worker 解封後向交易所確認兩件事，任一不過就轉 `rejected` 並寫明原因：
     - 權限是唯讀（I3）。
     - 交易所回報的帳號 id 等於 `exch_account_uid`。各 venue 的端點：Binance `GET /api/v3/account` 的 `uid`、Bybit `GET /v5/user/query-api` 的 `userID`、OKX `GET /api/v5/account/config` 的 `uid`（以 venue profile 為準）。
  3. 通過就轉 `active`。驗證從 worker 發出，用的正是之後拉歷史的出口 IP，所以 key 的 IP 白名單設錯會在這一步就被發現。
- **key 是 write-only：** 登記後不回傳 secret（比照 mftiko E10-D063）。唯讀 key 沒有 reveal 的需要；要換就重新登記。
- `pms.credential.purpose` 一期只有 `read`。欄位先留著，給 §4.9 的未來用。
- **credential 的健康狀態是一等公民**（`pms.credential.status`）：`unverified → active | rejected`，`active → auth_failed`，另有 `disabled`（操作者停用）。由 worker 依錯誤分類寫入（§4.4），ReconcileLoop 依它決定要不要派 job。
- 所有寫入（建立、登記、停用、刪除）進 `meta.audit`。

### 4.2 mirror（兩個 job）

- **`mftiko.sync`**（預設每 60 秒）：
  - `GET /accounts?include_deleted=true` 全量分頁 → `mirror.account`。只用來做 join，不建立 cpanel 的帳號。
  - `GET /scopes` 全量 → `mirror.scope`。**列永不刪，`since` 只往小改**。回應的 `freshness` 列出不可用的 region 時，只新增、不推論「沒有 scope」（E21-D010）。
  - 一期不 mirror session records；歸屬是二期的事。
- **`sym.sync`**（預設每 60 秒）：`Revisions` 比對每個 `(venue, category)` 的 revision，落後的以 `List` 整張重載 → `mirror.symbol`。遇到不在表內的 UID（例如 scope 指向已下架的商品）以 `GetByUid(include_inactive=true)` 補。之後要更即時再改成 `WatchChanges`；一期用 job 的好處是失敗會出現在 job 歷史裡，也不需要長連線的 leader。

### 4.3 ReconcileLoop

每一輪只讀 DB，算出「應該存在的 job」，以 `INSERT … ON CONFLICT DO NOTHING` 寫入。它是 level-triggered：漏掉一輪無所謂，下一輪會補；多副本同時跑也安全（唯一性由 partial unique index 保證，§5），不需要選主。

規則，依序：

1. **credential：** `unverified` 的派 `credential.verify`。`auth_failed` 的不派任何歷史或快照 job，只派一個低頻的 `credential.probe`（預設每 15 分鐘）。`rejected`、`disabled` 什麼都不派。
2. **stream 規劃**（upsert `ingest.stream`，不刪）：
   - scope：`api_id` → `mirror.account` 的 `(exchange, exch_account_uid)` → `pms.exchange_account`；對不到的計入 §4.1 的「PMS 沒涵蓋」告警，不建 stream。UID → `mirror.symbol` → `(venue, exch_ticker)`；同一個 exchange_account 內同一個 UID 取各 `api_id` 最早的 `since`，往前多讀 1 分鐘（TD 主機與交易所的時鐘差，E21-D085）。
   - discovery：funding／income 與持倉快照裡出現、但沒有 stream 的商品（手動單），加成 `source = discovered`。Binance UM／CM 的 income 帶 `COMMISSION` 的 symbol，能找出有成交的商品；Binance 現貨沒有帳號層級的成交端點，手動現貨交易要操作者加進 watch list（`source = manual`），UI 上明講。
3. **stream 派工：** `next_due_at ≤ now`、而且沒有未完成 job 的 stream 派一個 job。上一個 job 回報 `more = true`（還沒追上現在）的，`next_due_at = now`。
4. **待查訂單：** `ingest.pending_order` 裡到期的，依 `(exchange_account, venue)` 批次派 `order.resolve`（§4.5）。
5. **快照：** 每個 active exchange_account，當前時間點 `b = floor(now, 1h)` 還沒有 batch、也沒有未完成 job，就派 `snapshot`。過去的時間點補不回來（不能對過去取快照），缺了就是看得見的缺口。
6. **mirror：** `mftiko.sync`、`sym.sync` 依間隔派。

**頻率分層**（控制 Binance per-symbol 端點的 request weight）：

| 層 | 條件 | 預設間隔 |
|---|---|---|
| hot | 最近 24 小時有成交，或最近一次快照有持倉 | 2 分鐘 |
| cold | 其他 | 24 小時 |

冷的商品每天跑一次就足夠：成交至少回 3 個月、訂單 90 天內查得到（§4.5），每天一次離這兩個期限都很遠。

### 4.4 job 表與 Worker

- **job 表就是 Dispatcher 加 Queue**，不另外架 broker。
- **認領：** worker 先以 `ingest.account_lease` 租一個 exchange_account（`UPDATE … WHERE lease_until < now() … SKIP LOCKED`），租期內依 priority 依序跑該帳號的 pending job，並續租。同一個帳號同時只有一個 worker 在跑，per-account 的限速預算只在一個進程內。
- **一個 job = 一條 stream 跑到預算用完**（例如 20 個請求或 30 秒），不是一頁一個 job。每頁一個 transaction：raw、ledger、cursor（帶版本號 CAS）一起 commit。結束時寫 job 結果：請求數、筆數、`more`、錯誤類別。
- **cursor 在執行時讀當下的值**，不用派工當下帶進來的值。
- **錯誤分類**（worker 寫入 job，並更新 stream 或 credential 的狀態）：

| 類別 | 例子 | 處理 |
|---|---|---|
| `auth` | key 無效、權限不足、IP 不在白名單 | credential 連續 2 次 → `auth_failed`，觸發告警；ReconcileLoop 斷路（§4.3-1） |
| `rate_limited` | 429；Binance 418（IP 被封） | 依 `Retry-After` 退避；418 時整台 worker（整個出口 IP）暫停 |
| `unavailable` | 5xx、逾時、維護 | stream 指數退避 |
| `bad_request` | 4xx 的參數錯誤 | 視為 bug：stream 暫停並告警，不熱重試 |
| `not_found` | Binance `-2013`（訂單不存在） | 只在 `order.resolve` 出現，見 §4.5 |

- **健康狀態放在 stream 與 credential 那一列上**（`last_ok_at`、`consecutive_failures`、`last_error_class`、`last_error`），UI 與告警讀這裡。job 表是證據，不是偵測機制。
- **job 表的量：** 例如 3 個帳號 × 40 個商品 × 2 種、每 2 分鐘一輪，一天約 17 萬筆。`ingest.job` 按天分區、保留 30 天；失敗的另外複製到 `ingest.job_failure`，保留一年。

### 4.5 History 的語意

**TradeHistory（只新增）**

- 以交易所的 trade id 去重，raw 與 ledger 同一個 transaction 寫入。
- Binance UM／CM 以 symbol 為單位：從 `since − 1 分鐘` 起以 7 天窗回補；追上現在之後改用 `fromId` 往前推（`fromId` 不能與 `startTime`／`endTime` 同時送），cursor 記成 `{mode: window, t}` 或 `{mode: fromId, id}`。
- Bybit、OKX 是帳號層級、依 category／instType 分頁，一條 stream 一個 category。
- 每筆成交的 `orderId` 若還沒有 `ledger.order`，就寫進 `ingest.pending_order`。

**OrderHistory（只落已結束、而且成交量大於 0 的單）**

- 不追蹤 open order，也不落沒有成交的單。訂單一旦寫入就不再變動。
- **由成交驅動，不另外掃訂單：** 沒有成交的單不會出現在成交裡，所以「該有的訂單」= 成交裡出現過、但還沒有已結束紀錄的 `orderId`，也就是 `ingest.pending_order`。
- `order.resolve` job 依 venue 選批次方式：
  - 待查的單不多：Binance 逐筆 `GET /order`（symbol 與 orderId 都從成交取得）；Bybit、OKX 用各自以 order id 查詢的端點。
  - 待查的單多（例如做市）：Binance 用 `allOrders(orderId = 最小值)` 一頁一千筆往後翻，在本地過濾。
- 結果：
  - 已結束 → 寫入 `ledger.order`，從 pending 移除。
  - 還沒結束（部分成交、仍掛著）→ 留在 pending，退避後再查。這個集合只會有部分成交中的單。
  - `not_found` → 標成 `unresolved` 並告警。Binance 的訂單建立 90 天後就查不到；一張掛超過 90 天才結束的部分成交單會落到這裡。
- **已結束的定義：** `FILLED`，以及有成交的 `CANCELED`、`EXPIRED`、`EXPIRED_IN_MATCH`（Binance 現貨的自成交保護）。`PARTIALLY_FILLED` 不算。各 venue 的對照寫在 venue profile。
- **為什麼 Binance 一定要查單：** `userTrades` 不帶 `clientOrderId`，cloid 只能從訂單取得（E21-D011）。`ledger.order` 存 `client_order_id` 與解碼後的 `cloid_ver`、`algo`、`cloid_ts_ms`；解不開（非平台 cloid）就存 null，二期歸屬時記為 `manual`。
- **一致性檢查**（每次 resolve 後跑，不一致就告警）：訂單的成交量等於其成交加總；每筆訂單至少有一筆成交；pending 的存活時間。

**FundingHistory（只新增，帳號層級）**

- Binance UM／CM：income，`incomeType = FUNDING_FEE`；Bybit：transaction log 的 `SETTLEMENT`；OKX：bills 的 funding fee 類型。一個 exchange_account 每個 venue 一條 stream，時間窗 cursor，預設每小時。
- 唯一鍵依 venue 定義，寫在 venue profile。

**交易所的歷史會過期**：以 Binance COIN-M 文件為例，訂單 90 天後查不到、成交只回 3 個月內、一次查詢最多 7 天窗。所以 ingest 必須持續跑，raw 永久保存；normalizer 有版本號，修 bug 就升版、從 raw 重建，不必重抓已經過期的歷史（I4）。

### 4.6 快照

- 每個 active exchange_account 每小時一個時間點（對齊整點），唯一鍵 `(exchange_account_id, bucket)`。所有帳號在同一刻取樣，跨帳號加總才可比。
- 一個 snapshot job 依該帳號 kind 的錢包結構，抓餘額（各錢包）與持倉，寫一筆 `ledger.snapshot_batch` 加上明細。帳號是空的也要寫 batch，否則讀「最新」會讀到上一次的非空結果（v2 的教訓）。
- 持倉存交易所回報的數量、方向、開倉價、mark price、未實現損益、保證金。一期不做跨資產估值；要估值時再加價格來源。
- 每小時對每個帳號都跑，所以快照 job 同時是 key 的健康探測：沒有 scope 的帳號，key 失效也會在一小時內被發現。

### 4.7 cpanel-api 與 web

- 無狀態、可多副本，OpenAPI-first（`contract/openapi/cpanel.v1.yaml`），經 Traefik 對公網。
- **認證：** 人用 Discord OAuth（guild＋role，v2 已在跑的流程），登入後發 HttpOnly、Secure、SameSite=Strict 的 session cookie；程式存取用 static bearer token，只存 SHA-256（比照 mftiko 的 API）。帳號與 key 的寫入端點全部進 audit。
- 一期端點：
  - Account／Key：`GET /exchange-accounts`（含 join 到的 `api_id`、key 狀態、stream 健康摘要）、`GET /exchange-accounts/unmatched`（mftiko 有、cpanel 沒有的帳號）、`POST /exchange-accounts`、`POST /exchange-accounts/{id}/credentials`（封裝後回 `202`，驗證走 job）、`POST …/credentials/{cid}/verify`（重新驗證）、`POST …/credentials/{cid}/disable`、`DELETE …/credentials/{cid}`。
  - History：`GET /trades`、`GET /orders`、`GET /funding`（篩選：exchange_account、UID、時間），不透明 cursor 分頁，比照 mftiko 的慣例（`limit` 1–1000、`next_cursor`）。
  - 快照：`GET /snapshots`（batch 與缺口）、`GET /balances?at=`、`GET /positions?at=`。
  - 營運：`GET /streams`、`GET /jobs`（依狀態、錯誤類別、帳號篩選）、`GET /pending-orders`。
- web：SvelteKit SPA，由 cpanel-api 直接服務，同源，不需要 CORS。

### 4.8 platform

- **PMS key 的封裝：非對稱，公網層只有公鑰。** worker 共用一組 X25519 金鑰對：私鑰以部署 secret 交付給 worker，cpanel-api 只拿得到公鑰。格式照 mftiko 的 `sealbox`（E06-D013）：`version (1 byte) | 收件公鑰指紋 (8 bytes) | NaCl anonymous sealed box`，封的內容是含 `exchange_account_id`、`credential_id` 的 protobuf。sealed box 沒有 associated data，所以解開後要比對這兩個 id，防止密文被搬到別的帳號底下。換鑰時新舊私鑰並存，由背景 job 重新封裝。
  - 這樣做的理由：公網層被攻破時，攻擊者拿得到資料庫裡的密文，也拿得到之後送進來的新 key，但解不開已經存的 key。
  - 與 mftiko 用同一個格式，是為了 §4.9：哪天 cpanel 要把 key 交給 mftiko，可以直接封給 TD instance 的公鑰（E21-D004）。`internal/platform/sealbox` 不能 import（I1），cpanel 自己實作，用 mftiko 的測試向量對拍。
  - 私鑰與 DB 分開備份；私鑰遺失，所有 key 都要重新登記。
- migration：純 SQL；部署流程先 migrate、成功才換版。
- log：結構化 JSON；`/metrics`（Prometheus）：stream lag、job 失敗率（依錯誤類別）、credential 狀態、pending order 的存活時間、快照缺口、PMS 沒涵蓋的 mftiko 帳號數。

### 4.9 未來：mftiko 向 cpanel 拿帳號

走向：帳號先注入 cpanel，mftiko 再向 cpanel 拿。這會修訂 mftiko 的 E21-D002（帳號的權威在 mftiko 的 Postgres）與 E21-D008（`exch_account_uid` 由使用者手填）。一期不做，但先把會影響 schema 與部署的部分定下來：

- **cpanel 的帳號身分要能被引用：** `pms.exchange_account` 有穩定的 id 與 `revision`（成功更新加 1，比照 mftiko E21-D027），`exch_account_uid` 經交易所驗證、建立後不可改，刪除用軟刪除。mftiko 之後以這個 id 引用帳號，填錯 uid 的風險就不存在了。
- **給 mftiko 的是內部 API，不經公網：** 另一個只聽私網的 listener，service token 認證，分頁與 cursor 比照 mftiko 的慣例。
- **mftiko 在交易路徑上不能依賴 cpanel 活著：** mftiko 應該把拿到的帳號存一份在自己那邊（它本來就把帳號投影到 etcd），cpanel 掛掉只影響新增與修改，不影響正在跑的 session。
- **相依方向：** cpanel 依賴 mftiko 的 scope 與 SYM，mftiko 依賴 cpanel 的帳號。只要兩邊都存副本、啟動時不互等，這個循環就沒問題。
- **待定的是 secret（D10）。** 見 §12。

---

## 5. 資料模型

| schema | 表 | 重點 |
|---|---|---|
| `pms` | `exchange_account(id)`，`UNIQUE(exchange, exch_account_uid)`；`credential(id)` | `credential.status`、`exch_reported_uid`、`permissions`、`verified_at`、`consecutive_auth_failures` |
| `mirror` | `account(api_id)`、`scope(api_id, uid)`、`symbol(uid)`、`sync_state(source)` | 只有 mirror job 寫；二期加 `session_record`、`session_account` |
| `ingest` | `stream(id)`，`UNIQUE(exchange_account_id, venue, kind, key)` | `source`（scope／discovered／manual）、`tier`、`cursor jsonb`、`cursor_version`、`next_due_at`、健康欄位 |
| | `job(id)`，按天分區 | partial unique：`(stream_id) WHERE status IN ('pending','running')`；`(kind, exchange_account_id, bucket) WHERE status IN (…)` 給快照；singleton key 給 mirror job |
| | `job_failure`、`account_lease(exchange_account_id)`、`pending_order(exchange_account_id, venue, native_order_id)` | |
| `raw` | `venue_trade`、`venue_order`、`venue_income`、`venue_snapshot` | `(exchange_account_id, venue, native_id)`；payload `jsonb`、`fetched_at`、請求參數；不可變；按月分區 |
| `ledger` | `trade`、`order`、`funding`、`snapshot_batch`、`balance_snapshot`、`position_snapshot` | 可由 raw 重建，帶 `norm_version`；商品一律 UID |
| `meta` | `audit` | |

`ledger.order` 給二期歸屬預先建 index：`(exchange_account_id, algo, cloid_ts_ms)`。

---

## 6. 限速與出口 IP

- Binance 的 request weight 以 IP 計（mftiko PROTOCOL §10 P17 也標為未定）。**cpanel 的 worker 不能與 TD 主機共用出口 IP**，否則 PMS 的輪詢會吃掉實盤的額度。
- Bybit、OKX 有些端點以帳號限速，PMS 輪詢與實盤共用同一個帳號的額度：每個 exchange_account 設保守預算，回補（backfill）再低一級。
- 一台 worker 主機 = 一個出口 IP = 一個進程，限速 bucket 在進程內就正確；擴充就加主機，以 account lease 分配帳號。

---

## 7. 二期（已設計，不在一期）

- **歸屬**（照 E21-D011、D083、D084，與 `mftiko-pms-sim` 的 `attribute.go` 等價）：mirror 加 `/session-records?api_id=` 與 `/sessions/{id}`。cloid 解不開 → `manual`；解得開時，在同一個 exchange_account 內找 `algo ∈ algo_ids` 且 `ts_ms ∈ [created_at, min(last_exit.at, sts_retired_at)]` 的 session：0 個 → `unattributed`、1 個 → `session`、≥2 個 → `ambiguous`。只看 cloid 的 `ts_ms`，不看成交時間。session 區間改變時重算該帳號。
- **持倉**：`Q(uid, t) = Q_open(uid) + Σ s_i·q_i`，依歸屬各算一份；`Q_open` 取自第一張快照。
- **PnL（cash-flow mark-to-market，不必選 FIFO 或平均成本）：** linear `Q(t)·m(t) − Σ s_i·q_i·p_i`（quote）；inverse `Σ s_i·q_i / p_i − Q(t) / m(t)`（base）。交易所回報的 `realizedPnl` 是帳號層級的平均成本，只拿來對帳。
- **funding 分配：** funding 對 `Q` 是線性的（linear `−r·Q·M`、inverse `−r·Q/M`），按有號持倉分給各 session，加總精確等於帳號層級；按 `|Q|` 比例分配在多空互抵時會錯。差額記成 residual。
- **對帳：** 等帳號所有 stream 的 cursor 都過了快照時間 `t`，檢查 `Q_snapshot(t) = Q_open + Σ fills ≤ t`，不相等就定向重抓。
- **驗收：** 在 mftiko 的 `deploy/accept/accept.sh pms` 環境，每張單的歸屬與 `pms-report.json` 完全一致。

---

## 8. 部署

- worker 必須在 mftiko 的 private VPC 內（連 mftiko-api、SYM），並有自己的出口 IP（§6）。
- 資料庫與 mftiko 的 Postgres 分開（不同 database、不同 role），可以同一個 cluster。
- **UI 開放公網：** cpanel-api 放在 VPC 內，由公網的 Traefik（或 LB）轉進來；worker 與 DB 不對外。cpanel-api 只有 worker 的公鑰（I6）。§4.9 給 mftiko 的內部 API 是另一個只聽私網的 listener，不掛在 Traefik 上。

---

## 9. 封存 v2（C1）

**repo**（一個 PR，`chore/archive-v2`）：

```
archive/
├── README.md                  索引：v1、v2 各自的時間、tag、DB 位置
├── v1-20260713/               ← 原 archive/backend、archive/frontend
└── v2-20261106/               ← backend/ frontend/ docs/ docker/ deployment/ migration/
                                 docker-compose.dev.yml  templatemo-catalyst-style.css
                                 .github-workflows/（GitHub 只執行根目錄 .github/workflows，搬走即停用）
```

- **先在 repo 層級停用 workflow，再推 tag。** `docker.yml` 監聽所有 tag，而 GitHub 執行的是 tag 所在 commit 上的 workflow 檔；不先停用，推 `v2-final`（`9ae8629`）會重新 build image。另打 `v1-final`（`a992d00`）。
- 根目錄 `.github/workflows/` 刪除（檔案留在 tag）。
- `archive/` 不放依賴清單（`requirements*.txt`、`package.json`、`package-lock.json`）：它是給人讀的，要重建就 checkout tag；否則 Dependabot 會一直對封存的程式碼告警。
- 開著的 PR 關閉；沒 merge 的分支打成 `archive/v2/<branch>` tag 後刪除，已 merge 的直接刪。
- 根目錄 `README.md` 改寫成 v3；v3 文件放進 `docs/`。本機的 `credentials/`、`venv/` 不進 git。

**正式環境：**

1. 停機前記下執行中的 image digest，以它打 `:v2-final`（不重新 build）；然後 `docker compose -p cpanel down`。不做封存頁，停機後 Traefik 回 404。
2. **交易所 key：** 資料庫裡有三張表存著 key：v1 之前的 `api_archived_20260221`、v1 的 `api_archived_20260713`（兩張都是**明文** `api_secret`），以及 v2 的 `api_credential`（envelope encryption）。逐把盤點，沿用的寫明理由，其餘到交易所撤銷；然後把 secret 欄位清成 `REDACTED`，再銷毀 `CREDENTIAL_ENCRYPTION_KEY`。
3. **備份：** 清除之後才 `pg_dump -Fc`，用 age 加密，異地保存，並在另一台還原過。
4. **唯讀以權限保證：** `default_transaction_read_only` 只是預設值，連線可以 `SET` 改回，owner 也一定能寫。做法是把擁有權 `REASSIGN OWNED` 給一個 NOLOGIN 的封存角色，另開只有 `SELECT` 的登入角色，原本的 app 角色設 `NOLOGIN`；`default_transaction_read_only` 只當第二道防線。角色若與其他服務共用，改成只對 cpanel 的物件逐一改 owner。
5. **憑證：** 刪除 GitHub repo secret `DATABASE_URL`、`SSH_PRIVATE_KEY`（並移除伺服器上對應的公鑰），刪除伺服器上的 `env.sh`。

v2 裡超過 90 天的訂單已經無法從交易所重抓，所以封存的資料庫要留著。

---

## 10. 分期

一期拆成 8 個 epic、44 張 ticket，細節見 [PLAN.md](PLAN.md)。

| Epic | 名稱 | 驗收 |
|---|---|---|
| C1 | 封存 v2（§9） | v2 停機、DB 唯讀、repo 重排、workflows 停用、tag `v2-final` |
| C2 | 骨架與平台 | VPC 內的 cpanel-api 經公網可登入；打得到 mftiko `/accounts` 與 SYM |
| C3 | Job 系統 | 多副本 ReconcileLoop 不產生重複 job；錯誤分類與退避生效 |
| C4 | mftiko 與 SYM mirror | mftiko 掛掉時 scope 不縮；unmatched 可見 |
| C5 | Account／Key 管理 | uid 不符或非唯讀的 key 被拒；cpanel-api 的環境裡沒有私鑰 |
| C6 | 快照（BinanceUM 起步） | 整點快照、缺口可見；key 失效演練通過 |
| C7 | History（BinanceUM 起步） | 與 `mftiko-pms-sim` 對拍一致 |
| C8 | 其他 venue | 各 venue 達到 C6、C7 的標準 |
| 二期 | §7 | §7 |

---

## 11. 需要 mftiko 配合的事

- PMS 用的 bearer token 有完整寫權限（單一角色）：建議加唯讀角色。
- `/events` 的 `accounts`、`scopes` topic（E21-D010 列為之後再加）：有了 `mftiko.sync` 才能從輪詢改成推送。
- 要支援 GateIO、Bitget、Deribit，得先在 SYM 補 listing adapter。
- §4.9 的帳號流向：mftiko 改成向 cpanel 拿帳號，要修訂 E21-D002、E21-D008，`api.v1` 的帳號建立流程也會變。

---

## 12. 決策

**已定（2026-11-06）**

| # | 決策 |
|---|---|
| D1 | backend 用 Go，直接 import mftiko 的 `contract/` |
| D2 | 不做任何對交易所的寫入 |
| D3 | 範圍限 SYM 有的 venue |
| D6 | 一期兩個子模組：Account／Key 管理、History（Trade、Order、Funding）＋定時餘額與持倉快照 |
| D7 | 管線為 ReconcileLoop → job 表 → Worker；ReconcileLoop 只讀 DB，對外呼叫都是 job |
| D8 | OrderHistory 只落已結束且成交量大於 0 的單；不追蹤 open order；由成交驅動 |
| D4 | UI 開放公網（經 Traefik），公網層只能封裝 secret、解不開（I6） |
| D5 | v2 資料不匯入，封存的 DB 唯讀保留 |
| D9 | 支援不在 mftiko 裡的帳號；兩邊的帳號系統分開，只以 `(exchange, exch_account_uid)` join |

**待定**

| # | 問題 | 選項 | 建議 |
|---|---|---|---|
| D10 | 未來 mftiko 向 cpanel 拿的「帳號」包不包含交易 key 的 secret | (a) 只有身分：cpanel 管帳號身分與 uid 驗證，交易 key 仍登記在 mftiko、引用 cpanel 的帳號 id；(b) 連 secret：cpanel 也存交易 key，封給 TD instance 的公鑰交出去 | (a)。(b) 等於讓一個開在公網的服務成為所有交易 key 的根；若要 (b)，存交易 key 的服務要從 cpanel-api 拆出去、只在私網 |

---

參考：mftiko `docs/DECISIONS.md` E21-D001–D014、E21-D083–D087、E22-D001；`docs/PROTOCOL.md` §2、§7.3、§10；`cmd/mftiko-pms-sim`（`c769041`，2026-11-06）。Binance 歷史查詢限制：[COIN-M Query Order](https://developers.binance.com/docs/derivatives/coin-margined-futures/trade/rest-api/Query-Order)、[Updates to Binance Futures Order History Data](https://www.binance.com/en/support/articles/e04e15b8b0154280bb7ef0f36c06eb77)。
