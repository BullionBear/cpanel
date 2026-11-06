# CPanel production deployment

## Layout

- Host: system host (`172.237.19.123`)
- Path: `/root/cpanel/deployment`
- Compose project name: **`cpanel`** (required — many stacks live under `*/deployment` and would otherwise share the default project name `deployment`)
- Secrets: `env.sh` (never commit). Exports `DOMAIN`, `DATABASE_URL`, `FRONTEND_URL`,
  `SECRET_KEY`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`,
  `DISCORD_GUILD_ID`, `DISCORD_REQUIRED_ROLE_ID` (optional), `DISCORD_REDIRECT_URI`,
  `COMPOSE_PROJECT_NAME=cpanel`.

The stack runs the rebuilt backend and frontend (fresh scaffolds as of
2026-07-13). The previous application lives under `archive/` in the repo.

The rebuilt API is **unversioned and has no `/api` prefix**: routes live at the
root (`/auth/...`, `/ping`, `/health`, `/status`). This means:

- `DISCORD_REDIRECT_URI` must be `https://capi.lynxlinkage.com/auth/discord/callback`
  — update it in `env.sh` **and** in the Discord Developer Portal (the old value
  ended with `/api/v2/auth/discord/callback`).

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
- Auth is Discord OAuth (guild membership required, optional role check). No
  users table yet — the JWT carries the Discord identity directly.
