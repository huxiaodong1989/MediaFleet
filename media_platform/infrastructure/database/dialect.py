"""多方言数据库类型与 DDL 辅助。"""

import json

from sqlalchemy import JSON, Text
from sqlalchemy.types import TypeDecorator, TypeEngine


def is_dm(db_type: str) -> bool:
    return (db_type or "mysql").lower() == "dm"


def use_dm_mysql_compat(db_type: str, dm_mysql_compat: bool) -> bool:
    """达梦是否走 MySQL 兼容模式（路径 A）。"""
    return is_dm(db_type) and dm_mysql_compat


class DamengJSON(TypeDecorator):
    """
    达梦 JSON 列类型。
    dmSQLAlchemy MySQL 兼容模式下对 NULL 会错误调用 json.loads(None)，
    因此达梦统一用字符串存储并在应用层序列化/反序列化。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, (bytes, bytearray)):
            value = value.decode()
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            return json.loads(text)
        return value


def json_column(
    db_type: str | None = None,
    dm_mysql_compat: bool = True,
) -> TypeEngine:
    """返回当前数据库应使用的 JSON ORM 列类型。"""
    if db_type is None:
        from media_platform.common.config import get_settings

        settings = get_settings()
        db_type = settings.DB_TYPE
        dm_mysql_compat = settings.database.dm_mysql_compat

    if is_dm(db_type):
        return DamengJSON()
    return JSON()


def get_json_column_type(db_type: str, dm_mysql_compat: bool = True) -> TypeEngine:
    return json_column(db_type, dm_mysql_compat)


def get_json_ddl_type(db_type: str, dm_mysql_compat: bool = True) -> str:
    """返回增量 DDL 中 JSON 列的类型名。"""
    if is_dm(db_type) and not dm_mysql_compat:
        return "CLOB"
    if is_dm(db_type):
        return "CLOB"
    return "JSON"
