from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# The original CPanel schema was archived on 2026-07-13: every table was
# renamed to <name>_archived_20260713 (see alembic revision a0b1c2d3e4f5).
# The legacy model files remain in this package for reference but are no
# longer imported, so the metadata starts empty for the rebuilt project.
#
# Define and import new models here as the new schema takes shape.

__all__ = ["Base"]
