"""配置版本迁移在SQLite执行往返，并检查MySQL版本化DDL。"""

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
import sqlalchemy as sa

from media_platform.infrastructure.database.models import (
    ServiceConfigModel,
    ServiceConfigVersionModel,
)


def migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/20261016_0004_add_service_runtime_config.py"
    )
    spec = spec_from_file_location("runtime_config_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_downgrade_and_orm_columns_match(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    module = migration()
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = sa.inspect(connection)
        for model in (ServiceConfigModel, ServiceConfigVersionModel):
            assert {
                c["name"] for c in inspector.get_columns(model.__tablename__)
            } == set(model.__table__.columns.keys())
        module.downgrade()
        assert not sa.inspect(connection).get_table_names()
    engine.dispose()


def test_mysql_ddl_has_comments_constraints_and_audit_fields():
    stream = StringIO()
    module = migration()
    module.op = Operations(
        MigrationContext.configure(
            url="mysql+pymysql://", opts={"as_sql": True, "output_buffer": stream}
        )
    )
    module.upgrade()
    sql = stream.getvalue()
    assert "uk_mt_fwpzbb_qq" in sql and "FOREIGN KEY" in sql
    for model in (ServiceConfigModel, ServiceConfigVersionModel):
        assert model.__tablename__ in sql
        assert all(column.comment for column in model.__table__.columns)
        assert {"cjr", "xgr", "xxm", "cjsj", "xgsj"} <= set(
            model.__table__.columns.keys()
        )
