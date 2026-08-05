from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Without an explicit convention, SQLAlchemy lets postgres auto-name most constraints and Alembic
# then emits migrations that can't drop or alter them by name. Setting it once here means every
# index/FK/unique gets a deterministic name we can reference in a later migration.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
