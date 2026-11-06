# cpanel v3 — Epic 與 Ticket（一期）

2026-11-06 · 依據 [ARCHITECTURE.md](ARCHITECTURE.md)

## 慣例

- **編號加前綴 `C`**：epic `C1`…`C8`，ticket `C1-01`，決策 `C1-D001`。mftiko 用 `E{n}`，cpanel 的文件會一直引用 mftiko 的 E21、E22，不加前綴會撞號。
- **一張 ticket 約一個 PR**，一到三天的量；PR 標題帶 ticket 編號（比照 mftiko 的「E21-01，#513」）。
- **每個 epic 有自己的驗收與決策紀錄**，epic 結束時決策併進架構文件。
- **UI 跟著各 epic 做**，不另開 UI epic：每個 epic 結束時都有可用的頁面。

## 依賴

```
C1 封存 v2 ───────────────(C1-02 先於 C2-06)
C2 骨架 ──► C3 Job 系統 ──┬─► C4 Mirror ──┐
                          └─► C5 Account／Key ─┴─► C6 快照 ──► C7 History（BinanceUM）──► C8 其他 venue
```

- C1 與 C2 可以平行；C4 與 C5 可以平行。
- 關鍵路徑：C2 → C3 → C5 → C6 → C7 → C8。
- C6 完成時就有第一個可用的成果：所有帳號的餘額與持倉快照，加上 key 的健康監控。

## 總覽

| Epic | 名稱 | Ticket 數 | 驗收 |
|---|---|---|---|
| C1 | 封存 v2（#32） | 6 | v2 停機、DB 以權限唯讀、repo 重排、workflows 停用、tag `v1-final`／`v2-final`、key 已撤銷並清除 |
| C2 | 骨架與平台（#33） | 7 | VPC 內的 cpanel-api 經公網可登入；打得到 mftiko `/accounts` 與 SYM |
| C3 | Job 系統（#34） | 5 | 多副本 ReconcileLoop 不產生重複 job；錯誤分類與退避生效 |
| C4 | mftiko 與 SYM mirror（#35） | 3 | mftiko 掛掉時 scope 不縮；symbol 跟得上 SYM revision；unmatched 可見 |
| C5 | Account／Key 管理（#36） | 6 | uid 不符或非唯讀的 key 被拒；cpanel-api 的環境裡沒有私鑰 |
| C6 | 快照（#37） | 5 | BinanceUM 整點快照、缺口可見；key 失效演練通過 |
| C7 | History（BinanceUM）（#38） | 8 | 與 `mftiko-pms-sim` 對拍一致；一致性檢查全綠 |
| C8 | 其他 venue（#39） | 4 | 各 venue 的快照與 History 達到 C6、C7 的標準 |

---

## C1 封存 v2

決策（2026-11-06）：

- C1-D001 key 撤銷後清除 secret 欄位（v1 是明文、v2 是密文），之後銷毀 `CREDENTIAL_ENCRYPTION_KEY`。
- C1-D002 不做封存頁；停機後 Traefik 回 404，v3 上線時再切網域。
- C1-D003 `archive/` 不放依賴清單；要重建就 checkout tag。
- C1-D004 開著的 PR 關閉；沒 merge 的分支打成 `archive/v2/<branch>` tag 後刪除，已 merge 的直接刪。
- C1-D005 唯讀以權限保證（擁有權移給 NOLOGIN 角色、另開 SELECT-only 角色）；`default_transaction_read_only` 只是第二道防線。
- C1-D006 先停用 workflow 再推 tag；`:v2-final` 以停機前執行中的 digest 打，不重新 build。

