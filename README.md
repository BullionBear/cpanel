# CPanel

Trading dashboard with a FastAPI backend and SvelteKit frontend.

## Repository layout

```
cpanel/
├── backend/          FastAPI application
├── frontend/         SvelteKit application
├── migration/        Alembic database migrations
├── docker/           Dockerfiles (Dockerfile.backend, Dockerfile.frontend)
└── deployment/       Production compose file for this service only
```

## CI/CD pipeline

Three GitHub Actions workflows run on every push to `main`:

### 1. Build and Push Docker Images (`docker.yml`)

Builds and pushes both images to GHCR on every `main` push or tag.

| Image | Tag (branch) | Tag (git tag) |
|---|---|---|
| `ghcr.io/bullionbear/cpanel-backend` | `latest`, `<short-sha>` | `<tag>` |
| `ghcr.io/bullionbear/cpanel-frontend` | `latest`, `<short-sha>` | `<tag>` |

### 2. Migrate Database (`migrate.yml`)

Runs `alembic upgrade head` against the production database on every `main` push, in parallel with the Docker build.

### 3. Deploy to Production (`deploy.yml`)

Triggered automatically once the Docker build succeeds. SSHes into the production server and runs:

```bash
cd /root/cpanel/deployment
source env.sh
docker compose pull backend frontend
docker compose up -d backend frontend
docker image prune -f
```

Sends a Discord notification on success or failure.

### Workflow sequence

```
push to main
├── docker.yml  ──────────────────────────────► deploy.yml (on success)
│                                                  └─ SSH → server → docker compose up
└── migrate.yml (Alembic upgrade head)
```

### Required GitHub secrets

| Secret | Used by |
|---|---|
| `GHCR_TOKEN` | `docker.yml` — push images to GHCR |
| `SSH_PRIVATE_KEY` | `deploy.yml` — SSH into production server |
| `DATABASE_URL` | `migrate.yml` — Alembic target database |
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

`env.sh` exports `DOMAIN` and `DATABASE_URL`. It is git-ignored and must be created on the server.

## Database migrations

```bash
cd migration
pip install -r requirements.txt
DATABASE_URL=<dsn> alembic upgrade head   # apply
DATABASE_URL=<dsn> alembic downgrade -1   # roll back one
```

See `migration/README.md` for details.
