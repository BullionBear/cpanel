# Database Migration Component

This component handles database migrations using Alembic.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure database connection:
   - Create a `.env` file in this directory with:
     ```
     DATABASE_URL=postgresql://user:password@localhost:5432/dbname
     ```
   - Or source the `env.sh` file from the root directory:
     ```bash
     source ../env.sh
     ```

## Usage

### Create a new migration
```bash
alembic revision --autogenerate -m "Description of changes"
```

### Apply migrations
```bash
alembic upgrade head
```

### Rollback migrations
```bash
alembic downgrade -1
```

### View migration history
```bash
alembic history
```

### Check current revision
```bash
alembic current
```

## Models

The schema's source of truth is `backend/app/models`. This component's
`models/__init__.py` puts the backend on `sys.path` and re-exports its
`Base`, so `alembic revision --autogenerate` compares against the exact
metadata the application uses. Define new models in `backend/app/models`
(and register them in its `__init__.py`), then autogenerate a revision here.

Backend model modules must stay import-safe without app configuration
(SQLAlchemy-only imports — no settings, no network clients).

The legacy model files in `models/` belong to the archived pre-2026-07-13
schema and are no longer imported.

## Project Structure

```
migration/
├── alembic/              # Alembic configuration and migrations
│   ├── versions/         # Migration scripts
│   ├── env.py           # Alembic environment configuration
│   └── script.py.mako   # Migration template
├── models/              # Re-exports Base from backend/app/models (+ legacy files)
├── alembic.ini          # Alembic configuration file
├── requirements.txt     # Python dependencies
└── README.md           # This file
```

