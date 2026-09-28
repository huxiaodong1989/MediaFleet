import asyncio
from datetime import datetime, timedelta
from threading import Event

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.content_evaluation import PromptStepDefinition
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.messaging import TaskBusyError, TaskRetryableError
from services.content_analysis.application.task_execution_service import (
    ContentTaskExecutionService,
)
from services.content_analysis.application.workflow import (
    ClassEvaluationWorkflow,
    EvaluationOutcome,
)


class Callback:
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    async def notify(self, url, payload, token=None):
        self.calls.append((url, payload, token))
        return self.success


class SuccessfulWorkflow:
    def __init__(self):
        self.progress_callback = Callback()
        self.execute_calls = 0

    async def execute(self, message, generation, lease_lost: Event):
        self.execute_calls += 1
        assert generation == 1
        assert lease_lost.is_set() is False
        return EvaluationOutcome(
            result={"taskId": message.business_task_id, "status": "COMPLETED"},
            business_task_id=message.business_task_id,
            callback_url="https://example.invalid/callback",
            callback_token=None,
        )


class TakenOverWorkflow(SuccessfulWorkflow):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory

    async def execute(self, message, generation, lease_lost: Event):
        with self.factory.begin() as session:
            task = session.get(MediaTaskModel, message.task_id)
            task.execution_generation = generation + 1
            task.executor_node_id = "content-worker-new"
            task.lease_owner = "content-worker-new"
            task.lease_expires_at = datetime.now() + timedelta(minutes=30)
        return await super().execute(message, generation, lease_lost)


class FlakyLlmClient:
    def __init__(self):
        self.calls = 0

    async def analyze(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("临时模型错误\n第二行")
        return {"ok": True}, {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}


def _setup(tmp_path, name):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    MediaTaskModel.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            MediaTaskModel(
                id="task-1",
                business_task_id="business-1",
                task_type="content.class_evaluation",
                delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS.value,
                routing_key="content.class_evaluation",
                status=TaskStatus.PENDING.value,
                params={},
                publish_status=PublishStatus.PUBLISHED.value,
                message_id="message-1",
                max_retries=3,
                school_code="school-1",
                created_by="test",
                updated_by="test",
            )
        )
    message = TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        business_task_id="business-1",
        school_code="school-1",
        task_type="content.class_evaluation",
        routing_key="content.class_evaluation",
        delivery_channel=TaskDeliveryChannel.CONTENT_ANALYSIS,
        params={},
    )
    return engine, factory, message


def test_content_task_completion_uses_generation_cas(tmp_path, caplog):
    engine, factory, message = _setup(tmp_path, "content-complete.db")
    try:
        service = ContentTaskExecutionService(
            factory,
            SuccessfulWorkflow(),
            "content-worker-1",
            lease_renew_interval=timedelta(minutes=10),
        )
        with caplog.at_level("INFO"):
            service.handle(message)

        with factory() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.COMPLETED.value
            assert task.result["status"] == "COMPLETED"
            assert task.execution_generation == 1
            assert task.callback_result["success"] is True
        assert "AI评课任务开始执行" in caplog.text
        assert "business_task_id=business-1" in caplog.text
        assert "AI评课任务结果已写回" in caplog.text
        assert "AI评课任务执行成功" in caplog.text
    finally:
        engine.dispose()


def test_old_content_executor_cannot_overwrite_takeover_result(tmp_path):
    engine, factory, message = _setup(tmp_path, "content-takeover.db")
    try:
        service = ContentTaskExecutionService(
            factory,
            TakenOverWorkflow(factory),
            "content-worker-old",
            lease_renew_interval=timedelta(minutes=10),
        )
        with pytest.raises(TaskBusyError):
            service.handle(message)

        with factory() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.PROCESSING.value
            assert task.executor_node_id == "content-worker-new"
            assert task.result is None
            assert task.execution_generation == 2
    finally:
        engine.dispose()


