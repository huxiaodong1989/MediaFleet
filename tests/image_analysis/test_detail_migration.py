"""图像三类明细的版本化迁移与同任务/学校图片关联约束。"""

from importlib.util import module_from_spec, spec_from_file_location
from datetime import datetime
from io import StringIO
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from media_platform.infrastructure.database.models.image_analysis import (
    ImageFrameModel,
    ImageSampleModel,
    ImageVideoModel,
)


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/20261009_0002_add_image_analysis.py"
)


def _migration():
    spec = spec_from_file_location("image_detail_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_image_detail_migration_up_down_and_cross_scope_foreign_keys(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'image-details.db').as_posix()}")
    sa.event.listen(
        engine, "connect", lambda dbapi, _: dbapi.execute("PRAGMA foreign_keys=ON")
    )
    parents = sa.MetaData()
    task = sa.Table(
        "media_task", parents, sa.Column("zj", sa.String(36), primary_key=True)
    )
    parents.create_all(engine)
    migration = _migration()

    try:
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
        inspector = sa.inspect(engine)
        assert {
            "image_analysis_video",
            "image_analysis_sample",
            "image_analysis_frame",
        } <= set(
            inspector.get_table_names()
        )

        video = ImageVideoModel.__table__
        sample = ImageSampleModel.__table__
        frame = ImageFrameModel.__table__
        for table in (video, sample, frame):
            assert {column["name"] for column in inspector.get_columns(table.name)} == {
                column.name for column in table.columns
            }
        with engine.begin() as connection:
            connection.execute(task.insert().values(zj="task-1"))
            connection.execute(task.insert().values(zj="task-2"))
            connection.execute(
                video.insert().values(
                    zj="video-1",
                    rwzj="task-1",
                    xxm="school-a",
                    cjr="test",
                    xgr="test",
                    ljzj="source-1",
                    lbs="camera-1",
                    sfzy="b" * 64,
                    lylx="ROOM_CAMERA",
                    fxpz=["S_ANALYSIS"],
                )
            )
            connection.execute(
                sample.insert().values(
                    zj="sample-1",
                    rwzj="task-1",
                    xxm="school-a",
                    cjr="test",
                    xgr="test",
                    ktpyhs=120000,
                    cysj=datetime(2026, 9, 29, 8, 32),
                    yqsp=["video-1"],
                    qqzb=["HEAD_UP"],
                    bzzb=[],
                    tsbbzj="prompt-1",
                    tsbbh=1,
                    tszy="a" * 64,
                    mxzp={"model": "test"},
                    sjbh="event-1",
                )
            )
            connection.execute(
                frame.insert().values(
                    zj="frame-1",
                    rwzj="task-1",
                    xxm="school-a",
                    cjr="test",
                    xgr="test",
                    cydzj="sample-1",
                    spzj="video-1",
                    mbpyhs=120000,
                )
            )

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    frame.insert().values(
                        zj="frame-wrong-school",
                        rwzj="task-1",
                        xxm="school-b",
                        cjr="test",
                        xgr="test",
                        cydzj="sample-1",
                        spzj="video-1",
                        mbpyhs=120000,
                    )
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    video.insert().values(
                        zj="video-duplicate",
                        rwzj="task-1",
                        xxm="school-a",
                        cjr="test",
                        xgr="test",
                        ljzj="source-1",
                        lbs="camera-1",
                        sfzy="b" * 64,
                        lylx="ROOM_CAMERA",
                        fxpz=["S_ANALYSIS"],
                    )
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    frame.insert().values(
                        zj="frame-wrong-task",
                        rwzj="task-2",
                        xxm="school-a",
                        cjr="test",
                        xgr="test",
                        cydzj="sample-1",
                        spzj="video-1",
                        mbpyhs=120000,
                    )
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    frame.insert().values(
                        zj="frame-duplicate",
                        rwzj="task-1",
                        xxm="school-a",
                        cjr="test",
                        xgr="test",
                        cydzj="sample-1",
                        spzj="video-1",
                        mbpyhs=120000,
                    )
                )

        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.downgrade()
        assert not (
            {
                "image_analysis_video",
                "image_analysis_sample",
                "image_analysis_frame",
            }
            & set(sa.inspect(engine).get_table_names())
        )
    finally:
        engine.dispose()


def test_all_image_detail_columns_have_chinese_comments():
    for table in (
        ImageVideoModel.__table__,
        ImageSampleModel.__table__,
        ImageFrameModel.__table__,
    ):
        assert table.comment
        assert {"cjr", "xgr", "xxm", "cjsj", "xgsj"} <= set(table.columns.keys())
        assert all(column.comment for column in table.columns)


def test_mysql_migration_sql_compiles_without_connecting():
    migration = _migration()
    output = StringIO()
    migration.op = Operations(
        MigrationContext.configure(
            url="mysql+pymysql://",
            opts={"as_sql": True, "output_buffer": output},
        )
    )
    migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE image_analysis_video" in sql
    assert "CREATE TABLE image_analysis_sample" in sql
    assert "CREATE TABLE image_analysis_frame" in sql
    assert "ALTER TABLE prompt_version" in sql
    assert "fk_img_frame_sample" in sql
