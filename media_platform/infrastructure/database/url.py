"""数据库连接 URL 构建工具。"""

from urllib.parse import quote_plus


def normalize_dm_schema(schema: str) -> str:
    """达梦 Schema 名称统一为大写（与 CREATE SCHEMA 保持一致）。"""
    return schema.upper()


def build_dm_connect_args(
    schema: str,
    dm_mysql_compat: bool = True,
    connection_timeout: int = 10,
) -> dict:
    """达梦 dmPython 连接参数（schema 不能放在 URL 路径中）。"""
    args: dict = {
        "schema": normalize_dm_schema(schema),
        "connection_timeout": connection_timeout,
    }
    if dm_mysql_compat:
        args["compatible_mode"] = "MYSQL"
    return args


def build_db_url(
    db_type: str,
    user: str,
    password: str,
    host: str,
    port: int,
    db_name: str,
    charset: str = "utf8mb4",
) -> str:
    """根据数据库类型生成 SQLAlchemy 连接 URL。"""
    encoded_password = quote_plus(password) if password else ""
    db_type = (db_type or "mysql").lower()

    if db_type == "dm":
        # 达梦 schema 通过 connect_args 传递，不能写在 URL 路径里
        return f"dm+dmPython://{user}:{encoded_password}@{host}:{port}/"

    query = f"?charset={charset}" if charset else ""
    return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{db_name}{query}"


def build_admin_db_url(
    db_type: str,
    user: str,
    password: str,
    host: str,
    port: int,
    charset: str = "utf8mb4",
) -> str:
    """生成不含库名/Schema 的管理连接 URL，用于建库/建 Schema。"""
    encoded_password = quote_plus(password) if password else ""
    db_type = (db_type or "mysql").lower()

    if db_type == "dm":
        return f"dm+dmPython://{user}:{encoded_password}@{host}:{port}/"

    query = f"?charset={charset}" if charset else ""
    return f"mysql+pymysql://{user}:{encoded_password}@{host}:{port}/{query}"


def build_async_db_url(sync_url: str, db_type: str) -> str:
    """将同步 URL 转为异步 URL（仅 MySQL 支持原生异步驱动）。"""
    db_type = (db_type or "mysql").lower()
    if db_type == "dm":
        return sync_url
    return sync_url.replace("mysql+pymysql", "mysql+aiomysql")


def default_db_port(db_type: str) -> int:
    """返回数据库类型对应的默认端口。"""
    return 5236 if (db_type or "mysql").lower() == "dm" else 3306