| Ticket | 內容 | 大小 | 驗收 | 依賴 |
|---|---|---|---|---|
| C1-01 #40 ✅ | 停用三個 workflow（repo 層級）；annotated tag `v2-final` → `9ae8629`、`v1-final` → `a992d00` | S | 推 tag 沒有觸發任何 run | — |
| C1-02 #41 | repo 重排：v1 → `archive/v1-20260713/`、v2 → `archive/v2-20261106/`；刪根目錄 workflows；移除封存目錄的依賴清單；`archive/README.md` 索引；放入 `docs/ARCHITECTURE.md`、`PLAN.md`、`DECISIONS.md`；README 改寫；關閉 PR #24、#25、#26、#31；舊分支 tag 後刪除 | M | merge 後沒有 run；根目錄只剩 `archive/`、`docs/`、README、`.gitignore`；遠端只剩 `main` | C1-01 |
| C1-03 #42 | 正式環境停機：記下執行中的 digest，以它打 `:v2-final`；`docker compose -p cpanel down` | S | 容器不在；`:v2-final` 等於停機前的 digest | C1-01 |
| C1-04 #43 | key 盤點、撤銷、清除：`api_archived_20260221`、`api_archived_20260713`（明文）、`api_credential`；銷毀 `CREDENTIAL_ENCRYPTION_KEY` | M | 每把 key 有處置紀錄；三張表沒有未清除的 secret | C1-03 |
| C1-05 #44 | 資料庫封存：先確認資料庫位置、owner、是否共用 instance；清除後 `pg_dump` 加密異地、另一台還原；`REASSIGN OWNED` 給 NOLOGIN 角色、SELECT-only 登入角色、app 角色 `NOLOGIN` | M | app 角色登不進；唯讀角色的寫入與 DDL 都失敗；備份還原成功 | C1-04 |
| C1-06 #45 | 憑證清理：刪 repo secret `DATABASE_URL`、`SSH_PRIVATE_KEY` 與伺服器上的對應公鑰（先確認沒有其他 repo 共用）；刪伺服器的 `env.sh`；本機 remote 的 PAT 撤銷 | S | `gh secret list` 只剩 `GHCR_TOKEN`、`DISCORD_WEBHOOK`；伺服器沒有 cpanel 的 secret | C1-05 |

順序：C1-01 → C1-03 → C1-04 → C1-05 → C1-06；C1-02 在 C1-01 之後可以平行。C1-03 到 C1-06 要在伺服器、資料庫與交易所上操作。

## C2 骨架與平台

| Ticket | 內容 | 驗收 | 依賴 |
|---|---|---|---|
| C2-01 | Go module 與目錄（`cmd/cpanel-api`、`cmd/cpanel-worker`、`cmd/cpanel-migrate`、`internal/…`、`contract/openapi`、`web/`）；archtest：I1（只 import mftiko 的 `contract/`）、I5（reconcile 套件不得連到網路與交易所 client）、I6（`cmd/cpanel-api` 不得連到解封的程式）；lint；Makefile | archtest 在 CI 跑，違規即失敗 | — |
| C2-02 | Postgres、SQL migration 與 `cpanel-migrate`；schema 骨架（`pms`、`mirror`、`ingest`、`raw`、`ledger`、`meta`）；`meta.audit` | up／down 在 CI 對真的 Postgres 跑 | C2-01 |
| C2-03 | cpanel-api：OpenAPI-first（oapi-codegen）、`/healthz`、`/readyz`、`/metrics`、結構化 log、服務 SPA | OpenAPI 與實作不一致時 CI 失敗 | C2-02 |
| C2-04 | 認證：Discord OAuth（guild＋role）→ HttpOnly／Secure／SameSite=Strict session cookie；static bearer token（只存 SHA-256）；寫入端點一律寫 audit | 沒有 role 的 Discord 帳號登不進；token 明文不落地 | C2-03 |
| C2-05 | web 骨架：SvelteKit SPA、登入、layout、API client（由 OpenAPI 產生） | 登入後看得到空的首頁 | C2-04 |
| C2-06 | CI/CD：build → GHCR（新 image 名）→ migrate → deploy，依序；Discord 通知 | migrate 失敗時不換版 | C2-03、C1-02 |
| C2-07 | 部署：cpanel-api 在 VPC 內、經公網 Traefik 進來；worker 與 DB 只在私網；worker 出口 IP 與 TD 主機分開；設定 mftiko token 與 SYM token | VPC 內以 GET 打得到 mftiko `/accounts`、SYM gRPC health | C2-06 |

## C3 Job 系統

