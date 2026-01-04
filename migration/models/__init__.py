from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

# Import all models here so Alembic can detect them
from .user import User  # noqa: E402

__all__ = ["Base", "User"]

