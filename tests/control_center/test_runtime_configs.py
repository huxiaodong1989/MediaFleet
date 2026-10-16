"""配置发布、任务锁版、热调整与管理边界的行为测试。"""

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from media_platform.application.analysis_config import (
    AnalysisConfigReader,
    DatabaseImagePromptPolicy,
    initialize_analysis_config,
)
from media_platform.application.image_prompt_service import (
    ImagePromptReader,
    ensure_image_prompt_bootstrap,
)
from media_platform.application.runtime_config import (
    AdjustableLimits,
    RuntimeConfigRefresher,
)
from media_platform.contracts.runtime_config import ImagePerformanceConfig
from media_platform.infrastructure.database.base import Base
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaTaskModel,
    ServiceConfigVersionModel,
)
from media_platform.infrastructure.database.repositories.content_prompt_bundle import (
    PromptBundleRepository,
)
from media_platform.infrastructure.messaging import (
    PikaTaskConsumer,
    RabbitMQConsumerConfig,
)
from media_platform.infrastructure.runtime_config import (
    ConfigConflict,
    RuntimeConfigStore,
)
from services.content_analysis.application.prompt_defaults import (
    load_bootstrap_prompt_bundle,
)
from services.control_center.application.runtime_config_service import (
    RuntimeConfigManagementService,
)
from services.control_center.main import create_control_center_app
from services.image_analysis.concurrency import (
    ImageConcurrencySettings,
    InstanceConcurrency,
    ConcurrencyStage,
)
from services.image_analysis.prompt_defaults import (
    load_bootstrap_prompt_bundle as image_seed,
)


@pytest.fixture
def config_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'config.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    store = RuntimeConfigStore(factory, Fernet.generate_key().decode())
    with factory.begin() as session:
        PromptBundleRepository(session).ensure_bootstrap(
            load_bootstrap_prompt_bundle("old-model")
        )
    yield store, RuntimeConfigManagementService(store)
    engine.dispose()


def connection(model="old-model", url="https://models.example/v1"):
    return {"base_url": url, "allowed_models": [model], "timeout_seconds": 90}


def draft(
    store,
    *,
    service="content-analysis",
    category="model_connection",
    revision=0,
    content=None,
    **kwargs,
):
    return store.draft(
        service,
        category,
        content=content or connection(),
        expected_revision=revision,
        request_id=kwargs.pop("request_id", str(uuid4())),
        reason=kwargs.pop("reason", "测试调整"),
        **kwargs,
    )


def publish_initial(store, management):
    value = draft(
        store,
        api_key="private-token",
        prompt_updates=management.prompts("content-analysis"),
    )
    return management.publish("content-analysis", "model_connection", value["id"])


def add_task(store, params, *, task_id="task-1", generation=1):
    with store.session_factory.begin() as session:
        session.add(
            MediaTaskModel(
                id=task_id,
                school_code="school-1",
                task_type="content.class_evaluation",
                delivery_channel="CONTENT_ANALYSIS",
                routing_key="content.class_evaluation",
                status="processing",
                params=params,
                publish_status="PUBLISHED",
                message_id=f"message-{task_id}",
                execution_generation=generation,
            )
        )


def test_secrets_are_encrypted_and_never_in_management_views(config_db):
    store, management = config_db
    published = publish_initial(store, management)
    assert "private-token" not in str(
        store.list_versions("content-analysis", "model_connection")
    )
    assert published["has_api_key"] is True
    with store.session_factory() as session:
        row = session.get(ServiceConfigVersionModel, published["id"])
        assert "private-token" not in row.secret_ciphertext
        assert "api_key" not in row.content
    snapshot = store.snapshot("content-analysis", "model_connection")
    assert snapshot.api_key == "private-token"
    assert "private-token" not in repr(snapshot)


def test_optimistic_publication_and_idempotent_retry(config_db):
    store, management = config_db
    first = publish_initial(store, management)
    a = draft(store, revision=1, content=connection(url="https://a.example/v1"))
    b = draft(store, revision=1, content=connection(url="https://b.example/v1"))
    management.publish("content-analysis", "model_connection", a["id"])
    with pytest.raises(ConfigConflict):
        management.publish("content-analysis", "model_connection", b["id"])
    assert (
        management.publish("content-analysis", "model_connection", first["id"])[
            "revision"
        ]
        == 1
    )
    assert store.snapshot("content-analysis", "model_connection").id == a["id"]


def test_draft_idempotency_rejects_changed_request(config_db):
    store, _ = config_db
    first = draft(store, request_id="same", api_key="private-token")
    assert draft(store, request_id="same", api_key="private-token")["id"] == first["id"]
    with pytest.raises(ConfigConflict):
        draft(store, request_id="same", api_key="different")


