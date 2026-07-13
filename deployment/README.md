# CPanel production deployment

## Layout

- Host: system host (`172.237.19.123`)
- Path: `/root/cpanel/deployment`
- Compose project name: **`cpanel`** (required — many stacks live under `*/deployment` and would otherwise share the default project name `deployment`)
- Secrets: `env.sh` (never commit). Exports `DOMAIN`, `DATABASE_URL`, `FRONTEND_URL`, `COMPOSE_PROJECT_NAME=cpanel`.

The stack runs the rebuilt backend and frontend (fresh scaffolds as of
2026-07-13). The previous application lives under `archive/` in the repo; its
Discord OAuth env vars are no longer consumed by compose — keep them in
`env.sh` if you plan to reintroduce auth.

## Bring-up

```bash
cd /root/cpanel/deployment
source env.sh
docker compose -p cpanel up -d --pull never backend frontend
```

Prefer `docker compose -p cpanel` (or `COMPOSE_PROJECT_NAME=cpanel`) so sibling stacks (`hr`, shared `deployment`) are not treated as orphans.

## Health

- API: `GET https://capi.lynxlinkage.com/health` → `{"status":"healthy"}`
- API build info: `GET https://capi.lynxlinkage.com/status`
- UI: `https://cpanel.lynxlinkage.com` → landing page

## Notes

- All previous database tables were archived (renamed to
  `<name>_archived_20260713`) by Alembic revision `a0b1c2d3e4f5`. The migration
  pipeline (`migrate.yml`) still runs `alembic upgrade head` on every push to
  `main`, and the rebuilt schema will grow from new models in
  `migration/models/`.
- Auth (Discord OAuth) and the old API surface are not part of the fresh
  scaffold yet; see `archive/backend/` for the previous implementation.
