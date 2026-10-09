"""图像提示词单份种子、版本隔离和锁定读取的数据库行为。"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from media_platform.application.image_prompt_service import (
    ImagePromptManagementService,
    ImagePromptPolicy,
    ImagePromptReader,
    ensure_image_prompt_bootstrap,
)
from media_platform.contracts.image_analysis import IMAGE_PROMPT_SCHEME
from media_platform.infrastructure.database.models.content_prompt_bundle import (
    ContentPromptBundleModel,
)
from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    PROMPT_SCHEME_CODE,
    PromptBundleRepository,
)
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
from services.image_analysis.main import create_image_analysis_app


@pytest.fixture
def prompt_db(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'image-prompts.db').as_posix()}")
    ContentPromptBundleModel.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture
def policy():
    return ImagePromptPolicy(
        allowed_models=frozenset({"test-vision"}),
        output_schema_versions=frozenset({"image-metrics.v1"}),
        metric_definition_versions=frozenset({"classroom-behavior.v1"}),
    )


def seed():
    return load_bootstrap_prompt_bundle("test-vision")


def test_one_seed_bootstraps_once_and_does_not_override_database(prompt_db, policy):
    with prompt_db.begin() as session:
        session.add(
            ContentPromptBundleModel(
                id="evaluation-existing",
                scheme_code=PROMPT_SCHEME_CODE,
                version=1,
                status="DRAFT",
                content={"steps": []},
                content_hash="other-scheme",
                school_code="GLOBAL",
                created_by="test",
                updated_by="test",
            )
        )
    first = ensure_image_prompt_bootstrap(prompt_db, seed, policy)
    assert first.status == "PUBLISHED"
    assert first.content["prompt"] == seed().prompt
    assert "studentPrompt" not in first.content
    assert "teacherPrompt" not in first.content
    second = ensure_image_prompt_bootstrap(
        prompt_db,
        lambda: pytest.fail("existing scope must not read local file"),
        policy,
    )
    assert second.id == first.id
    with prompt_db() as session:
        assert (
            len(
                PromptBundleRepository(
                    session, scheme_code=IMAGE_PROMPT_SCHEME, school_code="GLOBAL"
                ).list_versions()
            )
            == 1
        )


def test_existing_draft_blocks_seed_and_published_reader_does_not_fallback_to_file(
    prompt_db, policy
):
    management = ImagePromptManagementService(prompt_db, policy)
    draft = management.create_draft(seed(), school_code="GLOBAL", operator="editor")
    assert (
        ensure_image_prompt_bootstrap(
            prompt_db, lambda: pytest.fail("existing draft must block seed"), policy
        ).id
        == draft.id
    )
    with pytest.raises(LookupError):
        ImagePromptReader(prompt_db, policy).published("GLOBAL")
    assert len(management.list_versions(school_code="GLOBAL")) == 1


def test_school_draft_does_not_fall_back_to_global_published(prompt_db, policy):
    ensure_image_prompt_bootstrap(prompt_db, seed, policy)
    ImagePromptManagementService(prompt_db, policy).create_draft(
        seed(), school_code="school-a", operator="editor"
    )
    with pytest.raises(LookupError, match="没有发布版本"):
        ImagePromptReader(prompt_db, policy).published("school-a")


def test_school_scheme_edit_publish_rollback_and_locked_history(prompt_db, policy):
    management = ImagePromptManagementService(prompt_db, policy)
    reader = ImagePromptReader(prompt_db, policy)
    global_version = ensure_image_prompt_bootstrap(prompt_db, seed, policy)
    assert reader.published("school-a").id == global_version.id

    draft = management.create_draft(seed(), school_code="school-a", operator="editor")
    with pytest.raises(LookupError):
        management.clone_draft(
            global_version.id, school_code="school-a", operator="editor"
        )
    with pytest.raises(LookupError):
        management.publish(
            draft.id,
            school_code="school-b",
            operator="editor",
            expected_hash=draft.content_hash,
        )
    with prompt_db() as session:
        assert PromptBundleRepository(session).get(draft.id) is None

    changed = seed().model_copy(update={"prompt": seed().prompt + "\n版本二"})
    with pytest.raises(ValueError, match="预期内容摘要"):
        management.update_draft(
            draft.id,
            changed,
            school_code="school-a",
            operator="editor",
            expected_hash="",
        )
    with pytest.raises(ValueError, match="刷新版本"):
        management.update_draft(
            draft.id,
            changed,
            school_code="school-a",
            operator="editor",
            expected_hash="wrong",
        )
    updated = management.update_draft(
        draft.id,
        changed,
        school_code="school-a",
        operator="editor",
        expected_hash=draft.content_hash,
    )
    published = management.publish(
        updated.id,
        school_code="school-a",
        operator="publisher",
        expected_hash=updated.content_hash,
    )
    assert reader.published("school-a").id == published.id
    old = reader.locked(
        global_version.id,
        school_code="school-a",
        expected_version=global_version.version,
        expected_hash=global_version.content_hash,
    )
    assert old.content.prompt == seed().prompt
    with pytest.raises(ValueError, match="只有草稿"):
        management.update_draft(
            published.id,
            changed,
            school_code="school-a",
            operator="editor",
            expected_hash=published.content_hash,
        )
    with pytest.raises(ValueError, match="不匹配"):
        reader.locked(
            published.id,
            school_code="school-a",
            expected_version=999,
            expected_hash=published.content_hash,
        )


def test_unsupported_model_and_database_error_fail_closed(prompt_db, policy):
    manager = ImagePromptManagementService(prompt_db, policy)
    draft = manager.create_draft(
        seed().model_copy(update={"model": "unknown-model"}),
        school_code="GLOBAL",
        operator="editor",
    )
    with pytest.raises(ValueError, match="受控名单"):
        manager.publish(
            draft.id,
            school_code="GLOBAL",
            operator="editor",
            expected_hash=draft.content_hash,
        )
    with pytest.raises(LookupError):
        ImagePromptReader(prompt_db, policy).published("GLOBAL")

    def broken_factory():
        raise sa.exc.OperationalError("select", {}, Exception("connection lost"))

    with pytest.raises(sa.exc.OperationalError):
        ImagePromptReader(broken_factory, policy).published("GLOBAL")
    with pytest.raises(sa.exc.OperationalError):
        ensure_image_prompt_bootstrap(broken_factory, seed, policy)


def test_image_service_bootstraps_only_when_prompt_is_configured(prompt_db, policy):
    app = create_image_analysis_app(
        prompt_session_factory=prompt_db,
        prompt_policy=policy,
        prompt_default_model="test-vision",
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
    first = ImagePromptReader(prompt_db, policy).published("GLOBAL")
    with TestClient(app):
        pass
    assert ImagePromptReader(prompt_db, policy).published("GLOBAL").id == first.id


def test_enabled_image_service_bootstraps_prompt_before_runtime(
    monkeypatch, prompt_db, policy
):
    monkeypatch.setenv("IMAGE_ANALYSIS_CONSUMER_ENABLED", "true")

    class Connection:
        is_closed = False

    class Consumer:
        _connection = Connection()

    class Runtime:
        consumer = Consumer()

        async def start(self):
            ImagePromptReader(prompt_db, policy).published("GLOBAL")

        async def close(self):
            pass

    app = create_image_analysis_app(
        prompt_session_factory=prompt_db,
        prompt_policy=policy,
        prompt_default_model="test-vision",
        runtime_factory=Runtime,
    )

    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200

    assert ImagePromptReader(prompt_db, policy).published("GLOBAL").content.model == (
        "test-vision"
    )


def test_image_service_rejects_invalid_model_configuration(prompt_db, policy):
    app = create_image_analysis_app(
        prompt_session_factory=prompt_db,
        prompt_policy=policy,
        prompt_default_model="not-allowed",
    )
    with pytest.raises(ValueError, match="受控名单"):
        with TestClient(app):
            pytest.fail("unapproved model started")