def test_joint_model_update_and_endpoint_switch_are_atomic(config_db):
    store, management = config_db
    initial = publish_initial(store, management)
    updates = management.prompts("content-analysis")
    updates[0]["models"] = dict.fromkeys(updates[0]["models"], "new-model")
    value = draft(
        store,
        revision=1,
        content=connection("new-model", "https://new.example/v1"),
        prompt_updates=updates,
    )
    management.publish("content-analysis", "model_connection", value["id"])
    assert set(management.prompts("content-analysis")[0]["models"].values()) == {
        "new-model"
    }
    assert (
        store.snapshot("content-analysis", "model_connection").content["base_url"]
        == "https://new.example/v1"
    )
    assert store.snapshot(
        "content-analysis", "model_connection", initial["id"]
    ).content["allowed_models"] == ["old-model"]
    invalid = draft(store, revision=2, content=connection("unsupported"))
    with pytest.raises(ValueError):
        management.publish("content-analysis", "model_connection", invalid["id"])
    assert store.snapshot("content-analysis", "model_connection").id == value["id"]


def test_school_overrides_are_validated_before_publish(config_db):
    store, management = config_db
    publish_initial(store, management)
    with store.session_factory.begin() as session:
        repo = PromptBundleRepository(session)
        row = repo.create_draft(
            load_bootstrap_prompt_bundle("school-model"),
            school_code="school-1",
            operator="test",
        )
        repo.publish(row.id, operator="test")
    value = draft(store, revision=1)
    with pytest.raises(ValueError, match="名单"):
        management.publish("content-analysis", "model_connection", value["id"])
    assert store.snapshot("content-analysis", "model_connection").revision == 1


def test_pending_task_and_cross_generation_retry_keep_connection_and_prompt(config_db):
    store, management = config_db
    first = publish_initial(store, management)
    reader = AnalysisConfigReader(store, "content-analysis")
    with store.session_factory.begin() as session:
        params = reader.prepare(
            session,
            "school-1",
            {"taskId": "business-1", "_runtimeConfig": {"connection_id": "forged"}},
        )
    add_task(store, params)
    changes = management.prompts("content-analysis")
    changes[0]["models"] = dict.fromkeys(changes[0]["models"], "new-model")
    second = draft(
        store, revision=1, content=connection("new-model"), prompt_updates=changes
    )
    management.publish("content-analysis", "model_connection", second["id"])
    assert reader.connection(reader.task_params("task-1", 1)).id == first["id"]
    with store.session_factory.begin() as session:
        session.get(MediaTaskModel, "task-1").execution_generation = 2
    assert reader.task_params("task-1", 2)["_promptBundle"] == params["_promptBundle"]
    with pytest.raises(ValueError, match="代次"):
        reader.task_params("task-1", 1)
    with store.session_factory.begin() as session:
        new = reader.prepare(session, "school-1", {})
    assert new["_runtimeConfig"]["connection_id"] == second["id"]
    assert new["_promptBundle"]["id"] != params["_promptBundle"]["id"]


@pytest.mark.parametrize("has_connection_reference", [False, True])
def test_historical_task_uses_imported_baseline_not_latest(
    config_db, monkeypatch, has_connection_reference
):
    store, management = config_db
    monkeypatch.setenv("RUNTIME_CONFIG_BOOTSTRAP", "true")
    initialize_analysis_config(
        store,
        "content-analysis",
        connection(),
        {"task_concurrency": 1, "model_concurrency": 1},
        "old-key",
    )
    baseline = store.snapshot("content-analysis", "model_connection")
    old_prompt = management.prompts("content-analysis")[0]["bundle_id"]
    legacy_params = {"taskId": "legacy"}
    if has_connection_reference:
        legacy_params["_runtimeConfig"] = {"connection_id": baseline.id}
    add_task(store, legacy_params)
    changes = management.prompts("content-analysis")
    changes[0]["models"] = dict.fromkeys(changes[0]["models"], "new-model")
    newer = draft(
        store, revision=1, content=connection("new-model"), prompt_updates=changes
    )
    management.publish("content-analysis", "model_connection", newer["id"])
    reader = AnalysisConfigReader(store, "content-analysis")
    params = reader.task_params("task-1", 1)
    assert reader.connection(params).id == baseline.id
    assert params["_promptBundle"]["id"] == old_prompt


