"""新旧数据库模型共用的 SQLAlchemy 声明基类。"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


NAMING_CONVENTION = {
    "ix": "idx_%(table_name)s_%(column_0_name)s",
    "uq": "uk_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """为 Alembic 和开发环境自动建表提供统一元数据。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


__all__ = ["Base", "NAMING_CONVENTION"]
