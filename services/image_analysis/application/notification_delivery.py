"""从MySQL通知意图发布图像时间点结果；与课堂消费生命周期解耦。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Protocol

from sqlalchemy.orm import Session

from services.image_analysis.infrastructure.repositories import ImageAnalysisRepository


LOGGER = logging.getLogger(__name__)


class ImageResultPublisher(Protocol):
    def publish(self, *, event_id: str, payload: dict) -> None:
        ...


@dataclass(frozen=True)
class DeliveryBatchResult:
    claimed: int
    published: int
    failed: int


class ImageNotificationDelivery:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        publisher: ImageResultPublisher,
        *,
        instance_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 10,
    ) -> None:
        if not instance_id or lease_seconds < 1 or max_attempts < 1:
            raise ValueError("通知发布配置无效")
        self.session_factory = session_factory
        self.publisher = publisher
        self.instance_id = instance_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def deliver_batch(self, *, limit: int = 20) -> DeliveryBatchResult:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            pending = ImageAnalysisRepository(session).claim_notifications(
                owner=self.instance_id,
                now=now,
                lease_seconds=self.lease_seconds,
                limit=limit,
                max_attempts=self.max_attempts,
            )
        published = 0
        failed = 0
        for item in pending:
            confirmed = False
            try:
                self.publisher.publish(event_id=item.event_id, payload=item.payload)
                confirmed = True
            except Exception:
                # 不记录消息体、图片URL或签名；下轮只补发通知，不重跑模型。
                LOGGER.warning("图像通知发布失败: sample_id=%s", item.sample_id)
            with self.session_factory.begin() as session:
                settled = ImageAnalysisRepository(session).settle_notification(
                    sample_id=item.sample_id,
                    owner=self.instance_id,
                    now=datetime.now(timezone.utc),
                    confirmed=confirmed,
                    max_attempts=self.max_attempts,
                )
            if confirmed and settled:
                published += 1
                LOGGER.info(
                    "图像结果通知发布成功: sample_id=%s, event_id=%s",
                    item.sample_id,
                    item.event_id,
                )
            else:
                failed += 1
        with self.session_factory.begin() as session:
            terminal = ImageAnalysisRepository(
                session
            ).claim_task_terminal_notifications(
                owner=self.instance_id,
                now=datetime.now(timezone.utc),
                lease_seconds=self.lease_seconds,
                limit=limit,
                max_attempts=self.max_attempts,
            )
        for item in terminal:
            confirmed = False
            try:
                self.publisher.publish(event_id=item.event_id, payload=item.payload)
                confirmed = True
            except Exception:
                LOGGER.warning("图像任务终态通知发布失败: task_id=%s", item.task_id)
            with self.session_factory.begin() as session:
                settled = ImageAnalysisRepository(
                    session
                ).settle_task_terminal_notification(
                    notification_id=item.notification_id,
                    owner=self.instance_id,
                    now=datetime.now(timezone.utc),
                    confirmed=confirmed,
                    max_attempts=self.max_attempts,
                )
            if confirmed and settled:
                published += 1
                LOGGER.info(
                    "图像任务终态通知发布成功: task_id=%s, event_id=%s",
                    item.task_id,
                    item.event_id,
                )
            else:
                failed += 1
        return DeliveryBatchResult(len(pending) + len(terminal), published, failed)


__all__ = ["DeliveryBatchResult", "ImageNotificationDelivery", "ImageResultPublisher"]
