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

## C2 骨架與平台（#33）

C2-D001–C2-D007 於 2026-11-06 定在 epic，原文照錄。

- **C2-D001** CI 拆成兩半：檢查（lint、test、archtest、OpenAPI）在 C2-01 就上線，每個 PR 都會跑；C2-06 只負責 build 與發布。
- **C2-D002** 工具鏈比照 mftiko：Go 1.26、pgx v5、zerolog、connect、oapi-codegen 的 `std-http-server` 加 `strict-server`、embed 的 SQL migration 加自己的 migrate command、每個測試起一個用完即丟的 Postgres。mftiko 的 `internal/` 不能 import（I1），需要的部分在 cpanel 自己實作。
- **C2-D003** 部署在 `172.237.19.123`，docker compose 加既有的 Traefik。GitHub Actions 只 build 與發布 image；部署由主機上的腳本執行：先 migrate，成功才換版，`/readyz` 不通過就換回上一個 digest。
- **C2-D004** web 以 `go:embed` 打包進 cpanel-api：一個 artifact、API 與 UI 的版本一致、同源。
- **C2-D005** session 存在資料庫，bearer token 由 UI 產生；兩者都只存 SHA-256。登出、撤銷與 Discord role 被拔掉都能立即生效。
- **C2-D006** 沿用 v2 的 Discord OAuth app，只改 redirect URI。
- **C2-D007** mftiko 以 commit 釘住，要升版就開 PR；契約的破壞性改動會在 compile 時出現。

### C2-D008 secret 以檔案進容器（#54）

- 每個 `Secret` 設定除了 `$NAME` 也接受 `$NAME_FILE`（檔案內容去掉前後空白）。兩個都設是錯誤。compose 的 `secrets` 掛在 `/run/secrets/`，環境變數只放路徑，`docker inspect` 看不到值。
- 資料庫的 secret 是整個 URL（`database_url`），不是只有密碼：程式只認 URL，拆開反而要在容器裡組字串。
- compose 的 file secret 是 bind mount，不改 owner，所以檔案的 owner 是 image 的 `nonroot`（65532），`0600`。

### C2-D009 資料庫：同一個 instance，自己的 database 與 role（#54）

- `100.65.26.119` 的 Postgres 17 上新建 database `cpanel_v3`、role `cpanel_v3`：`LOGIN`，不是 superuser，不能建 role 或 database，只擁有 `cpanel_v3`。`cpanel` 這個名字留給封存的 v2（C1-D005）。
- `pg_hba.conf` 最前面：只放行 `cpanel_v3` 從部署主機的 Tailscale IP 連 `cpanel_v3`，其他一律 `reject`。`REVOKE ALL ON DATABASE cpanel_v3 FROM PUBLIC`。
- 不沿用 `bullionbear`（#45）。

### C2-D010 到 mftiko 與 SYM 走 Tailscale，不經 `mftiko-proxy`（#54）

- 部署主機與 `100.65.26.119` 在同一個 tailnet；mftiko-api（`:8080`）、SYM gRPC（`:7450`，h2c）、Postgres 都只聽 tailnet。容器的流量經主機路由，直接連得到。
- `mftiko-proxy` 是 Traefik 把公網的 `mftiko.lynxlinkage.com` 轉到 mftiko console 用的。cpanel 經它就等於繞公網，而且多一個不屬於 cpanel 的元件。
- worker 的出口 IP 是主機的 `172.237.19.123`，沒有 exit node。

### C2-D011 `/readyz` 從主機以容器 IP 檢查；有 migration 的版本不自動往回滾（#54）

- image 是 distroless，沒有 shell 與 curl，不能用 compose 的 healthcheck；`deploy.sh` 在主機上以容器 IP 打 cpanel-api `/readyz` 與 cpanel-worker `/healthz`。
- 換回上一個 digest 只在新版沒有加 migration 時有用：舊的 binary 看到不認識的 migration 拒絕啟動（`RequireCurrent`）。這種情況 `deploy.sh` 照樣嘗試，失敗就明說要往前修。不在 binary 裡放寬這條：放寬就等於讓舊程式跑在新 schema 上。

### C2-D012 `capi.lynxlinkage.com` 永久轉址（#54）