def test_terminal_redelivery_recovers_callback_without_reprocessing(tmp_path):
    engine, factory, message = _setup(tmp_path, "content-callback-recovery.db")
    try:
        with factory.begin() as session:
            task = session.get(MediaTaskModel, message.task_id)
            task.status = TaskStatus.COMPLETED.value
            task.progress = 100
            task.result = {"taskId": "business-1", "status": "COMPLETED"}
            task.callback_url = "https://example.invalid/callback"
            task.params = {"webhookToken": "secret-token"}
            task.callback_result = None
        workflow = SuccessfulWorkflow()
        service = ContentTaskExecutionService(
            factory,
            workflow,
            "content-worker-1",
            lease_renew_interval=timedelta(minutes=10),
        )

        service.handle(message)

        assert workflow.execute_calls == 0
        assert workflow.progress_callback.calls == [
            (
                "https://example.invalid/callback",
                {"taskId": "business-1", "status": "COMPLETED"},
                "secret-token",
            )
        ]
        with factory() as session:
            task = session.get(MediaTaskModel, message.task_id)
            assert task.status == TaskStatus.COMPLETED.value
            assert task.callback_result["success"] is True
            assert task.callback_result["recovered"] is True
    finally:
        engine.dispose()


def test_terminal_callback_failure_retries_callback_without_reopening_task(tmp_path):
    engine, factory, message = _setup(tmp_path, "content-callback-retry.db")
    try:
        with factory.begin() as session:
            task = session.get(MediaTaskModel, message.task_id)
            task.status = TaskStatus.COMPLETED.value
            task.progress = 100
            task.result = {"taskId": "business-1", "status": "COMPLETED"}
            task.callback_url = "https://example.invalid/callback"
            task.callback_result = None
        workflow = SuccessfulWorkflow()
        workflow.progress_callback = Callback(success=False)
        service = ContentTaskExecutionService(
            factory,
            workflow,
            "content-worker-1",
            lease_renew_interval=timedelta(minutes=10),
        )

        with pytest.raises(TaskRetryableError, match="终态业务回调失败"):
            service.handle(message)

        assert workflow.execute_calls == 0
        with factory() as session:
            task = session.get(MediaTaskModel, message.task_id)
            assert task.status == TaskStatus.COMPLETED.value
            assert task.result == {"taskId": "business-1", "status": "COMPLETED"}
            assert task.callback_result["success"] is False
    finally:
        engine.dispose()


def test_initial_callback_failure_keeps_completed_result_and_retries_only_callback(tmp_path):
    engine, factory, message = _setup(tmp_path, "content-initial-callback-retry.db")
    try:
        workflow = SuccessfulWorkflow()
        workflow.progress_callback = Callback(success=False)
        service = ContentTaskExecutionService(
            factory,
            workflow,
            "content-worker-1",
            lease_renew_interval=timedelta(minutes=10),
        )

        with pytest.raises(TaskRetryableError, match="终态业务回调失败"):
            service.handle(message)

        assert workflow.execute_calls == 1
        with factory() as session:
            task = session.get(MediaTaskModel, message.task_id)
            assert task.status == TaskStatus.COMPLETED.value
            assert task.result == {"taskId": "business-1", "status": "COMPLETED"}
            assert task.callback_result["success"] is False
    finally:
        engine.dispose()


def test_model_retry_log_contains_task_and_step_context(caplog):
    llm_client = FlakyLlmClient()
    workflow = ClassEvaluationWorkflow(
        lambda: None,
        subtitle_client=None,
        behavior_client=None,
        file_preprocessor=None,
        llm_client=llm_client,
        progress_callback=None,
        llm_max_retries=2,
    )
    step = PromptStepDefinition(
        code="CLASSROOM_SUMMARY",
        name="课堂摘要",
        order=1,
        system_prompt="系统提示词",
        user_prompt="用户提示词",
        model="test-model",
    )

    with caplog.at_level("WARNING"):
        result, usage = asyncio.run(
            workflow._run_step(
                step,
                "课堂材料",
                "已解析提示词",
                task_id="task-1",
                business_task_id="business-1",
                step_position=1,
                total_steps=8,
            )
        )

    assert result == {"ok": True}
    assert usage["total_tokens"] == 12
    assert "AI评课步骤模型调用失败" in caplog.text
    assert "business_task_id=business-1" in caplog.text
    assert "step=1/8" in caplog.text
    assert "will_retry=True" in caplog.text
    assert "临时模型错误 第二行" in caplog.text
