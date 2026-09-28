"""数据库自动初始化：建库/Schema、建表、增量补丁。"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from media_platform.common.settings import Settings, get_settings
from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.dialect import get_json_ddl_type
from media_platform.infrastructure.database.driver import ensure_dm_driver
from media_platform.infrastructure.database.url import (
    build_admin_db_url,
    build_dm_connect_args,
)
import media_platform.infrastructure.database.models  # noqa: F401
import services.content_analysis.infrastructure.models  # noqa: F401

logger = logging.getLogger(__name__)


def _ensure_mysql_database(settings: Settings) -> None:
    from sqlalchemy_utils import create_database, database_exists

    url = settings.DB_URL
    if database_exists(url):
        logger.info("MySQL 数据库已存在: %s", settings.DB_NAME)
        return

    logger.info("创建 MySQL 数据库: %s", settings.DB_NAME)
    create_database(url, encoding=settings.database.charset)


def _schema_exists_dm(conn, schema_name: str) -> bool:
    result = conn.execute(
        text(
            "SELECT COUNT(*) FROM SYS.SYSOBJECTS "
            "WHERE TYPE$ = 'SCH' AND NAME = :schema_name"
        ),
        {"schema_name": schema_name.upper()},
    )
    count = result.scalar()
    return bool(count and count > 0)


def _dm_connect_args(settings: Settings) -> dict:
    return build_dm_connect_args(
        settings.DB_NAME,
        settings.database.dm_mysql_compat,
    )


def _ensure_dm_schema(settings: Settings) -> None:
    ensure_dm_driver()
    admin_url = build_admin_db_url(
        settings.DB_TYPE,
        settings.DB_USER,
        settings.DB_PASSWORD,
        settings.DB_HOST,
        settings.DB_PORT,
        settings.database.charset,
    )
    schema_name = settings.DB_NAME.upper()
    user = settings.DB_USER.upper()

    engine = create_engine(
        admin_url,
        pool_pre_ping=True,
        connect_args={"connection_timeout": 10},
    )
    try:
        with engine.connect() as conn:
            if _schema_exists_dm(conn, schema_name):
                logger.info("达梦 Schema 已存在: %s", schema_name)
                return

            ddl = text(
                f'CREATE SCHEMA "{schema_name}" AUTHORIZATION "{user}"'
            )
            conn.execute(ddl)
            conn.commit()
            logger.info("创建达梦 Schema: %s", schema_name)
    finally:
        engine.dispose()


def ensure_database_or_schema(settings: Settings) -> None:
    """幂等创建 MySQL 库或达梦 Schema。"""
    if not settings.database.init_create_schema:
        logger.info("DB_INIT_CREATE_SCHEMA=false，跳过建库/Schema")
        return

    db_type = settings.DB_TYPE.lower()
    if db_type == "mysql":
        _ensure_mysql_database(settings)
    elif db_type == "dm":
        _ensure_dm_schema(settings)
    else:
        raise ValueError(f"不支持的数据库类型: {db_type}")


def ensure_tables(engine: Engine) -> None:
    """根据 ORM 模型幂等创建表。"""
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("数据库表结构已就绪")


def run_schema_migrations(settings: Settings) -> None:
    """执行 Alembic 版本化迁移到最新版本。

    `create_all(checkfirst=True)` 只能创建缺失表，不能删除字段、调整唯一约束或
    回填迁移数据。开发/测试阶段允许随服务启动自动执行 Alembic；生产环境如果
    担心多实例并发 DDL，应设置 `DB_AUTO_MIGRATE=false`，由发布流水线单独执行。
    """

    if not settings.database.auto_migrate:
        logger.info("DB_AUTO_MIGRATE=false，跳过数据库版本化迁移")
        return

    repo_root = Path(__file__).resolve().parents[3]
    config_path = repo_root / "alembic.ini"
    if not config_path.exists():
        raise RuntimeError(f"找不到 Alembic 配置文件: {config_path}")

    logger.info("开始执行数据库版本化迁移: alembic upgrade head")
    alembic_config = Config(str(config_path))
    # 服务启动阶段执行迁移时不能让 Alembic 重新配置 root logger，否则迁移完成后
    # 调用中心的任务发布、事件消费和心跳处理日志会被 alembic.ini 的 WARN 级别覆盖。
    alembic_config.attributes["skip_logging_config"] = True
    command.upgrade(alembic_config, "head")
    logger.info("数据库版本化迁移完成")


def apply_schema_patches(engine: Engine, settings: Settings) -> None:
    """检查并补齐 ORM 与历史 SQL 脚本之间的增量差异。"""
    inspector = inspect(engine)
    if not inspector.has_table("tasks"):
        return

    columns = {col["name"].lower() for col in inspector.get_columns("tasks")}
    if "callback_result" not in columns:
        json_type = get_json_ddl_type(
            settings.DB_TYPE, settings.database.dm_mysql_compat
        )
        ddl = f"ALTER TABLE tasks ADD callback_result {json_type} NULL"
        with engine.begin() as conn:
            conn.execute(text(ddl))
        logger.info("已为 tasks 表添加 callback_result 列")


def init_database(settings: Settings | None = None) -> None:
    """
    应用启动时初始化数据库。
    包含：建库/Schema（可选）→ Alembic版本化迁移（可选）→ 建表兜底 → 增量补丁。
    """
    settings = settings or get_settings()

    if not settings.database.auto_init:
        logger.info("DB_AUTO_INIT=false，跳过数据库自动初始化")
        return

    logger.info(
        "开始数据库自动初始化: type=%s, name=%s",
        settings.DB_TYPE,
        settings.DB_NAME,
    )

    if settings.DB_TYPE.lower() == "dm":
        ensure_dm_driver()

    ensure_database_or_schema(settings)
    run_schema_migrations(settings)

    connect_args = (
        _dm_connect_args(settings) if settings.DB_TYPE.lower() == "dm" else {}
    )
    engine = create_engine(
        settings.DB_URL, pool_pre_ping=True, connect_args=connect_args
    )
    try:
        ensure_tables(engine)
        apply_schema_patches(engine, settings)
    finally:
        engine.dispose()

    logger.info("数据库自动初始化完成")