- v3 同源，`capi.` 不再有用途。以 Traefik `redirectregex`（permanent）轉到 `cpanel.` 的同一路徑，舊書籤與舊 redirect URI 至少會落到新站。

### C2-D013 `cpanel-worker doctor`（#54）

- 子命令，不開 server、不需要資料庫。依序：mftiko `GET /accounts?limit=1`（帶 token）、SYM `grpc.health.v1` Check（不需 token）、SYM `Revisions`（帶 token，證明 token 有效）。每項一行，全過 exit 0。
- 只有讀的呼叫（I2）。

### C2-D014 GHCR 的 `cpanel` package 設為 public（#54）

- 主機不需要 GHCR 的登入憑證就能以 digest 拉 image，主機上少一把可以讀 org 其他 package 的 token。
- image 裡沒有 secret：設定與 secret 都在部署時以 `config.env` 與 `secrets/` 給。repo 本身仍是 private。
- 代價：任何人都拉得到 binary 與內嵌的 SPA。cpanel 沒有要對外保密的程式邏輯；之後若有，改回 private 並在主機上以只有 `read:packages` 的 token 登入。

## C3 Job 系統（#34）

### C3-D001 未完成與已完成的 job 分兩張表（#64）

- `ingest.job` 只放 `pending`、`running`，不分區；完成時在同一個 transaction 裡刪除，並寫入按 UTC 日分區的 `ingest.job_run`。
- 理由：Postgres 分區表上的 unique index 必須包含分區鍵。原設計「`ingest.job` 按天分區＋partial unique index」只在同一天的分區內互斥，跨日就失效。
- `job_run` 有 default 分區，分區維護落後時寫入不會失敗；`ingest.ensure_job_run_partition` 建分區時先把 default 裡那一天的列搬出，因為 default 持有某個範圍的列時，那個範圍不能 attach。
- 修訂 ARCHITECTURE §4.4、§5 原本的寫法。

### C3-D002 去重只靠一個 `dedup_key`（#64）

- `ingest.job.dedup_key TEXT UNIQUE`：`stream:<id>`、`periodic:<name>`、`bucket:<kind>:<lease_key>:<時間點>`（C6 的快照）。一個 index 涵蓋三種情況。
- ReconcileLoop 一律 `INSERT … ON CONFLICT (dedup_key) DO NOTHING`，多副本同時跑也不重複。

### C3-D003 lease 以字串為鍵（#64）

- `ingest.lease.key`：`account:<id>`、`global:mftiko`、`global:sym`…。C3 不依賴 C5 才出現的 `pms.exchange_account`。
- `token` 每次取得加一，job 執行時記下拿到的 token。

### C3-D004 資料的正確性不依賴 lease（#64）

- 每一頁一個 transaction：寫入以 native id 冪等，cursor 以 `ingest.stream.cursor_version` CAS 推進，CAS 失敗整頁 rollback。
- lease 只負責把同一個帳號的限速預算留在一個進程裡。lease 判斷出錯（例如時鐘或續約延遲），最多是同一帳號短暫多一個進程在打交易所，資料不會重複。

### C3-D005 告警走 Prometheus，不在行程裡送出（#68）

- `cpanel-worker` 的 `/metrics` 以資料庫現況為準：沒暫停的 stream 的 lag 與連續失敗、暫停的 stream 數、生效中的 `ingest.pause`、未完成 job 的數量與最老年齡、最近一小時的 `job_run`（依狀態與錯誤類別）。重啟不會漏掉已經寫進資料庫的失敗。
- 規則在 `deploy/prometheus/cpanel.rules.yml`，dashboard 在 `deploy/grafana/cpanel.json`。門檻：lag 超過 15 分鐘持續 10 分鐘；連續失敗至少 3 次持續 5 分鐘；有暫停的 stream 持續 10 分鐘；出口暫停持續 2 分鐘；最老的未完成 job 超過 10 分鐘持續 5 分鐘；最近一小時失敗率超過一半且至少 10 筆、持續 10 分鐘。
- worker 不對公網開 port。Prometheus 從主機或 tailnet 抓容器的 `:8081/metrics`。不另做 Discord webhook，也不新增 secret。
- `AlertHook` 仍只記 log。暫停與連續失敗的 gauge 才是會被送出的告警。
