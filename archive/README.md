# Archive

封存的版本只供閱讀。依賴清單（`requirements*.txt`、`package.json`、`package-lock.json`）已移除（C1-D003）；要建置或執行，請 checkout 對應的 tag。

| 版本 | 目錄 | tag | 期間 | 內容 |
|---|---|---|---|---|
| v1 | [`v1-20260713/`](v1-20260713/) | `v1-final`（`a992d00`） | 至 2026-07-13 | 原始的 FastAPI＋SvelteKit 版本 |
| v2 | [`v2-20261106/`](v2-20261106/) | `v2-final`（`9ae8629`） | 2026-07-13 起重建，最後一個 commit 2026-09-29，2026-11-06 封存 | FastAPI＋SvelteKit 重建版：6 家交易所 connector、帳號與 key、快照、歷史、劃轉；設計文件在 `v2-20261106/docs/` |

```sh
git checkout v2-final   # 或 v1-final
```

## 資料庫

- v1、v2 共用正式環境的 `cpanel` 資料庫。v1 的表在 2026-07-13 改名為 `<name>_archived_20260713`（Alembic revision `a0b1c2d3e4f5`）；更早的 `api_archived_20260221`、`wallet_archived_20240221` 也在裡面。v2 的表沒有改名。
- 封存後以權限保證唯讀，交易所 secret 已清除（C1-D001、C1-D005；#43、#44）。v3 使用新的資料庫。
- 存取（#44）：
  - 資料庫與所有表的 owner 是 `cpanel_archive`（`NOLOGIN`）。
  - 唯讀登入用 `cpanel_archive_ro`，只有 `CONNECT`、schema `USAGE` 與 `SELECT`。
  - 原本的 app 角色 `bullionbear` 是 superuser，又擁有同一個 instance 上其他 10 個資料庫，所以不改它的登入權限，改在 `pg_hba.conf` 對 `cpanel` 資料庫 `reject`。
  - `default_transaction_read_only = on` 只是第二道防線。
- 加密備份（2026-11-06，清除 secret 之後）：
  | 項目 | 值 |
  |---|---|
  | 檔案 | `cpanel-20261106.dump.age`（`pg_dump -Fc`，45,013,986 bytes） |
  | 位置 | 維護者筆電的 `~/Archive/cpanel/`，不在資料庫主機上 |
  | 加密 | age，收件公鑰 `age10g79ukp84mnarld3ctuznkxgh886xvcvjfw427xqkup59ga26yuqz4l5cq`；私鑰離線保存 |
  | sha256（加密檔） | `3760fa3abc6c80c9f13f2b7c92336d725ab3f4fce33ccc2895e759842302be75` |
  | sha256（解密後的 dump） | `ff0c6a20b5c33641258f2c90abb6492c9c1aeaf7e7d8f01398cf89783eaa8026` |
  | 還原驗證 | 在筆電上的 Postgres 17 容器還原；39 張表的列數與來源一致，沒有未清除的 secret |

  ```sh
  age -d -i <私鑰> cpanel-20261106.dump.age | pg_restore --no-owner --no-acl -d <db>
  ```

## Image

- `ghcr.io/bullionbear/cpanel-backend:v2-final`、`ghcr.io/bullionbear/cpanel-frontend:v2-final`：停機前正式環境執行中的 digest（C1-D006，#42）。
  - backend `sha256:0f2d2611623ada9e1344803ba7540b963829de4c1322b310fd02668939952b4a`
  - frontend `sha256:202c9da3e219566de8fcece748a244af22cca52252a31d97ddfeb3ec74d0145a`

## 舊分支

- 2026-11-06 時沒 merge 的分支保存為 tag `archive/v2/<branch>`（C1-D004）。

## 封存紀錄

- C1 封存 v2：#32
