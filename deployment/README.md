# CPanel production deployment

## Layout

- Host: system host (`172.237.19.123`)
- Path: `/root/cpanel/deployment`
- Compose project name: **`cpanel`** (required — many stacks live under `*/deployment` and would otherwise share the default project name `deployment`)
- Secrets: `env.sh` + generated `.env` (never commit)

## Bring-up

```bash
cd /root/cpanel/deployment
source env.sh   # exports DOMAIN, DATABASE_URL, Discord OAuth vars, COMPOSE_PROJECT_NAME=cpanel
# Ensure .env exists for compose interpolation
docker compose -p cpanel up -d --pull never backend frontend
```

Prefer `docker compose -p cpanel` (or `COMPOSE_PROJECT_NAME=cpanel`) so sibling stacks (`hr`, shared `deployment`) are not treated as orphans.

## Auth (Discord OAuth)

CPanel uses Discord OAuth instead of Authelia. Users must be members of the configured Discord guild (and optionally have a required role).

Required env vars in `.env` / `env.sh`:

| Variable | Example |
|----------|---------|
| `SECRET_KEY` | JWT signing key |
| `DISCORD_CLIENT_ID` | From Discord Developer Portal |
| `DISCORD_CLIENT_SECRET` | From Discord Developer Portal |
| `DISCORD_BOT_TOKEN` | Bot token (guild member lookup) |
| `DISCORD_GUILD_ID` | Target server ID |
| `DISCORD_REQUIRED_ROLE_ID` | Optional role ID |
| `DISCORD_REDIRECT_URI` | `https://capi.lynxlinkage.com/api/v2/auth/discord/callback` |
| `FRONTEND_URL` | `https://cpanel.lynxlinkage.com` |

Discord Application redirect URL must match `DISCORD_REDIRECT_URI`. OAuth scopes: `identify`, `email`.

Run migrations before or after deploy when `users.discord_id` is new:

```bash
cd /root/cpanel/migration && alembic upgrade head
```

## Health

- API: `GET https://capi.lynxlinkage.com/health` → `{"status":"healthy"}`
- UI: `https://cpanel.lynxlinkage.com` → Discord sign-in

## Mint CATALOG_API_TOKEN

1. Sign in to `https://cpanel.lynxlinkage.com` via Discord.
2. Open **Settings** → create API token (calls `POST /api/v2/auth/api-token` with the session JWT).
3. Store the token only in:
   - shell env: `export CATALOG_API_TOKEN=...`
   - or gitignored `config/*.local.yml` in the seq repo
4. Do **not** commit token values or paste them into issue trackers.
