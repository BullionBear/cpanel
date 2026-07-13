# CPanel Backend

FastAPI application, rebuilt from scratch on 2026-07-13 (previous version in
`../archive/backend/`).

## Structure

```
backend/
├── app/
│   ├── api/v1/        API routers
│   ├── core/          Settings
│   └── main.py        App entrypoint (/, /health, /status)
├── requirements.txt
└── run.sh             Dev server on port 8186
```

## Development

```bash
python3 -m venv ../venv
source ../venv/bin/activate
pip install -r requirements.txt
cp .env.dev.example .env
./run.sh
```

Database models live in `../migration/models/` and are managed with Alembic.
