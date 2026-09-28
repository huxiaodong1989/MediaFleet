import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.content_evaluation import (
    PromptBundleContent,
    PromptStepDefinition,
)
from services.content_analysis.application import PromptManagementService
from services.content_analysis.infrastructure.models import ContentPromptBundleModel
from services.content_analysis.infrastructure.repositories import PromptBundleRepository


def _bundle(label: str = "初始") -> PromptBundleContent:
    codes = (
        "CLASSROOM_SUMMARY",
        "FREQUENCY_ANALYSIS",
        "KEYEVENT_ANALYSIS",
        "CONTENT_SUMMARY",
        "QA_ANALYSIS",
        "TEACHING_METHOD",
        "KNOWLEDGE_GRAPH",
        "CLASSROOM_SCORE",
    )
    return PromptBundleContent(
        steps=[
            PromptStepDefinition(
                code=code,
                name=code,
                order=index,
                system_prompt=f"{label}-{code}-系统提示词",
                user_prompt="输出评分JSON",
                model="test-model",
                critical=code == "CLASSROOM_SCORE",
            )
            for index, code in enumerate(codes, start=1)
        ]
    )


def test_prompt_draft_publish_edit_protection_and_rollback(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'prompts.db').as_posix()}")
    ContentPromptBundleModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    service = PromptManagementService(factory)
    try:
        first = service.create_draft(
            _bundle("v1"),
            school_code="GLOBAL",
            operator="tester",
        )
        published = service.publish(first.id, operator="publisher")
        assert published.status == "PUBLISHED"

        second = service.clone_draft(
            published.id,
            school_code="GLOBAL",
            operator="editor",
        )
        second = service.update_draft(second.id, _bundle("v2"), operator="editor")
        service.publish(second.id, operator="publisher")

        versions = service.list_versions("GLOBAL")
        assert [item.version for item in versions] == [2, 1]
        assert [item.status for item in versions] == ["PUBLISHED", "ARCHIVED"]

        rollback = service.publish(first.id, operator="rollback-user")
        assert rollback.status == "PUBLISHED"
        assert service.get_published("GLOBAL").id == first.id

        try:
            service.update_draft(first.id, _bundle("illegal"), operator="editor")
        except ValueError as exc:
            assert "草稿" in str(exc)
        else:
            raise AssertionError("已发布版本不应允许修改")
    finally:
        engine.dispose()


def test_bootstrap_prompt_is_inserted_as_first_published_version_once(tmp_path, caplog):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'bootstrap-prompts.db').as_posix()}")
    ContentPromptBundleModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with caplog.at_level("INFO"):
            with factory.begin() as session:
                first = PromptBundleRepository(session).ensure_bootstrap(_bundle("本地种子"))
            with factory.begin() as session:
                reused = PromptBundleRepository(session).ensure_bootstrap(_bundle("不应覆盖"))

        assert first.id == reused.id
        assert first.version == 1
        assert first.status == "PUBLISHED"
        with factory() as session:
            assert len(PromptBundleRepository(session).list_versions()) == 1
        assert "已从本地bootstrap_prompts写入首个发布版本" in caplog.text
        assert "复用数据库已发布版本" in caplog.text
    finally:
        engine.dispose()
