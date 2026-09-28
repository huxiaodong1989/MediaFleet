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
