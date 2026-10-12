"""图像任务终态通知表的版本化迁移。"""

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import mysql

from media_platform.infrastructure.database.models.image_analysis import (
    ImageTaskNotificationModel,
)


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/20261012_0003_add_image_task_notification.py"
)


def _migration():
    spec = spec_from_file_location("image_task_notification_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_terminal_notification_migration_up_down(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'terminal.db').as_posix()}")
    metadata = sa.MetaData()
    sa.Table("media_task", metadata, sa.Column("zj", sa.String(36), primary_key=True))
    metadata.create_all(engine)
    migration = _migration()
    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        inspector = sa.inspect(engine)
        table_name = "image_analysis_task_notification"
        assert table_name in inspector.get_table_names()
        assert {column["name"] for column in inspector.get_columns(table_name)} == {
            column.name for column in ImageTaskNotificationModel.__table__.columns
        }
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.downgrade()
        assert table_name not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_terminal_notification_table_comments_and_mysql_sql():
    table = ImageTaskNotificationModel.__table__
    assert table.comment
    assert {"cjr", "xgr", "xxm", "cjsj", "xgsj"} <= set(table.columns.keys())
    assert all(column.comment for column in table.columns)

    output = StringIO()
    migration = _migration()
    migration.op = Operations(
        MigrationContext.configure(
            url="mysql+pymysql://",
            opts={"as_sql": True, "output_buffer": output},
        )
    )
    migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE image_analysis_task_notification" in sql
    assert "idx_img_task_notice_delivery" in sql


class _Result:
    def mappings(self):
        return self

    @staticmethod
    def one_or_none():
        return {
            "DATA_TYPE": "varchar",
            "CHARACTER_MAXIMUM_LENGTH": 36,
            "CHARACTER_SET_NAME": "utf8mb4",
            "COLLATION_NAME": "utf8mb4_general_ci",
        }


class _Bind:
    dialect = SimpleNamespace(name="mysql")

    @staticmethod
    def execute(_statement):
        return _Result()


class _MigrationOperations:
    @staticmethod
    def get_bind():
        return _Bind()

    @staticmethod
    def get_context():
        return SimpleNamespace(as_sql=False)


def test_terminal_foreign_key_follows_existing_mysql_parent_definition():
    migration = _migration()
    migration.op = _MigrationOperations()

    column_type = migration._media_task_id_type()

    assert isinstance(column_type, mysql.VARCHAR)
    assert column_type.length == 36
    assert column_type.charset == "utf8mb4"
    assert column_type.collation == "utf8mb4_general_ci"
