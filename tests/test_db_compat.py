"""MySQL / 达梦8 双库兼容与自动初始化测试。"""

import os
from unittest.mock import patch

import pytest
from sqlalchemy import inspect

from media_platform.infrastructure.database.dialect import (
    get_json_column_type,
    get_json_ddl_type,
    is_dm,
)
from media_platform.infrastructure.database.init import (
    apply_schema_patches,
    ensure_tables,
    init_database,
)
from media_platform.infrastructure.database.sync_async_session import (
    SyncSessionAsyncAdapter,
)
from media_platform.infrastructure.database.url import (
    build_admin_db_url,
    build_async_db_url,
    build_db_url,
    default_db_port,
)


class TestDbUrl:
    def test_mysql_url(self):
        url = build_db_url("mysql", "root", "p@ss", "localhost", 3306, "mediafleet")
        assert url.startswith("mysql+pymysql://root:p%40ss@localhost:3306/mediafleet")
        assert "charset=utf8mb4" in url

    def test_dm_url(self):
        url = build_db_url("dm", "SYSDBA", "pwd", "127.0.0.1", 5236, "mediafleet")
        assert url == "dm+dmPython://SYSDBA:pwd@127.0.0.1:5236/"

    def test_dm_connect_args_schema_upper(self):
        from media_platform.infrastructure.database.url import build_dm_connect_args

        args = build_dm_connect_args("mediafleet")
        assert args["schema"] == "MEDIAFLEET"
        assert args["compatible_mode"] == "MYSQL"

    def test_admin_urls(self):
        mysql_admin = build_admin_db_url("mysql", "root", "", "localhost", 3306)
        assert mysql_admin.endswith("/?charset=utf8mb4")
        dm_admin = build_admin_db_url("dm", "SYSDBA", "", "127.0.0.1", 5236)
        assert dm_admin == "dm+dmPython://SYSDBA:@127.0.0.1:5236/"

    def test_async_url_mysql_only(self):
        sync = build_db_url("mysql", "u", "", "h", 3306, "db")
        assert "aiomysql" in build_async_db_url(sync, "mysql")
        dm_sync = build_db_url("dm", "u", "", "h", 5236, "db")
        assert build_async_db_url(dm_sync, "dm") == dm_sync

    def test_default_ports(self):
        assert default_db_port("mysql") == 3306
        assert default_db_port("dm") == 5236


class TestDialect:
    def test_json_type_mysql(self):
        assert get_json_ddl_type("mysql") == "JSON"

    def test_json_type_dm_compat(self):
        assert get_json_ddl_type("dm", True) == "CLOB"
        col = get_json_column_type("dm", True)
        assert col.__class__.__name__ == "DamengJSON"

    def test_json_type_dm_native(self):
        assert get_json_ddl_type("dm", False) == "CLOB"
        assert get_json_column_type("dm", False).__class__.__name__ == "DamengJSON"

    def test_is_dm(self):
        assert is_dm("dm") is True
        assert is_dm("mysql") is False


class TestSyncSessionAsyncAdapter:
    def test_adapter_delegates_add(self):
        class FakeSession:
            def __init__(self):
                self.added = []

            def add(self, obj):
                self.added.append(obj)

        fake = FakeSession()
        adapter = SyncSessionAsyncAdapter(fake)
        adapter.add("obj")
        assert fake.added == ["obj"]


class TestInitHelpers:
    def test_auto_init_disabled(self):
        with patch.dict(os.environ, {"DB_AUTO_INIT": "false"}, clear=False):
            from media_platform.common.settings import get_settings

            get_settings.cache_clear()
            try:
                settings = get_settings()
                with patch(
                    "media_platform.infrastructure.database.init.ensure_database_or_schema"
                ) as mock_ensure:
                    with patch(
                        "media_platform.infrastructure.database.init.ensure_tables"
                    ) as mock_tables:
                        init_database(settings)
                        mock_ensure.assert_not_called()
                        mock_tables.assert_not_called()
            finally:
                get_settings.cache_clear()

    def test_auto_init_runs_schema_migrations_before_create_all(self):
        class FakeEngine:
            def dispose(self):
                return None

        with patch.dict(
            os.environ,
            {
                "DB_AUTO_INIT": "true",
                "DB_AUTO_MIGRATE": "true",
                "DB_TYPE": "mysql",
                "DB_URL": "sqlite:///:memory:",
            },
            clear=False,
        ):
            from media_platform.common.settings import get_settings

            get_settings.cache_clear()
            try:
                settings = get_settings()
                with patch(
                    "media_platform.infrastructure.database.init.ensure_database_or_schema"
                ) as mock_ensure:
                    with patch(
                        "media_platform.infrastructure.database.init.run_schema_migrations"
                    ) as mock_migrate:
                        with patch(
                            "media_platform.infrastructure.database.init.create_engine",
                            return_value=FakeEngine(),
                        ):
                            with patch(
                                "media_platform.infrastructure.database.init.ensure_tables"
                            ) as mock_tables:
                                with patch(
                                    "media_platform.infrastructure.database.init.apply_schema_patches"
                                ) as mock_patches:
                                    init_database(settings)
                mock_ensure.assert_called_once_with(settings)
                mock_migrate.assert_called_once_with(settings)
                mock_tables.assert_called_once()
                mock_patches.assert_called_once()
            finally:
                get_settings.cache_clear()

    def test_schema_migration_can_be_disabled(self):
        from media_platform.infrastructure.database.init import run_schema_migrations

        with patch.dict(
            os.environ,
            {"DB_AUTO_MIGRATE": "false"},
            clear=False,
        ):
            from media_platform.common.settings import get_settings

            get_settings.cache_clear()
            try:
                settings = get_settings()
                with patch(
                    "media_platform.infrastructure.database.init.command.upgrade"
                ) as mock_upgrade:
                    run_schema_migrations(settings)
                mock_upgrade.assert_not_called()
            finally:
                get_settings.cache_clear()
