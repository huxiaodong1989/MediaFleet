"""Alembic 迁移环境，数据库连接统一复用项目 Settings。"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from media_platform.common.config import get_settings
from media_platform.infrastructure.database.driver import ensure_dm_driver
from media_platform.infrastructure.database.url import build_dm_connect_args
from media_platform.infrastructure.database.base import Base

# Register current MediaFleet models for autogenerate.
import media_platform.infrastructure.database.models  # noqa: F401,E402
import services.content_analysis.infrastructure.models  # noqa: F401,E402


config = context.config
if (
    config.config_file_name is not None
    and not config.attributes.get("skip_logging_config")
):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
VERSION_TABLE = "schema_version"


def _database_settings() -> tuple[str, dict]:
    settings = get_settings()
    connect_args: dict = {}
    if settings.DB_TYPE.lower() == "dm":
        ensure_dm_driver()
        connect_args = build_dm_connect_args(
            settings.DB_NAME,
            settings.database.dm_mysql_compat,
        )
    return settings.DB_URL, connect_args


def run_migrations_offline() -> None:
    """生成离线 SQL，不建立数据库连接。"""

    database_url, _ = _database_settings()
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """连接目标数据库执行版本化迁移。"""

    database_url, connect_args = _database_settings()
    engine = create_engine(
        database_url,
        poolclass=pool.NullPool,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
                version_table=VERSION_TABLE,
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