def test_environment_import_includes_school_specific_models(config_db, monkeypatch):
    store, _ = config_db
    with store.session_factory.begin() as session:
        repo = PromptBundleRepository(session)
        row = repo.create_draft(
            load_bootstrap_prompt_bundle("school-model"),
            school_code="school-1",
            operator="test",
        )
        repo.publish(row.id, operator="test")
    monkeypatch.setenv("RUNTIME_CONFIG_BOOTSTRAP", "true")
    initialize_analysis_config(
        store,
        "content-analysis",
        connection(),
        {"task_concurrency": 1, "model_concurrency": 1},
        "old-key",
    )
    assert store.snapshot("content-analysis", "model_connection").content[
        "allowed_models"
    ] == ["old-model", "school-model"]
    with store.session_factory.begin() as session:
        params = AnalysisConfigReader(store, "content-analysis").prepare(
            session, "school-1", {}
        )
    assert params["_promptBundle"]["schoolCode"] == "school-1"


def test_current_config_remains_available_after_many_drafts(config_db):
    store, management = config_db
    current = publish_initial(store, management)
    with store.session_factory.begin() as session:
        session.get(ServiceConfigVersionModel, current["id"]).created_at = datetime(
            2020, 1, 1
        )
    current = store.list_versions("content-analysis", "model_connection")["current"]
    for _ in range(101):
        draft(store, revision=1)
    listed = store.list_versions("content-analysis", "model_connection")
    assert len(listed["versions"]) == 100
    assert all(row["id"] != current["id"] for row in listed["versions"])
    assert listed["current"] == current


def test_editing_draft_preserves_new_secret_and_rejects_changed_source(config_db):
    store, management = config_db
    original = publish_initial(store, management)
    changed = draft(store, revision=1, api_key="replacement-key")
    edited = draft(
        store, revision=1, source_version_id=changed["id"], request_id="edit-draft"
    )
    assert "replacement-key" not in str(edited)
    with pytest.raises(ConfigConflict):
        draft(
            store, revision=1, source_version_id=original["id"], request_id="edit-draft"
        )
    management.publish("content-analysis", "model_connection", edited["id"])
    assert (
        store.snapshot("content-analysis", "model_connection").api_key
        == "replacement-key"
    )


def test_first_publish_refuses_unversioned_active_tasks(config_db):
    store, management = config_db
    add_task(store, {})
    value = draft(store, api_key="key")
    with pytest.raises(ValueError, match="基线"):
        management.publish("content-analysis", "model_connection", value["id"])


def test_old_online_instance_blocks_release(config_db):
    store, management = config_db
    with store.session_factory.begin() as session:
        session.add(
            MediaNodeModel(
                id="node-1",
                node_code="old",
                node_name="old",
                node_type="CONTENT_ANALYSIS",
                status="ONLINE",
            )
        )
    value = draft(store, api_key="key")
    with pytest.raises(ValueError, match="在线实例"):
        management.publish("content-analysis", "model_connection", value["id"])


@pytest.mark.parametrize(
    "bad",
    [
        {"base_url": "file:///tmp/model"},
        {"base_url": "https://user:secret@model/v1"},
        {"base_url": "https://model/v1?token=private"},
        {"base_url": "https://model/v1/chat/completions"},
        {"unknown": 1},
        {"allowed_models": []},
        {"timeout_seconds": 0},
        {"schema_version": 2},
    ],
)
def test_model_config_rejects_invalid_fields(config_db, bad):
    store, _ = config_db
    with pytest.raises(ValueError):
        draft(store, content={**connection(), **bad})


def test_performance_shrink_preserves_inflight_work():
    limits = AdjustableLimits(task=8, model=8)
    with ExitStack() as stack:
        assert all(stack.enter_context(limits.try_slot("model")) for _ in range(8))
        limits.update(task=3, model=3)
        assert limits.snapshot()["model"] == {"active": 8, "limit": 3}
        with limits.try_slot("model") as allowed:
            assert not allowed
    with limits.try_slot("model") as allowed:
        assert allowed
    assert limits.snapshot()["model"]["active"] == 0


def test_image_shrink_keeps_original_stage_counter():
    limits = InstanceConcurrency(ImageConcurrencySettings(model_concurrency=8))
    with ExitStack() as stack:
        assert all(
            stack.enter_context(limits.try_slot(ConcurrencyStage.MODEL))
            for _ in range(8)
        )
        limits.update_limits(ImagePerformanceConfig(model_concurrency=3).model_dump())
        assert limits.snapshot()["model"] == {"active": 8, "limit": 3}
        with limits.try_slot(ConcurrencyStage.MODEL) as allowed:
            assert not allowed


