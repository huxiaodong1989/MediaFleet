from datetime import datetime, timedelta
from threading import Event

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.domain.task import PublishStatus, TaskStatus
from media_platform.infrastructure.database.models import MediaTaskModel
from media_platform.infrastructure.messaging import TaskBusyError
from services.content_analysis.application.task_execution_service import (
    ContentTaskExecutionService,
)
from services.content_analysis.application.workflow import EvaluationOutcome


class Callback:
    async def notify(self, url, payload, token=None):
        return True


class SuccessfulWorkflow:
    def __init__(self):
        self.progress_callback = Callback()

    async def execute(self, message, generation, lease_lost: Event):
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


def test_content_task_completion_uses_generation_cas(tmp_path):
    engine, factory, message = _setup(tmp_path, "content-complete.db")
    try:
        service = ContentTaskExecutionService(
            factory,
            SuccessfulWorkflow(),
            "content-worker-1",
            lease_renew_interval=timedelta(minutes=10),
        )
        service.handle(message)

        with factory() as session:
            task = session.get(MediaTaskModel, "task-1")
            assert task.status == TaskStatus.COMPLETED.value
            assert task.result["status"] == "COMPLETED"
            assert task.execution_generation == 1
            assert task.callback_result["success"] is True
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
