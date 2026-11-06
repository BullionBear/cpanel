# Archive (2026-07-13)

The original CPanel application was archived on 2026-07-13 ahead of a
ground-up rebuild. Nothing in this directory is built or deployed.

```
archive/
├── backend/     Original FastAPI application
└── frontend/    Original SvelteKit application
```

The rebuilt project lives at the repository root with the same structure and
frameworks (`backend/` FastAPI, `frontend/` SvelteKit, `migration/` Alembic).

The corresponding database tables were archived by Alembic revision
`a0b1c2d3e4f5` (every table renamed to `<name>_archived_20260713`).
