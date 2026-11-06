"""Alembic model registry.

The schema's single source of truth is ``backend/app/models``; this package
just puts the backend on sys.path and re-exports its Base so Alembic
autogenerate compares against the same metadata the application uses.
Backend model modules must therefore stay import-safe without app config
(SQLAlchemy-only imports).

The original CPanel schema was archived on 2026-07-13: every table was
renamed to <name>_archived_20260713 (see alembic revision a0b1c2d3e4f5).
The legacy model files remain in this package for reference but are no
longer imported.
"""
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.models import Base  # noqa: E402

__all__ = ["Base"]
