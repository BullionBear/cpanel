# AGENTS.md

## Cursor Cloud specific instructions

### Architecture
CPanel is a crypto trading control panel with three components:
- **Backend** (FastAPI, Python 3.12) on port 8186 — see `backend/README.md`
- **Frontend** (SvelteKit 2 / Svelte 4) on port 5173 — see `frontend/README.md`
- **Database** (PostgreSQL 16) with Alembic migrations in `migration/`

### Running services

1. **PostgreSQL**: `sudo pg_ctlcluster 16 main start`
2. **Migrations**: `source /workspace/venv/bin/activate && cd /workspace/migration && alembic upgrade head`
3. **Backend**: `source /workspace/venv/bin/activate && cd /workspace/backend && uvicorn app.main:app --host 0.0.0.0 --port 8186 --reload`
4. **Frontend**: `cd /workspace/frontend && npm run dev -- --host 0.0.0.0`

### Gotchas
- Users registered via the API default to `is_active=false` (email activation required). For local dev, activate manually: `sudo -u postgres psql -d cpanel_db -c "UPDATE users SET is_active = true WHERE username = 'testuser';"`
- The backend reads `.env` from `backend/.env` first, falling back to `migration/.env`. Both should contain `DATABASE_URL`.
- The login endpoint uses `application/x-www-form-urlencoded` (OAuth2 form), not JSON.
- Frontend type-check (`npm run check`) has 2 pre-existing TS errors in `(app)/+layout.svelte` — these are in the existing codebase, not introduced by setup.
- `@sveltejs/kit` v2.50+ emits build warnings about `untrack`/`fork`/`settled` not exported from svelte 4 — safe to ignore; the app builds and runs fine.

### Useful commands
| Task | Command |
|---|---|
| Type check frontend | `cd frontend && npm run check` |
| Build frontend | `cd frontend && npm run build` |
| Swagger docs | `http://localhost:8186/docs` |
| Run backend tests | No test suite configured yet |
