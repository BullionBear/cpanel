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
- 加密備份的 checksum 與存放位置：待 #44 補上。

## Image

- `ghcr.io/bullionbear/cpanel-backend:v2-final`、`ghcr.io/bullionbear/cpanel-frontend:v2-final`：停機前正式環境執行中的 digest（C1-D006）。digest 待 #42 補上。

## 舊分支

- 2026-11-06 時沒 merge 的分支保存為 tag `archive/v2/<branch>`（C1-D004）。

## 封存紀錄

- C1 封存 v2：#32
