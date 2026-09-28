"""Current MediaFleet schema and clean Alembic baseline tests."""

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.init import ensure_tables
from media_platform.infrastructure.database.models import (  # noqa: F401
    MediaFileModel,
    MediaNodeModel,
    MediaStreamBindingModel,
    MediaTaskModel,
    RecordingServerModel,
)
from media_platform.infrastructure.database.types import PortableJSON
from services.content_analysis.infrastructure.models import (  # noqa: F401
    ContentEvaluationRecordModel,
    ContentEvaluationStepModel,
)


ROOT = Path(__file__).resolve().parents[2]
CORE_TABLES = {
    "media_node",
    "media_stream_binding",
    "media_task",
    "media_artifact",
    "recording_server",
}
CONTENT_ANALYSIS_TABLES = {
    "content_evaluation",
    "content_evaluation_step",
    "prompt_version",
}
APPLICATION_TABLES = CORE_TABLES | CONTENT_ANALYSIS_TABLES
AUDIT_COLUMNS = {"cjr", "xgr", "xxm", "cjsj", "xgsj"}
NODE_AUDIT_COLUMNS = {"cjr", "xgr", "cjsj", "xgsj"}


def test_current_models_share_metadata():
    assert APPLICATION_TABLES <= set(Base.metadata.tables)


def test_portable_json_serializes_dameng_text_values():
    column_type = PortableJSON()
    dialect = SimpleNamespace(name="dm")

    encoded = column_type.process_bind_param({"能力": ["video"]}, dialect)
    assert encoded == '{"能力": ["video"]}'
    assert column_type.process_result_value(encoded, dialect) == {
        "能力": ["video"]
    }


def test_core_tables_and_columns_have_chinese_comments():
    for table_name in CORE_TABLES:
        table = Base.metadata.tables[table_name]
        assert table.comment
        expected_audit_columns = (
            NODE_AUDIT_COLUMNS
            if table_name in {"media_node", "recording_server"}
            else AUDIT_COLUMNS
        )
        assert expected_audit_columns <= set(table.columns.keys())
        assert all(column.comment for column in table.columns)


def test_core_physical_column_names_match_mapping():
    assert set(Base.metadata.tables["media_node"].columns.keys()) == {
        "zj", "jdbh", "jdmc", "jdlx", "jdzt", "dlfwdz", "zljkdz",
        "zlfwbs", "lxgml", "qz", "gnlb", "nlpz", "jxzt", "jxmx",
        "zhxjsj", *NODE_AUDIT_COLUMNS,
    }
    assert set(Base.metadata.tables["recording_server"].columns.keys()) == {
        "zj", "fwqbh", "fwqmc", "fwqzt", "lzjdzj", "zlfwbs",
        "zljkdz", "bfzjdz", "bfdk", "bfxy", "rtmpdk", "rtspdk",
        "lxgml", "zdlzls", "zdbds", "yzbds", *NODE_AUDIT_COLUMNS,
    }
    assert set(Base.metadata.tables["media_stream_binding"].columns.keys()) == {
        "zj", "zylx", "zybh", "kjbh", "jdzj", "yym", "lbs", "lmc",
        "llx", "bdzt", "bbyh", "yldz", "zhhysj", *AUDIT_COLUMNS,
    }
    assert set(Base.metadata.tables["media_task"].columns.keys()) == {
        "zj", "qqbh", "mdj", "ywrwbh", "rwlx", "tdqd", "lyj", "rwzt",
        "yxj", "jd", "qqcs", "zxjg", "cwxx", "hddz", "hdjg", "zxjdzj",
        "lzfwqzj", "yym", "lbs", "yykssj", "yyjssj", "cs", "zdcs", "fbzt",
        "xxbh", "sdslbs", "sdsj", "fbsj", "kssj", "wcsj", "zxdc",
        "zysyd", "zysxsj", *AUDIT_COLUMNS,
    }
    assert set(Base.metadata.tables["media_artifact"].columns.keys()) == {
        "zj", "rwzj", "wjlx", "wjmc", "wjdz", "xdlj", "cttmc", "wjdx",
        "mllx", "kzxx", *AUDIT_COLUMNS,
    }


def test_mysql_ddl_contains_table_and_column_comments():
    for table_name in CORE_TABLES:
        ddl = str(
            CreateTable(Base.metadata.tables[table_name]).compile(
                dialect=mysql.dialect()
            )
        )
        assert "COMMENT" in ddl
        assert Base.metadata.tables[table_name].comment in ddl


def test_development_auto_init_creates_current_tables():
    engine = create_engine("sqlite:///:memory:")
    try:
        ensure_tables(engine)
        assert APPLICATION_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_alembic_has_one_clean_baseline():
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["20260928_0001"]
    revision = scripts.get_revision("20260928_0001")
    assert revision is not None
    assert revision.down_revision is None


def test_alembic_uses_project_version_table():
    env_source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    assert 'VERSION_TABLE = "schema_version"' in env_source


def test_baseline_emits_mysql_compatible_ddl(monkeypatch):
    monkeypatch.setenv("DB_TYPE", "mysql")
    monkeypatch.setenv(
        "DB_URL",
        "mysql+pymysql://migration:test@127.0.0.1:3306/mediafleet",
    )

    from media_platform.common.settings import get_settings

    get_settings.cache_clear()
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.attributes["skip_logging_config"] = True
    try:
        command.upgrade(config, "head", sql=True)
        ddl = output.getvalue()
    finally:
        get_settings.cache_clear()

    assert ddl.count("ENGINE=InnoDB") == len(APPLICATION_TABLES)
    assert ddl.count("CHARSET=utf8mb4") == len(APPLICATION_TABLES)
    assert ddl.count("COLLATE utf8mb4_unicode_ci") == len(APPLICATION_TABLES)
    assert "DEFAULT now()" not in ddl
    assert "DEFAULT (now())" not in ddl


def test_alembic_upgrade_and_downgrade_on_empty_database(tmp_path, monkeypatch):
    database_file = (tmp_path / "mediafleet-schema.db").resolve().as_posix()
    monkeypatch.setenv("DB_TYPE", "mysql")
    monkeypatch.setenv("DB_URL", f"sqlite:///{database_file}")

    from media_platform.common.settings import get_settings

    get_settings.cache_clear()
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["skip_logging_config"] = True
    try:
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{database_file}")
        try:
            table_names = set(inspect(engine).get_table_names())
            assert APPLICATION_TABLES <= table_names
            assert "schema_version" in table_names
        finally:
            engine.dispose()

        command.downgrade(config, "base")
        engine = create_engine(f"sqlite:///{database_file}")
        try:
            table_names = set(inspect(engine).get_table_names())
            assert APPLICATION_TABLES.isdisjoint(table_names)
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
