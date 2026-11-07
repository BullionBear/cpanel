# cpanel

cpanel 是 [mftiko](https://github.com/BullionBear/mftiko) 的外部 PMS：管理交易所帳號與唯讀 key，持續落庫訂單、成交、funding 歷史，並定時取餘額與持倉快照。

目前正在以 Go 重建（v3）。v1、v2 已封存，見 [`archive/`](archive/README.md)。

## 文件

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：架構、與 mftiko 的邊界、不變式
- [docs/PLAN.md](docs/PLAN.md)：一期的 epic 與 ticket
- [docs/DECISIONS.md](docs/DECISIONS.md)：決策紀錄

## 進度

一期 8 個 epic：#32（C1 封存 v2）到 #39（C8 其他 venue）。v3 的程式碼從 C2（#33）開始放進這個 repo。

## 與 mftiko 的關係

cpanel 只使用四樣輸入（mftiko E21-D085）：mftiko API（只發 GET）、SYM（gRPC）、cpanel 自己的唯讀交易所 key、交易所 REST。不連 mftiko 的 etcd、資料庫或 `td.evt`。

## 目錄

```
archive/                     v1、v2 的程式碼，只供閱讀
cmd/cpanel-api/              公網的 HTTP server（I6：連不到解封 secret 的程式）
cmd/cpanel-worker/           job worker；對 mftiko、SYM、交易所的呼叫都在這裡
cmd/cpanel-migrate/          套用 embed 的 SQL migration；啟動時不 migrate 的檢查在 api 與 worker
contract/openapi/            cpanel.v1.yaml；Redocly、oapi-codegen、oasdiff
contract/gen/cpanel/v1/      由契約產生的 server 與型別，要 commit
web/                         SvelteKit SPA。`src/lib/gen/api/v1.ts` 由契約產生，要 commit
web/build/                   SvelteKit 的建置結果；cpanel-api embed
migrations/                  NNNN_name.up.sql／.down.sql，版本表 meta.schema_migrations
internal/api/                cpanel-api 的 OpenAPI handler、middleware、SPA
internal/archtest/           以 go list 檢查 I1、I5、I6
internal/mftiko/             釘住 mftiko 的 contract（C2-D007）
internal/platform/           設定、log、HTTP server、版本、測試用 Postgres（pgtest）
internal/reconcile/          ReconcileLoop（C3；I5）
deploy/dev/compose.yml       本機開發用的 Postgres 17
docs/                        v3 的文件
```

## 開發

Go 1.26（`go.mod`）。`make help` 列出所有 target；CI 在每個 PR 與 push `main` 時跑 `make check`（gofmt、`go mod tidy`、OpenAPI lint、oasdiff、gen-check、svelte-check、SPA build、vet、build、test，archtest 包含在 test 裡）。

```
make dev-db   # 本機 Postgres 17，postgres://cpanel:cpanel@127.0.0.1:5432/cpanel?sslmode=disable
make openapi  # 重新產生 contract/gen 與 web/src/lib/gen/api/v1.ts，然後 commit
make web      # 建置 SPA 到 web/build
make web-dev  # Vite。把 /auth、/me、/tokens 代理到 127.0.0.1:8080 的 cpanel-api
make check    # 與 CI 相同。Postgres 測試在找不到 binary 時跳過；CI 設 CPANEL_REQUIRE_PG=1，找不到就失敗
make build    # bin/cpanel-api、bin/cpanel-worker、bin/cpanel-migrate，並寫入版本與 commit
```

`make web-dev` 時，把 `CPANEL_PUBLIC_ORIGIN` 設成 Vite 的 origin（預設 `http://localhost:5173`）。cookie 的寫入請求會帶這個 Origin，OAuth redirect 也從這裡組出來。

`cpanel-api` 提供 `GET /healthz`（只看行程，帶版本、commit 與建置時間）、`GET /readyz`（資料庫連得上且 schema 是目前版本，否則 503 `application/problem+json`）、`GET /metrics`（Prometheus）。

`cpanel-worker` 跑 ReconcileLoop（`internal/reconcile`）：預設每 5 秒（`-reconcile-interval`）把到期的 stream 與 periodic 寫成 `ingest.job`，每個副本都跑，不選主。同一個行程也跑 job runner（`internal/worker`）：以 lease 認領 job 並執行，同時跑 `-job-slots`（預設 4）個 lease key；目前只有 `maint.partitions`（`job_run` 的分區維護）。錯誤依類別處理：429 延後整個 lease key，418 暫停同一個 `-egress`，參數錯誤暫停該 stream。SIGTERM 時做完進行中的頁、交還 job 並釋放 lease。worker 只在私網提供 `GET /healthz` 與 `GET /metrics`。

Image 是 `ghcr.io/bullionbear/cpanel`。同一個 image 有 `cpanel-api`、`cpanel-worker`、`cpanel-migrate`，預設跑 api；換成 `/usr/local/bin/cpanel-worker` 或 `/usr/local/bin/cpanel-migrate` 就跑另一個。push `main` 會推 `:main` 與 `:<12 碼 commit>`，push `v*` tag 會推該 tag。這個 workflow 只建置與發布，不連主機。`/healthz` 的 `commit` 與 `main` 上的 commit tag 相同。其他路徑回傳嵌入的 SPA：未知路徑是 `index.html`（不快取），`_app/immutable/*` 長快取。錯誤一律是 `application/problem+json`。oasdiff 比較 `OAS_BASE`（預設 `origin/main`）；那個 commit 還沒有契約時略過，不視為失敗。

登入是 Discord OAuth。使用者必須在 `-discord-guild-id` 裡，而且有 `-discord-role-id`。cookie `cpanel_session` 是 `HttpOnly`、`Secure`、`SameSite=Strict`，資料庫只存 SHA-256。bearer token 前綴是 `cpanel_`，明文只在建立時出現一次。cookie 的寫入請求要帶與 `-public-origin` 相同的 `Origin`。Discord 的 client secret 與 bot token 沒設時，行程仍會啟動，`/auth/discord/login` 回 503。正式環境要在既有的 Discord application 加上 redirect URI：`{CPANEL_PUBLIC_ORIGIN}/auth/discord/callback`。

設定是 flag，預設值來自環境變數，flag 優先；`<command> -h` 列出各自的設定與對應的環境變數。啟動時會以 JSON log 印出生效的設定，secret 以 `***` 遮掉。資料庫是 `-database`／`$CPANEL_DATABASE_URL`。`cpanel-migrate up` 才會改 schema；`cpanel-api` 與 `cpanel-worker` 只檢查版本，落後就拒絕啟動。
