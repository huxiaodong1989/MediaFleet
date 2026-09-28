"""录制节点后处理队列 API 测试。"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.recorder_node.application.runtime import RecorderNodeRuntime
from services.recorder_node.commands import RecorderCommandRegistry
from services.recorder_node.main import create_recorder_node_app


class FakeConsumer:
    """避免测试启动真实 RabbitMQ 命令消费者。"""

    def start_consuming(self):
        raise AssertionError("测试不应启动真实消费")

    def stop(self):
        pass

    def close(self):
        pass


class FakePostProcessingManager:
    """提供可预测的后处理队列状态。"""

    async def get_stats(self):
        return {
            "total_tasks": 3,
            "completed_tasks": 1,
            "failed_tasks": 0,
            "current_processing": 1,
            "queue_size": 1,
        }

    async def get_task_status(self, task_id: str):
        if task_id == "task-1":
            return {
                "task_id": task_id,
                "current_stage": "audio_extract",
                "errors": [],
            }
        return None


def _app(*, recovery_service=None):
    runtime = RecorderNodeRuntime(
        consumer=FakeConsumer(),
        registry=RecorderCommandRegistry(),
        node_id="recorder-api-test",
        command_consumer_enabled=False,
        post_processing_recovery_service=recovery_service,
    )
    return create_recorder_node_app(lambda: runtime)


def test_post_processing_stats_endpoint(monkeypatch):
    """后处理统计接口应挂在 recorder-node 自己的 API 下。"""

    import services.recorder_node.api.post_processing as post_processing_api

    monkeypatch.setattr(
        post_processing_api,
        "get_post_processing_manager",
        lambda: FakePostProcessingManager(),
    )

    with TestClient(_app()) as client:
        response = client.get("/api/v1/post-processing/stats")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["current_processing"] == 1
    assert payload["data"]["queue_size"] == 1


def test_post_processing_task_endpoint(monkeypatch):
    """后处理任务查询接口只查询本节点队列中的任务。"""

    import services.recorder_node.api.post_processing as post_processing_api

    monkeypatch.setattr(
        post_processing_api,
        "get_post_processing_manager",
        lambda: FakePostProcessingManager(),
    )

    with TestClient(_app()) as client:
        found_response = client.get("/api/v1/post-processing/tasks/task-1")
        missing_response = client.get("/api/v1/post-processing/tasks/missing")

    assert found_response.status_code == 200
    assert found_response.json()["data"]["current_stage"] == "audio_extract"
    assert missing_response.status_code == 200
    assert missing_response.json()["code"] == 404
    assert missing_response.json()["data"] is None


def test_post_processing_config_exposes_idle_strategy(monkeypatch):
    """本机配置接口应展示闲时策略的阈值和忙碌并发。"""

    import services.recorder_node.api.post_processing as post_processing_api

    monkeypatch.setattr(
        post_processing_api,
        "get_settings",
        lambda: SimpleNamespace(
            post_processing=SimpleNamespace(
                max_workers=3,
                media_concurrency=2,
                idle_strategy_enabled=True,
                busy_recording_threshold=20,
                busy_media_concurrency=1,
                busy_media_percent=50,
                idle_strategy_poll_interval_seconds=1,
                db_save_max_attempts=5,
                db_save_retry_delay_seconds=5,
                failed_auto_retry_enabled=True,
                failed_auto_retry_max_attempts=3,
                failed_auto_retry_initial_delay_seconds=60,
                failed_auto_retry_max_delay_seconds=900,
                failed_auto_retry_scan_interval_seconds=30,
                priority_high=10,
                priority_normal=5,
                priority_low=1,
                timeout_video_info=60,
                timeout_audio_extract=300,
                timeout_cover_extract=120,
                timeout_video_upload=600,
                timeout_audio_upload=300,
                timeout_cover_upload=60,
            )
        ),
    )
    monkeypatch.setattr(
        post_processing_api,
        "get_post_processing_manager",
        lambda: SimpleNamespace(max_recordings=100),
    )

    with TestClient(_app()) as client:
        response = client.get("/api/v1/post-processing/config")

    assert response.status_code == 200
    idle_strategy = response.json()["data"]["idle_strategy"]
    assert idle_strategy == {
        "enabled": True,
        "busy_recording_threshold": 20,
        "busy_media_concurrency": 1,
        "busy_media_percent": 50,
        "max_recordings": 100,
        "poll_interval_seconds": 1,
    }
    assert response.json()["data"]["db_save_retry"] == {
        "max_attempts": 5,
        "retry_delay_seconds": 5,
    }
    assert response.json()["data"]["failed_auto_retry"] == {
        "enabled": True,
        "max_attempts": 3,
        "initial_delay_seconds": 60,
        "max_delay_seconds": 900,
        "scan_interval_seconds": 30,
    }


def test_post_processing_retry_endpoint_uses_runtime_recovery_service():
    class FakeRecoveryService:
        def __init__(self):
            self.calls = []

        async def recover_pending_post_processing(self):
            return 0

        async def retry_failed_post_processing(self, task_id):
            self.calls.append(task_id)
            return True

    recovery_service = FakeRecoveryService()
    with TestClient(_app(recovery_service=recovery_service)) as client:
        response = client.post(
            "/api/v1/post-processing/tasks/failed-task/retry"
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "task_id": "failed-task",
        "accepted": True,
    }
    assert recovery_service.calls == ["failed-task"]
