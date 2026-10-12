"""图像任务领取和重试代次恢复。"""

from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from media_platform.contracts.image_analysis import IMAGE_TASK_TYPE
from media_platform.contracts.task import TaskDeliveryChannel, TaskDispatchMessage
from media_platform.infrastructure.database.models import (
    MediaNodeModel,
    MediaTaskModel,
    RecordingServerModel,
)
from media_platform.infrastructure.messaging import TaskBusyError, TaskRetryableError
from services.image_analysis.application.task_handler import ImageTaskHandler
from services.image_analysis.planning import SamplingLimits
from services.image_analysis.source_selection import SourceSelectionPolicy


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{(tmp_path / 'task-handler.db').as_posix()}")
    MediaTaskModel.metadata.create_all(
        engine,
        tables=[
            MediaNodeModel.__table__,
            RecordingServerModel.__table__,
            MediaTaskModel.__table__,
        ],
    )
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _handler(factory):
    return ImageTaskHandler(
        factory,
        pipeline=object(),
        prompt_reader=object(),
        video_source=object(),
        video_probe=object(),
        worker_id="image-node-1",
        selection_policy=SourceSelectionPolicy(
            version="test",
            minimum_classroom_coverage=0.5,
            minimum_duration_ratio_to_longest=0.8,
            require_continuity=False,
        ),
        sampling_limits=SamplingLimits(
            min_interval_seconds=30,
            max_videos=1,
            max_classroom_duration_ms=60_000,
            max_samples=1,
            max_images=1,
        ),
        lease_timeout=timedelta(minutes=10),
    )


def _message(*, attempt: int):
    return TaskDispatchMessage(
        message_id="message-1",
        task_id="task-1",
        school_code="school-1",
        task_type=IMAGE_TASK_TYPE,
        routing_key="image.classroom.analyze",
        delivery_channel=TaskDeliveryChannel.IMAGE_ANALYSIS,
        params={},
        attempt=attempt,
        max_attempts=3,
    )


def _task(factory, *, status="pending", retry_count=1):
    with factory.begin() as session:
        session.add(
            MediaTaskModel(
                id="task-1",
                message_id="message-1",
                task_type=IMAGE_TASK_TYPE,
                delivery_channel=TaskDeliveryChannel.IMAGE_ANALYSIS.value,
                routing_key="image.classroom.analyze",
                status=status,
                retry_count=retry_count,
                max_retries=3,
                publish_status="PUBLISHED",
                school_code="school-1",
                params={},
                created_by="test",
                updated_by="test",
            )
        )


def test_pending_task_rejects_redelivered_message_with_older_attempt(factory):
    _task(factory, status="pending", retry_count=1)

    with pytest.raises(TaskRetryableError, match="代次落后"):
        _handler(factory)._claim(_message(attempt=0))

    with factory() as session:
        task = session.get(MediaTaskModel, "task-1")
        assert task.status == "pending"
        assert task.retry_count == 1


def test_corrected_attempt_can_claim_pending_retry_checkpoint(factory):
    _task(factory, status="pending", retry_count=1)

    claimed = _handler(factory)._claim(_message(attempt=1))

    assert claimed is not None
    generation, params = claimed
    assert generation == 1
    assert params == {}
    with factory() as session:
        task = session.get(MediaTaskModel, "task-1")
        assert task.status == "processing"
        assert task.retry_count == 1
        assert task.lease_owner == "image-node-1"


def test_old_duplicate_does_not_advance_while_current_attempt_is_processing(factory):
    _task(factory, status="processing", retry_count=1)

    with pytest.raises(TaskBusyError):
        _handler(factory)._claim(_message(attempt=0))
