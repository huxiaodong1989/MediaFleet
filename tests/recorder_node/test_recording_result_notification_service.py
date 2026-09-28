"""录制结果通知应用服务测试。"""

from __future__ import annotations

from typing import Any

import pytest

from services.recorder_node.application.result_notification_service import (
    RecordingCallbackSendResult,
    RecordingResultNotificationService,
)


class FakePayloadBuilder:
    """返回固定录制结果体，避免测试依赖真实媒体信息提取。"""

    async def build(self, *, task_id: str, task_status: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "status": task_status.get("status"),
            "summary": {"stream_id": task_status.get("stream_id")},
        }


class FakeNotifier:
    """记录 RabbitMQ 录制结果通知。"""

    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.messages: list[tuple[str, dict[str, Any]]] = []

    def publish(self, *, task_id: str, message_data: dict[str, Any]) -> bool:
        self.messages.append((task_id, message_data))
        return self.result


class FakeRecordingTaskResultService:
    """记录 recorder-node 任务结果服务调用。"""

    saved_results: list[tuple[str, dict[str, Any], str]] = []
    completed_results: list[tuple[str, dict[str, Any], str]] = []
    failed_results: list[tuple[str, str, dict[str, Any] | None, str]] = []

    def __init__(self, node_id: str = "recorder-local-1") -> None:
        self.node_id = node_id

    def mark_callback_result(
        self,
        *,
        task_id: str,
        callback_result: dict[str, Any],
    ) -> bool:
        self.saved_results.append((task_id, callback_result, self.node_id))
        return True

    def mark_completed(
        self,
        *,
        task_id: str,
        result_payload: dict[str, Any],
    ) -> bool:
        self.completed_results.append((task_id, result_payload, self.node_id))
        return True

    def mark_failed(
        self,
        *,
        task_id: str,
        error_message: str,
        result_payload: dict[str, Any] | None,
    ) -> bool:
        self.failed_results.append(
            (task_id, error_message, result_payload, self.node_id)
        )
        return True


@pytest.fixture(autouse=True)
def reset_fake_task_result_service():
    FakeRecordingTaskResultService.saved_results = []
    FakeRecordingTaskResultService.completed_results = []
    FakeRecordingTaskResultService.failed_results = []


@pytest.mark.asyncio
async def test_notify_publishes_mq_and_records_skipped_http_callback() -> None:
    """未配置 HTTP 回调时，仍发布 MQ 并把跳过结果写入国标任务表。"""

    notifier = FakeNotifier()
    service = RecordingResultNotificationService(
        payload_builder=FakePayloadBuilder(),
        result_notifier=notifier,
        task_result_service=FakeRecordingTaskResultService(),
        node_id="recorder-local-1",
    )

    ok = await service.notify(
        task_id="record-task-1",
        task_status={"status": "completed", "stream_id": "stream-1"},
    )

    assert ok is True
    assert notifier.messages[0][0] == "record-task-1"
    completed_task_id, result_payload, completed_by = (
        FakeRecordingTaskResultService.completed_results[0]
    )
    assert completed_task_id == "record-task-1"
    assert completed_by == "recorder-local-1"
    assert result_payload["status"] == "completed"
    assert result_payload["extra_info"]["stream_id"] == "stream-1"
    saved_task_id, callback_result, updated_by = (
        FakeRecordingTaskResultService.saved_results[0]
    )
    assert saved_task_id == "record-task-1"
    assert updated_by == "recorder-local-1"
    assert callback_result["skipped"] is True
    assert callback_result["mq_published"] is True
    assert callback_result["callback_payload"]["summary"]["stream_id"] == "stream-1"


@pytest.mark.asyncio
async def test_notify_retries_http_callback_three_times_and_records_failure() -> None:
    """HTTP 回调失败默认最多尝试 3 次，并记录每次尝试过程。"""

    attempts: list[str] = []

    def failing_callback_sender(
        callback_url: str,
        payload: dict[str, Any],
    ) -> RecordingCallbackSendResult:
        attempts.append(callback_url)
        return RecordingCallbackSendResult(
            success=False,
            status_code=500,
            response_text="mock failure",
        )

    service = RecordingResultNotificationService(
        payload_builder=FakePayloadBuilder(),
        result_notifier=FakeNotifier(),
        task_result_service=FakeRecordingTaskResultService(),
        callback_sender=failing_callback_sender,
        node_id="recorder-local-1",
        callback_retry_interval_seconds=0,
    )

    ok = await service.notify(
        task_id="record-task-2",
        task_status={
            "status": "completed",
            "stream_id": "stream-2",
            "callback_url": "https://rtc.example.com/record/callback?token=secret",
        },
    )

    assert ok is False
    assert len(attempts) == 3
    _, callback_result, _ = FakeRecordingTaskResultService.saved_results[0]
    assert callback_result["success"] is False
    assert callback_result["attempt_count"] == 3
    assert callback_result["max_retries"] == 3
    assert callback_result["callback_url"] == "https://rtc.example.com/record/callback"
    assert [item["attempt"] for item in callback_result["attempts"]] == [1, 2, 3]
    assert FakeRecordingTaskResultService.completed_results[0][0] == "record-task-2"


@pytest.mark.asyncio
async def test_notify_stops_retry_after_success() -> None:
    """回调成功后立即停止后续重试。"""

    attempts = 0

    async def successful_callback_sender(
        callback_url: str,
        payload: dict[str, Any],
    ) -> RecordingCallbackSendResult:
        nonlocal attempts
        attempts += 1
        return RecordingCallbackSendResult(success=True, status_code=200, response_text="ok")

    service = RecordingResultNotificationService(
        payload_builder=FakePayloadBuilder(),
        result_notifier=FakeNotifier(),
        task_result_service=FakeRecordingTaskResultService(),
        callback_sender=successful_callback_sender,
        node_id="recorder-local-1",
        callback_retry_interval_seconds=0,
    )

    ok = await service.notify(
        task_id="record-task-3",
        task_status={
            "status": "completed",
            "stream_id": "stream-3",
            "callback_url": "https://rtc.example.com/record/callback",
        },
    )

    assert ok is True
    assert attempts == 1
    _, callback_result, _ = FakeRecordingTaskResultService.saved_results[0]
    assert callback_result["success"] is True
    assert callback_result["attempt_count"] == 1


@pytest.mark.asyncio
async def test_notify_records_failed_task_status_before_callback() -> None:
    """录制失败通知会把 rwzt 写为 failed，并保留错误信息和结果摘要。"""

    service = RecordingResultNotificationService(
        payload_builder=FakePayloadBuilder(),
        result_notifier=FakeNotifier(),
        task_result_service=FakeRecordingTaskResultService(),
        node_id="recorder-local-1",
    )

    ok = await service.notify(
        task_id="record-task-failed",
        task_status={
            "status": "failed",
            "stream_id": "stream-failed",
            "error": "ZL停止录制失败",
        },
    )

    assert ok is True
    failed_task_id, error_message, result_payload, updated_by = (
        FakeRecordingTaskResultService.failed_results[0]
    )
    assert failed_task_id == "record-task-failed"
    assert error_message == "ZL停止录制失败"
    assert result_payload is not None
    assert result_payload["status"] == "failed"
    assert result_payload["extra_info"]["stream_id"] == "stream-failed"
    assert updated_by == "recorder-local-1"
