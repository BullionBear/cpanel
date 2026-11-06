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
migrations/                  NNNN_name.up.sql／.down.sql，版本表 meta.schema_migrations
internal/archtest/           以 go list 檢查 I1、I5、I6
internal/mftiko/             釘住 mftiko 的 contract（C2-D007）
internal/platform/           設定、log、HTTP server、測試用 Postgres（pgtest）
internal/reconcile/          ReconcileLoop（C3；I5）
deploy/dev/compose.yml       本機開發用的 Postgres 17
docs/                        v3 的文件
```

## 開發

Go 1.26（`go.mod`）。`make help` 列出所有 target；CI 在每個 PR 與 push `main` 時跑 `make check`（gofmt、`go mod tidy`、vet、build、test，archtest 包含在 test 裡）。

```
make dev-db  # 本機 Postgres 17，postgres://cpanel:cpanel@127.0.0.1:5432/cpanel?sslmode=disable
make check   # 與 CI 相同。Postgres 測試在找不到 binary 時跳過；CI 設 CPANEL_REQUIRE_PG=1，找不到就失敗
make build   # bin/cpanel-api、bin/cpanel-worker、bin/cpanel-migrate
```

設定是 flag，預設值來自環境變數，flag 優先；`<command> -h` 列出各自的設定與對應的環境變數。啟動時會以 JSON log 印出生效的設定，secret 以 `***` 遮掉。資料庫是 `-database`／`$CPANEL_DATABASE_URL`。`cpanel-migrate up` 才會改 schema；`cpanel-api` 與 `cpanel-worker` 只檢查版本，落後就拒絕啟動。
