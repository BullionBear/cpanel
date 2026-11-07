# cpanel v3 — 營運

部署、回滾、secret 輪換。依據 C2-D003、C2-D008–C2-D013（[DECISIONS.md](DECISIONS.md)）。

## 拓撲

| 元件 | 位置 | 對外 |
|---|---|---|
| cpanel-api | `172.237.19.123`，容器 `cpanel-api`，network `web_proxy` | 只經 Traefik：`https://cpanel.lynxlinkage.com` |
| cpanel-worker | 同一台，容器 `cpanel-worker`，network `cpanel_default` | 不開 port；出口 IP 是 `172.237.19.123` |
| cpanel-migrate | 同一台，部署時一次性執行 | — |
| Postgres 17 | `100.65.26.119:5432`（Tailscale），database `cpanel_v3`，role `cpanel_v3` | 只在 Tailscale |
| mftiko-api | `http://100.65.26.119:8080/api/v1`（Tailscale） | — |
| SYM gRPC | `http://100.65.26.119:7450`（Tailscale，h2c） | — |

- 主機在 tailnet 上，容器的流量經主機路由，所以容器直接連得到 `100.65.26.119`。不經 `mftiko-proxy`：那是讓 Traefik 從公網轉到 mftiko console 的，cpanel 走私網即可（C2-D010）。
- `capi.lynxlinkage.com` 永久轉址到 `cpanel.lynxlinkage.com` 的同一個路徑。
- Discord OAuth 的 redirect URI：`https://cpanel.lynxlinkage.com/auth/discord/callback`。

## 主機上的檔案

```
/root/cpanel/deployment/
  compose.yml          # = repo 的 deploy/host/compose.yml
  deploy.sh            # = repo 的 deploy/host/deploy.sh
  config.env           # 不是 secret 的設定，見 deploy/host/config.env.example
  secrets/             # 0700 root
    database_url           # postgres://cpanel_v3:…@100.65.26.119:5432/cpanel_v3?sslmode=disable
    discord_client_secret
    discord_bot_token
    mftiko_token
    sym_token
  state/
    current            # 正在跑的 digest
    previous           # 上一個 digest
    lock
```

- secret 檔是 `0600`，owner `65532:65532`（image 的 `nonroot`）。compose 的 file secret 是 bind mount，不改 owner；不是 65532 的話容器讀不到。
- 容器的環境變數只有路徑（`CPANEL_…_FILE=/run/secrets/…`），沒有值（C2-D008）。`docker inspect` 看不到 secret。
- 改 repo 的 `deploy/host/` 後，手動複製到主機：

  ```sh
  scp deploy/host/compose.yml deploy/host/deploy.sh root@172.237.19.123:/root/cpanel/deployment/
  ```

## 部署

1. 推 tag（`v3.0.0` 之類）或 merge 到 `main`。`release` workflow build 並推 `ghcr.io/bullionbear/cpanel`，Discord 通知裡有 digest。也可以查：

   ```sh
   docker buildx imagetools inspect ghcr.io/bullionbear/cpanel:v3.0.0 --format '{{json .Manifest.Digest}}'
   ```

2. 在主機上：

   ```sh
   /root/cpanel/deployment/deploy.sh sha256:<digest>
   ```

   - 拉 image（以 digest，不以 tag）。
   - `cpanel-migrate up`。失敗就停，正在跑的版本不動，exit 1。
   - 換掉 `cpanel-api`、`cpanel-worker`，等 cpanel-api `/readyz` 與 cpanel-worker `/healthz` 都 200（預設 90 秒，`READY_TIMEOUT` 可改）。從主機以容器 IP 檢查，image 裡沒有 shell 與 curl。
   - 通過：`state/current` 換成新的，舊的進 `state/previous`。
   - 不通過：印出兩個容器最後 30 行 log，換回 `state/current` 的 digest，exit 1。

3. 驗證：

   ```sh
   curl -s https://cpanel.lynxlinkage.com/healthz        # version、commit 對得上 tag
   docker exec cpanel-worker /usr/local/bin/cpanel-worker doctor
   ```

   `doctor` 以 GET 打 mftiko `/accounts`、SYM 的 `grpc.health.v1` 與 `Revisions`，每項一行，全過 exit 0。

## 回滾

```sh
/root/cpanel/deployment/deploy.sh --rollback      # 部署 state/previous，不跑 migrate
/root/cpanel/deployment/deploy.sh sha256:<更早的>  # 任一 digest
```

**有 migration 的版本不能往回滾。** 舊的 binary 看到它不認識的 migration 會拒絕啟動（`migrations.RequireCurrent`，drift `missing`）。所以：

- 一個版本的 migration 跑了、但 `/readyz` 不過：自動換回會失敗，`deploy.sh` 會說要往前修。修好再部署新的 digest。
- migration 本身失敗：每個檔各自一個 transaction，失敗的檔不留痕跡；舊版照常運作。
- 真要退掉一個 migration：手動跑對應的 `.down.sql`、刪掉 `meta.schema_migrations` 那一列，再部署舊 digest。這是例外處理，先備份。

## 資料庫

- 一次性建立（superuser 在 `100.65.26.119` 上執行）：

  ```sh
  PW=$(openssl rand -hex 24)
  psql -v ON_ERROR_STOP=1 -v password="$PW" -d postgres -f deploy/host/database.sql
  ```

- `pg_hba.conf` 最前面（first match wins），之後 `SELECT pg_reload_conf()`：

  ```
  host  cpanel_v3  cpanel_v3  100.127.116.76/32  scram-sha-256
  host  all        cpanel_v3  0.0.0.0/0          reject
  host  all        cpanel_v3  ::/0               reject
  local all        cpanel_v3                     reject
  ```

  `100.127.116.76` 是 `172.237.19.123` 的 Tailscale IP；容器連出去時來源是它。
- 驗證：

  ```sh
  psql "$(cat secrets/database_url)" -c 'select rolsuper, rolcreaterole, rolcreatedb from pg_roles where rolname = current_user'  # f f f
  psql "postgres://cpanel_v3:…@100.65.26.119/hr" -c 'select 1'                                                              # rejected
  ```

- 不用 `bullionbear`：它是 superuser、擁有其他資料庫，密碼曾外洩到 shell 紀錄（#45）。

## secret 輪換

每個 secret 都是一個檔。換的步驟一樣：寫新檔、`chown 65532:65532`、`chmod 600`，再重建用到它的容器。

```sh
cd /root/cpanel/deployment
install -m 600 -o 65532 -g 65532 /dev/stdin secrets/<name> <<<'<新值>'
CPANEL_IMAGE=ghcr.io/bullionbear/cpanel@$(cat state/current) docker compose -p cpanel up -d --force-recreate cpanel-api cpanel-worker
```

| secret | 怎麼換 | 用到的容器 |
|---|---|---|
| `database_url` | `ALTER ROLE cpanel_v3 PASSWORD '…'`，同時更新檔案 | 全部 |
| `discord_client_secret` | Discord Developer Portal → OAuth2 → Reset Secret | cpanel-api |
| `discord_bot_token` | Developer Portal → Bot → Reset Token | cpanel-api |
| `mftiko_token` | mftiko 那邊發新的 PMS token，換完撤舊的 | cpanel-worker |
| `sym_token` | `/etc/mftiko/sym/grpc-tokens` 加一行新的（例如 `cpanel-2`）、重啟 SYM、換檔、驗證 doctor、刪舊行再重啟 SYM（mftiko operations「輪換 gRPC 的 token」） | cpanel-worker |

換完跑一次 `doctor`；Discord 的換完從瀏覽器登入一次。
