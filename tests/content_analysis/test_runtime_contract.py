import sys
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from media_platform.contracts.task import TaskDeliveryChannel
from media_platform.contracts.topology import (
    CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
    CONTENT_ANALYSIS_TASK_EXCHANGE,
    CONTENT_ANALYSIS_TASK_QUEUE,
    CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE,
)
from media_platform.infrastructure.messaging import RabbitMQConsumerConfig
from services.content_analysis.application import runtime as runtime_module
from services.content_analysis.infrastructure.task_consumer import (
    ContentAnalysisTaskConsumer,
)


def test_content_analysis_uses_independent_shared_queue():
    config = RabbitMQConsumerConfig(
        host="rabbitmq",
        exchange=CONTENT_ANALYSIS_TASK_EXCHANGE,
        retry_exchange=CONTENT_ANALYSIS_TASK_RETRY_EXCHANGE,
        dead_letter_exchange=CONTENT_ANALYSIS_TASK_DEAD_LETTER_EXCHANGE,
        queue_name=CONTENT_ANALYSIS_TASK_QUEUE,
        routing_keys=("content.#",),
    )

    assert config.queue_name == "content-analysis.tasks"
    assert config.routing_keys == ("content.#",)
    assert config.exchange != "media.task"
    assert TaskDeliveryChannel.CONTENT_ANALYSIS.value == "CONTENT_ANALYSIS"


def test_concurrent_bootstrap_reuses_version_created_by_another_instance(monkeypatch, caplog):
    class Context:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def begin(self):
            return Context()

    class Repository:
        attempts = 0

        def __init__(self, session):
            self.session = session

        def ensure_bootstrap(self, content):
            Repository.attempts += 1
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

        def get_published(self, school_code):
            assert school_code == "GLOBAL"
            return SimpleNamespace(version=1, id="prompt-v1")

    monkeypatch.setattr(runtime_module, "PromptBundleRepository", Repository)

    with caplog.at_level("INFO"):
        runtime_module._ensure_bootstrap_prompt(Context, "test-model")

    assert Repository.attempts == 1
    assert "其他内容分析实例已并发完成提示词初始化" in caplog.text


def test_build_content_analysis_runtime_completes_dependency_wiring(monkeypatch):
    from media_platform.common import config as config_module
    from media_platform.infrastructure.database import init as database_init_module

    settings = SimpleNamespace(
        API_KEY="test-api-key",
        rabbitmq=SimpleNamespace(
            host="rabbitmq",
            port=5672,
            username="guest",
            password="guest",
            vhost="/",
            heartbeat=60,
            blocked_connection_timeout=300,
        ),
    )
    initialized_with = []

    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        database_init_module,
        "init_database",
        lambda value: initialized_with.append(value),
    )
    monkeypatch.setattr(runtime_module, "_ensure_bootstrap_prompt", lambda *_: None)
    monkeypatch.setitem(
        sys.modules,
        "media_platform.infrastructure.database.session",
        SimpleNamespace(SessionLocal=SimpleNamespace()),
    )
    monkeypatch.setattr(
        runtime_module,
        "EvaluationLlmClient",
        lambda **_: SimpleNamespace(),
    )
    monkeypatch.setenv("CONTENT_ANALYSIS_CONSUMER_ENABLED", "false")

    runtime = runtime_module.build_content_analysis_runtime()

    assert initialized_with == [settings]
    assert isinstance(runtime.consumer, ContentAnalysisTaskConsumer)
    assert runtime.worker_id
    assert runtime.consumer_enabled is False
