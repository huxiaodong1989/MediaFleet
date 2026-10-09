"""调用中心图像课堂接单：同一事务锁定发布提示词与公共任务。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from media_platform.application.image_prompt_service import (
    ImagePromptPolicy,
    ImagePromptReader,
)
from media_platform.application.task_dispatch_service import (
    CreateTaskCommand,
    TaskDispatchService,
)
from media_platform.contracts.image_analysis import (
    IMAGE_TASK_TYPE,
    ImageAnalysisRequest,
)
from media_platform.contracts.task import TaskDeliveryChannel
from media_platform.infrastructure.database.models import MediaTaskModel


@dataclass(frozen=True)
class ImageIntakeResult:
    task_id: str
    created: bool


class ImageAnalysisIntakeService:
    def __init__(self, dispatch: TaskDispatchService, policy: ImagePromptPolicy):
        self.dispatch = dispatch
        self.reader = ImagePromptReader(dispatch.session_factory, policy)

    def create(self, request: ImageAnalysisRequest) -> ImageIntakeResult:
        fingerprint = request.semantic_fingerprint()

        def prepare(session):
            snapshot = self.reader.published_in_session(session, request.school_id)
            mentioned = set(re.findall(r"\b[A-Z][A-Z_]+\b", snapshot.content.prompt))
            missing = sorted(
                metric.value
                for metric in request.requested_metrics
                if metric.value not in mentioned
            )
            if missing:
                raise ValueError("已发布图像提示词未覆盖请求指标: " + ",".join(missing))
            return {
                "request": request.to_payload(),
                "semanticFingerprint": fingerprint,
                "promptBundle": {
                    "id": snapshot.id,
                    "schoolCode": snapshot.school_code,
                    "version": snapshot.version,
                    "contentHash": snapshot.content_hash,
                },
            }

        def validate_existing(task: MediaTaskModel) -> None:
            if (
                task.task_type != IMAGE_TASK_TYPE
                or task.delivery_channel != TaskDeliveryChannel.IMAGE_ANALYSIS.value
                or (task.params or {}).get("semanticFingerprint") != fingerprint
            ):
                raise ValueError("messageId已用于不同课堂图像分析请求")

        result = self.dispatch.create_task(
            CreateTaskCommand(
                school_code=request.school_id,
                task_type=IMAGE_TASK_TYPE,
                delivery_channel=TaskDeliveryChannel.IMAGE_ANALYSIS,
                business_task_id=request.message_id,
                routing_key=IMAGE_TASK_TYPE,
                idempotency_key=request.idempotency_key(),
                created_by="image-analysis-api",
            ),
            params_factory=prepare,
            validate_existing=validate_existing,
        )
        return ImageIntakeResult(result.task_id, result.created)


__all__ = ["ImageAnalysisIntakeService", "ImageIntakeResult"]