def test_refresher_keeps_last_applied_version_on_error(config_db):
    store, management = config_db
    first = publish_initial(store, management)
    calls = []
    refresh = RuntimeConfigRefresher(
        store, "content-analysis", {"model_connection": lambda s: calls.append(s.id)}
    )
    refresh.refresh_once()
    refresh.refresh_once()
    assert calls == [first["id"]]
    newer = draft(store, revision=1)
    management.publish("content-analysis", "model_connection", newer["id"])

    def fail(_):
        raise ValueError("must-not-appear-private-token")

    refresh.appliers["model_connection"] = fail
    refresh.refresh_once()
    state = refresh.status()["model_connection"]
    assert state["applied_id"] == first["id"] and state["target_id"] == newer["id"]
    assert state["error"] == "ValueError"


def test_prefetch_runs_on_io_callback_and_survives_reconnect():
    from types import SimpleNamespace

    callbacks, qos = [], []
    consumer = PikaTaskConsumer(RabbitMQConsumerConfig(host="unused"), lambda _: None)
    consumer._connection = SimpleNamespace(
        is_closed=False, add_callback_threadsafe=callbacks.append
    )
    consumer._channel = SimpleNamespace(
        is_closed=False, basic_qos=lambda **kw: qos.append(kw)
    )
    consumer.update_prefetch(3)
    assert not qos and consumer.config.prefetch_count == 3
    callbacks.pop()()
    assert qos == [{"prefetch_count": 3, "global_qos": True}]
    consumer._connection.is_closed = True
    consumer.update_prefetch(8)
    assert consumer.config.prefetch_count == 8 and qos == [
        {"prefetch_count": 3, "global_qos": True}
    ]


def test_new_channel_has_no_stale_consumer_limit(monkeypatch):
    from types import SimpleNamespace
    from media_platform.infrastructure.messaging import pika_task_consumer as module

    calls = []
    channel = SimpleNamespace(
        confirm_delivery=lambda: None, basic_qos=lambda **kw: calls.append(kw)
    )
    connection = SimpleNamespace(channel=lambda: channel)
    monkeypatch.setattr(module.pika, "BlockingConnection", lambda _: connection)
    consumer = PikaTaskConsumer(
        RabbitMQConsumerConfig(host="unused", prefetch_count=4), lambda _: None
    )
    monkeypatch.setattr(consumer, "_declare_topology", lambda: None)
    consumer.connect()
    assert calls == [
        {"prefetch_count": 0, "global_qos": False},
        {"prefetch_count": 4, "global_qos": True},
    ]


@dataclass
class ConfigTestRuntime:
    runtime_config_service: RuntimeConfigManagementService
    api_key: str = "business-key"

    async def start(self):
        pass

    async def close(self):
        pass


def test_admin_api_auth_validation_redaction_and_publish(config_db, monkeypatch):
    store, management = config_db
    monkeypatch.setenv("CONFIG_ADMIN_API_KEY", "separate-admin")
    app = create_control_center_app(lambda: ConfigTestRuntime(management))
    url = "/api/v1/admin/runtime-configs/content-analysis/model_connection"
    headers = {"X-Config-Admin-Key": "separate-admin"}
    with TestClient(app) as client:
        assert client.get(url, headers={"X-API-Key": "business-key"}).status_code == 401
        assert client.get(url, headers=headers).status_code == 200
        bad = client.post(
            url + "/drafts", headers=headers, json={"api_key": "private-token"}
        )
        assert bad.status_code == 422 and "private-token" not in bad.text
        created = client.post(
            url + "/drafts",
            headers=headers,
            json={
                "content": connection(),
                "api_key": "private-token",
                "expected_revision": 0,
                "request_id": "admin-1",
                "reason": "测试地址切换",
                "prompt_updates": management.prompts("content-analysis"),
            },
        )
        assert created.status_code == 201, created.text
        assert "private-token" not in created.text
        assert (
            client.post(
                url + f"/{created.json()['id']}/publish", headers=headers
            ).status_code
            == 200
        )
        listed = client.get(url, headers=headers)
        assert listed.json()["revision"] == 1 and "private-token" not in listed.text