| Ticket | 內容 | 驗收 | 依賴 |
|---|---|---|---|
| C3-01 | `ingest` schema：`stream`、`job`（按天分區）、`job_failure`、`account_lease`；partial unique index（每條 stream、每個快照時間點、每個 singleton 各至多一個未完成 job）；分區建立與過期刪除 | 同一條 stream 插兩個 pending job，第二個被擋 | C2-02 |
| C3-02 | ReconcileLoop 框架：規則註冊、只讀寫 DB、`INSERT … ON CONFLICT DO NOTHING`；singleton job 依間隔派工 | 起三個 ReconcileLoop 同時跑，job 數與一個時相同 | C3-01 |
| C3-03 | Worker：租帳號、依 priority 依序跑、預算（請求數／秒數）、每頁一個 transaction、cursor 帶版本 CAS、回報 `more` | kill worker 後租約到期，另一台接手，資料不重複 | C3-01 |
| C3-04 | 錯誤分類與退避：`auth`、`rate_limited`、`unavailable`、`bad_request`、`not_found`；stream 與 credential 的健康欄位；418 時整台 worker 暫停 | 以假交易所（httptest）逐類注入，各自走對的處理 | C3-03 |
| C3-05 | 營運面：`GET /streams`、`GET /jobs`、metrics（stream lag、失敗率、快照缺口）、告警規則、UI 頁 | UI 依帳號、狀態、錯誤類別篩 job | C3-04、C2-05 |

## C4 mftiko 與 SYM mirror

| Ticket | 內容 | 驗收 | 依賴 |
|---|---|---|---|
| C4-01 | mftiko client：包 `contract/gen/api/v1`，只開放 GET（I2）；`mftiko.sync`：`/accounts?include_deleted=true`、`/scopes`（列不刪、`since` 只往小、`freshness` 有缺口時只新增） | mftiko API 停掉或回空的 scope 時，`mirror.scope` 不變 | C3-02 |
| C4-02 | SYM client（connect，契約 S）；`sym.sync`：`Revisions` 比對、落後的 `(venue, category)` 以 `List` 重載、未知 UID 以 `GetByUid(include_inactive)` 補 | SYM 推進 revision 後一輪內重載；下架商品查得到 | C3-02 |
| C4-03 | join 與 unmatched：`mirror.account` ↔ `pms.exchange_account`；`GET /exchange-accounts/unmatched`；metric 與告警 | mftiko 有、cpanel 沒有的帳號出現在 unmatched | C4-01、C5-01 |

## C5 Account／Key 管理

| Ticket | 內容 | 驗收 | 依賴 |
|---|---|---|---|
| C5-01 | `pms.exchange_account`：穩定 id、`revision`、`exch_account_uid` 建立後不可改、軟刪除；API：建立、列表、讀取、刪除 | 改 uid 回 400；刪除後歷史仍查得到出處 | C2-03 |
| C5-02 | sealbox：X25519 + NaCl anonymous sealed box，header 與 mftiko `internal/platform/sealbox` 相同（version、8 bytes 指紋）；封裝與解封拆成兩個套件；worker 金鑰對的設定與載入 | 與 mftiko 的測試向量對拍；archtest 確認 cpanel-api 沒有連到解封套件 | C2-01 |
| C5-03 | 登記 key：API 封裝、回 `202`、狀態 `unverified`；`credential.verify` job：Binance（`/api/v3/account` 的 `uid`、`apiRestrictions`）、Bybit（`query-api`）、OKX（`account/config`）驗證唯讀與 uid | 非唯讀或 uid 不符 → `rejected` 並寫明原因；通過 → `active` | C5-01、C5-02、C3-03 |
| C5-04 | credential 健康：狀態機（`unverified`、`active`、`rejected`、`auth_failed`、`disabled`）、`credential.probe`、ReconcileLoop 的斷路規則、停用與刪除、告警 | `auth_failed` 的 credential 只剩 probe job | C5-03、C3-04 |
| C5-05 | 換鑰：新舊私鑰並存，背景 job 重新封裝 | 換鑰中途重啟 worker，所有 key 都還解得開 | C5-02、C3-03 |
| C5-06 | Account UI：帳號列表、建立帳號、登記 key、狀態與原因、join 到的 `api_id`、unmatched | | C5-04、C4-03、C2-05 |

## C6 快照（BinanceUM 起步）

