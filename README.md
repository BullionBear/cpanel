# CPanel

Trading control panel with a FastAPI backend and SvelteKit frontend, being
rebuilt from scratch (started 2026-07-13). The previous application is
archived under `archive/`; the rebuilt apps keep the same structure and
frameworks. The frontend root route currently serves the public landing page.

## Repository layout

```
cpanel/
├── backend/          FastAPI application (fresh scaffold)
├── frontend/         SvelteKit application (fresh scaffold, landing page)
├── migration/        Alembic database migrations (all old tables archived 2026-07-13)
├── docker/           Dockerfiles (Dockerfile.backend, Dockerfile.frontend)
├── deployment/       Production compose file for this service only
└── archive/          Archived previous backend and frontend
```

## Local development

```bash
docker compose -f docker-compose.dev.yml up -d   # local Postgres 16

cd backend && ./run.sh                            # API on :8186
cd frontend && npm install && npm run dev        # UI on :5173
```

## CI/CD pipeline

Two GitHub Actions workflows run on every push to `main`:

### 1. Build and Push Docker Images (`docker.yml`)

Builds and pushes both images to GHCR on every `main` push or tag.

| Image | Tag (branch) | Tag (git tag) |
|---|---|---|
| `ghcr.io/bullionbear/cpanel-backend` | `latest`, `<short-sha>` | `<tag>` |
| `ghcr.io/bullionbear/cpanel-frontend` | `latest`, `<short-sha>` | `<tag>` |

### 2. Deploy to Production (`deploy.yml`)

Triggered automatically once the Docker build succeeds. Copies `migration/` and
`backend/app/models/` (which the migrations import) to
`/root/cpanel/deployment/migration-src` on the production server, then SSHes in and runs:

```bash
cd /root/cpanel/deployment
source env.sh
docker compose pull backend frontend
# Alembic in a throwaway python:3.12-slim container, against $DATABASE_URL from env.sh
docker run --rm -e DATABASE_URL -v "$PWD/migration-src:/src:ro" -w /src/migration \
  python:3.12-slim sh -c "pip install -r requirements.txt && alembic upgrade head"
docker compose up -d --remove-orphans backend frontend
docker image prune -f
```

Migrations run on the server because the database is only reachable from there
(over Tailscale), not from GitHub-hosted runners. A failed migration stops the
deploy before any container is restarted.

Sends a Discord notification on success or failure. Notifications include the
repository, branch, commit link, and the commit message.

### Workflow sequence

```
push to main
└── docker.yml ──► deploy.yml (on success)
                     └─ SSH → server → alembic upgrade head → docker compose up
```

### Required GitHub secrets

| Secret | Used by |
|---|---|
| `GHCR_TOKEN` | `docker.yml` — push images to GHCR |
| `SSH_PRIVATE_KEY` | `deploy.yml` — SSH into production server |
| `DISCORD_WEBHOOK` | `deploy.yml` — deployment notifications |

## Production deployment

Services run on `172.237.19.123` behind the shared Traefik reverse proxy (managed in `/root/deployment/`).

| Container | Route | Internal port |
|---|---|---|
| `cpanel-backend` | `capi.lynxlinkage.com` | 8186 |
| `cpanel-frontend` | `cpanel.lynxlinkage.com` | 80 |

Both containers join the shared `web_proxy` Docker network so Traefik can discover them.

### Manual deploy

```bash
cd /root/cpanel/deployment
source env.sh
docker compose up -d
```

`env.sh` exports `DOMAIN`, `DATABASE_URL`, `FRONTEND_URL`, and
`COMPOSE_PROJECT_NAME=cpanel`. It is git-ignored and must be created on the server.

## Database migrations

All tables of the previous schema were renamed to `<name>_archived_20260713`
by revision `a0b1c2d3e4f5`. The Alembic pipeline stays in place for the
rebuild; new models go in `backend/app/models/` (the single source of truth —
`migration/models/` re-exports its metadata for autogenerate).

```bash
cd migration
pip install -r requirements.txt
DATABASE_URL=<dsn> alembic upgrade head   # apply
DATABASE_URL=<dsn> alembic downgrade -1   # roll back one
```

See `migration/README.md` for details.

## Archive

`archive/` holds the previous application as-is (FastAPI backend and SvelteKit
frontend). Nothing in it is built or deployed — see `archive/README.md`.
