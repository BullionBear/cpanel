# CPanel production deployment

## Layout

- Host: system host (`172.237.19.123`)
- Path: `/root/cpanel/deployment`
- Compose project name: **`cpanel`** (required — many stacks live under `*/deployment` and would otherwise share the default project name `deployment`)
- Secrets: `env.sh` + generated `.env` (never commit)

## Bring-up

```bash
cd /root/cpanel/deployment
source env.sh   # exports DOMAIN, DATABASE_URL, COMPOSE_PROJECT_NAME=cpanel
# Ensure .env exists for compose interpolation (EMAIL/DOMAIN/DATABASE_URL/COMPOSE_PROJECT_NAME)
docker compose -p cpanel up -d --pull never backend frontend
```

Prefer `docker compose -p cpanel` (or `COMPOSE_PROJECT_NAME=cpanel`) so sibling stacks (`hr`, shared `deployment`) are not treated as orphans.

## Health

- API: `GET https://capi.lynxlinkage.com/health` → `{"status":"healthy"}`
- UI: `https://cpanel.lynxlinkage.com` (Authelia-gated)

## Mint CATALOG_API_TOKEN

1. Sign in to `https://cpanel.lynxlinkage.com` via Authelia.
2. Open **Settings** → create API token (calls `POST /api/v2/auth/api-token` with the session JWT).
3. Store the token only in:
   - shell env: `export CATALOG_API_TOKEN=...`
   - or gitignored `config/*.local.yml` in the seq repo
4. Do **not** commit token values or paste them into issue trackers.
