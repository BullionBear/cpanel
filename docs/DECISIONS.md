# cpanel v3 — 決策紀錄

每條決策一個編號；修訂時新增一條並寫明修訂了哪一條，不改舊的。架構層級的決策沿用 [ARCHITECTURE.md](ARCHITECTURE.md) §12 的 `D` 編號；epic 內的決策以 `C{n}-D{nnn}` 編號，從 C1 開始。

## 架構（2026-11-06）

- **D1** backend 用 Go，直接 import mftiko 的 `contract/`：契約改動在 compile 時發現。
- **D2** 不做任何對交易所的寫入（下單、劃轉、提領、開子帳號與 key）。
- **D3** 範圍限 mftiko SYM 有的 venue：Binance 系列、OKX、Bybit。其他交易所要先在 SYM 補 listing，否則會有兩個商品權威。
- **D4** UI 開放公網（經 Traefik）。公網層只能封裝 secret、解不開（I6）。
- **D5** v2 的資料不匯入 v3；封存的資料庫唯讀保留。
- **D6** 一期兩個子模組：Account／Key 管理；History（Trade、Order、Funding）加上定時的餘額與持倉快照。
- **D7** 管線為 ReconcileLoop → job 表 → Worker。ReconcileLoop 只讀寫資料庫，對外呼叫（mftiko、SYM、交易所）都是 job。
- **D8** OrderHistory 只落已結束、而且成交量大於 0 的單；不追蹤 open order；由成交驅動。
- **D9** 支援不在 mftiko 裡的帳號。兩邊的帳號系統分開，只以 `(exchange, exch_account_uid)` join。未來的走向是帳號先注入 cpanel，mftiko 再向 cpanel 拿（ARCHITECTURE §4.9）。
- **D10（待定）** 未來 mftiko 向 cpanel 拿的帳號是否包含交易 key 的 secret。建議只給身分。

## C1 封存 v2（#32）

### C1-D001 清除封存資料庫裡的交易所 secret（#43）

- key 撤銷之後，把三張表的 secret 欄位清成 `REDACTED`：`api_archived_20260221`、`api_archived_20260713`（v1，**明文** `api_secret`）、`api_credential`（v2，envelope encryption）。
- 清除之後銷毀 v2 的 `CREDENTIAL_ENCRYPTION_KEY`，不做離線保存。
- 理由：key 沒有封存價值；要沿用的 key 本來就存在別的地方。不清除的話，異地備份就是一份明文 key 清單。

### C1-D002 不做封存頁（#42）

- 停機後 Traefik 對 `cpanel.lynxlinkage.com`、`capi.lynxlinkage.com` 回 404；v3 在 C2-07 部署時再切網域。

### C1-D003 `archive/` 不放依賴清單（#41）

- `requirements*.txt`、`package.json`、`package-lock.json` 從封存目錄移除。`archive/` 是給人讀的，要建置或執行就 checkout tag `v1-final`、`v2-final`。
- 理由：封存的依賴清單會讓 Dependabot 一直對不再執行的程式碼告警（封存時 `frontend/package-lock.json` 有 12 個未處理的告警）。

### C1-D004 舊 PR 與分支（#41）

- 開著的 PR（#24、#25、#26、#31）關閉。
- 沒 merge 的分支先打成 tag `archive/v2/<branch>` 再刪除，commit 不會遺失；已 merge 的分支直接刪除。遠端只留 `main`。

### C1-D005 封存資料庫的唯讀以權限保證（#44）

- 擁有權以 `REASSIGN OWNED` 移給一個 NOLOGIN 的封存角色；另開只有 `SELECT` 的登入角色；原本的 app 角色設 `NOLOGIN`。
- `default_transaction_read_only` 只當第二道防線：它只是預設值，連線可以 `SET` 改回，owner 也一定能寫。
- app 角色若與其他服務共用，改成只對 cpanel 的物件逐一改 owner，並只收回 cpanel 的 `CONNECT`。
- 修訂 ARCHITECTURE §9 原本「`ALTER DATABASE … default_transaction_read_only = on` 加收回寫入權限」的寫法。

### C1-D006 先停用 workflow 再推 tag；`:v2-final` 不重新 build（#40、#42）

- `docker.yml` 監聽所有 tag，而 GitHub 執行的是 tag 所在 commit 上的 workflow 檔：不先停用，推 `v2-final` 會重新 build image。三個 workflow 於 2026-11-06 在 repo 層級停用，之後才建立 tag。
- `:v2-final` 以停機前正式環境執行中的 digest 打。requirements 用 `>=`，重新 build 的依賴版本未必相同。