@pytest.mark.parametrize("supported", [False, True])
def test_admin_api_reports_hot_update_support_before_first_publish(
    config_db, monkeypatch, supported
):
    store, management = config_db
    monkeypatch.setenv("CONFIG_ADMIN_API_KEY", "separate-admin")
    with store.session_factory.begin() as session:
        session.add(
            MediaNodeModel(
                node_code="content-01",
                node_name="content-01",
                node_type="CONTENT_ANALYSIS",
                status="ONLINE",
                capacity_config={"runtime_config_supported": True} if supported else {},
            )
        )
    app = create_control_center_app(lambda: ConfigTestRuntime(management))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/admin/runtime-configs/content-analysis/performance",
            headers={"X-Config-Admin-Key": "separate-admin"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["current_id"] is None and data["revision"] == 0
    assert data["instances"][0]["runtime_config_supported"] is supported
    assert data["instances"][0]["runtime_config"] == {}


def test_rollback_creates_new_release_and_restores_secret(config_db):
    store, management = config_db
    original = publish_initial(store, management)
    updated = draft(
        store,
        revision=1,
        api_key="new-key",
        content=connection(url="https://second.example/v1"),
    )
    management.publish("content-analysis", "model_connection", updated["id"])
    rollback = draft(
        store,
        revision=2,
        source_version_id=original["id"],
        content=original["content"],
        prompt_updates=management.prompts("content-analysis"),
    )
    released = management.publish(
        "content-analysis", "model_connection", rollback["id"]
    )
    restored = store.snapshot("content-analysis", "model_connection")
    assert released["revision"] == 3 and restored.api_key == "private-token"
    assert (
        store.snapshot("content-analysis", "model_connection", updated["id"]).api_key
        == "new-key"
    )


def test_content_workflows_use_distinct_task_clients_and_close_them(config_db):
    import asyncio
    from threading import Event
    from media_platform.contracts.task import TaskDispatchMessage
    from services.content_analysis.application.workflow import ClassEvaluationWorkflow

    store, management = config_db
    first = publish_initial(store, management)
    reader = AnalysisConfigReader(store, "content-analysis")
    with store.session_factory.begin() as session:
        old_params = reader.prepare(session, "school-1", {})
    add_task(store, old_params, task_id="old-task")
    new = draft(store, revision=1, content=connection(url="https://second.example/v1"))
    management.publish("content-analysis", "model_connection", new["id"])
    with store.session_factory.begin() as session:
        new_params = reader.prepare(session, "school-1", {})
    add_task(store, new_params, task_id="new-task")
    created = []

    class Client:
        closed = False

        def __init__(self, snapshot):
            self.snapshot = snapshot
            created.append(self)

        async def close(self):
            self.closed = True

    class Workflow(ClassEvaluationWorkflow):
        async def _execute(self, message, generation, lease_lost):
            await asyncio.sleep(0)
            assert not self.llm_client.closed
            assert (
                self.llm_client.snapshot.id
                == message.params["_runtimeConfig"]["connection_id"]
            )
            return self.connection_version

    workflow = Workflow(
        store.session_factory,
        subtitle_client=None,
        behavior_client=None,
        file_preprocessor=None,
        llm_client=None,
        progress_callback=None,
        config_reader=reader,
        client_factory=Client,
    )

    def message(task_id):
        return TaskDispatchMessage(
            task_id=task_id,
            message_id=f"message-{task_id}",
            school_code="school-1",
            task_type="content.class_evaluation",
            routing_key="content.class_evaluation",
            delivery_channel="CONTENT_ANALYSIS",
            params={"_runtimeConfig": {"connection_id": "untrusted-message-value"}},
        )

    async def run():
        return await asyncio.gather(
            workflow.execute(message("old-task"), 1, Event()),
            workflow.execute(message("new-task"), 1, Event()),
        )

    assert asyncio.run(run()) == [first["id"], new["id"]]
    assert workflow.llm_client is None
    assert len(created) == 2 and all(client.closed for client in created)


def test_image_historical_prompt_remains_valid_after_whitelist_change(config_db):
    store, management = config_db
    policy = DatabaseImagePromptPolicy(store, {"old-model"})
    ensure_image_prompt_bootstrap(
        store.session_factory, lambda: image_seed("old-model"), policy
    )
    first = draft(
        store,
        service="image-analysis",
        api_key="key",
        prompt_updates=management.prompts("image-analysis"),
    )
    management.publish("image-analysis", "model_connection", first["id"])
    reader = ImagePromptReader(store.session_factory, policy)
    locked = reader.published("GLOBAL")
    changes = management.prompts("image-analysis")
    changes[0]["models"] = {"default": "new-model"}
    newer = draft(
        store,
        service="image-analysis",
        revision=1,
        content=connection("new-model"),
        prompt_updates=changes,
    )
    management.publish("image-analysis", "model_connection", newer["id"])
    assert reader.published("GLOBAL").content.model == "new-model"
    assert (
        reader.locked(
            locked.id,
            school_code="GLOBAL",
            expected_version=locked.version,
            expected_hash=locked.content_hash,
        ).content.model
        == "old-model"
    )