| Ticket | 內容 | 驗收 | 依賴 |
|---|---|---|---|
| C6-01 | venue profile 範本＋BinanceUM：快照與歷史用到的端點、分頁、限速與 weight、錯誤碼對照錯誤類別、終態對照、去重鍵、查詢期限 | 文件 review 過；之後每個 venue 照這份寫 | — |
| C6-02 | 快照表（`snapshot_batch`、`balance_snapshot`、`position_snapshot`、`raw.venue_snapshot`）；ReconcileLoop 的整點規則，唯一鍵 `(exchange_account_id, bucket)` | 停掉 worker 一小時，該時間點顯示為缺口 | C3-02、C4-02 |
| C6-03 | BinanceUM 快照 job：各錢包餘額、持倉（數量、方向、開倉價、mark、未實現損益、保證金）；空帳號也寫 batch | 結果與 Binance 網頁一致；清空的帳號讀到空的最新快照 | C6-01、C6-02、C5-03 |
| C6-04 | 快照 API（`/snapshots`、`/balances?at=`、`/positions?at=`）＋ UI | | C6-03 |
| C6-05 | 驗收：key 失效演練。撤銷一把 testnet key，一小時內轉 `auth_failed`、歷史與快照 job 停派、只剩 probe；重新登記後恢復 | 演練紀錄附在 ticket | C6-03、C5-04 |

## C7 History（BinanceUM 起步）

| Ticket | 內容 | 驗收 | 依賴 |
|---|---|---|---|
| C7-01 | `raw.venue_trade`、`raw.venue_order`、`raw.venue_income`；`ledger.trade`、`ledger.order`、`ledger.funding`（帶 `norm_version`）；從 raw 重建的指令 | 刪掉 ledger 後重建，結果相同 | C2-02 |
| C7-02 | stream 規劃規則：scope join（`api_id` → `(exchange, exch_account_uid)` → exchange_account）、同一 UID 取最早的 `since` 減 1 分鐘、hot／cold 分層 | 規則的單元測試只用 DB fixture | C3-02、C4-03 |
| C7-03 | TradeHistory BinanceUM：從 `since` 以 7 天窗回補，追上後改 `fromId`；成交裡沒見過的 `orderId` 寫進 `pending_order` | 回補與增量交界處不漏不重 | C7-01、C7-02、C5-03 |
| C7-04 | OrderHistory：`order.resolve`（少量逐筆 `GET /order`，大量 `allOrders(orderId=最小值)`）；終態定義；`not_found` → `unresolved`；cloid 解碼存 `cloid_ver`、`algo`、`cloid_ts_ms`；一致性檢查（成交量等於成交加總、每單至少一筆成交） | 部分成交中的單留在 pending，結束後才寫入 | C7-03 |
| C7-05 | FundingHistory BinanceUM：income `FUNDING_FEE`，時間窗 cursor，每小時 | 與 Binance 網頁的 funding 紀錄一致 | C7-01、C5-03 |
| C7-06 | discovery：income 的 `COMMISSION` 與持倉快照裡沒有 stream 的商品 → `source = discovered`；watch list API（`source = manual`） | 在網頁上手動下一單，一輪內出現新的 stream | C7-03、C6-03 |
| C7-07 | History API（`/trades`、`/orders`、`/funding`、`/pending-orders`，不透明 cursor 分頁）＋ UI | | C7-04、C7-05 |
| C7-08 | 驗收：在 mftiko 的 `deploy/accept/accept.sh pms` 環境跑，cpanel 的成交與有成交的訂單（含 cloid）和 `pms-report.json` 一致 | 對拍報告附在 ticket | C7-04 |

## C8 其他 venue

每張 ticket：venue profile → 快照 → TradeHistory → OrderHistory → FundingHistory，各自達到 C6、C7 的驗收標準。

| Ticket | 內容 | 依賴 |
|---|---|---|
| C8-01 | Binance CM（inverse；qty 以 quote 計） | C6、C7 |
| C8-02 | Binance Spot（沒有 funding；手動交易只能靠 watch list） | C6、C7 |
| C8-03 | Bybit UTA（帳號層級的 execution、order history、transaction log 的 `SETTLEMENT`） | C6、C7 |
| C8-04 | OKX（fills-history、orders-history、bills 的 funding） | C6、C7 |

Binance PM（`papi`）與 Binance EO：有帳號用到時再開票。
