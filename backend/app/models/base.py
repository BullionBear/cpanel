from decimal import Decimal

from sqlalchemy import Numeric, String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class PreciseDecimal(TypeDecorator):
    """Exact decimal storage: NUMERIC on Postgres, TEXT on SQLite.

    SQLite has no fixed-point type — SQLAlchemy's Numeric silently goes
    through float there, corrupting tick sizes (0.1 -> 0.100000000000000006).
    """

    impl = Numeric(36, 18)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(36, 18))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value))
