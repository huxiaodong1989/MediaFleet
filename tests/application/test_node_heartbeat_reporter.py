"""节点心跳上报器测试。"""

from media_platform.application import (
    NodeHeartbeatReporter,
    NodeHeartbeatReporterConfig,
)


class FakeResponse:
    """模拟 httpx 响应对象。"""

    def raise_for_status(self):
        return None


class FakeClient:
    """记录心跳请求内容，不发起真实 HTTP。"""

    def __init__(self):
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, *, json, headers):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return FakeResponse()


def test_report_once_posts_payload_without_logging_secret():
    client = FakeClient()
    reporter = NodeHeartbeatReporter(
        NodeHeartbeatReporterConfig(
            control_center_base_url="http://control-center:8008/",
            api_key="internal-secret",
            enabled=True,
        ),
        lambda: {
            "node_code": "worker-1",
            "node_type": "WORKER",
            "capacity": {"processing_tasks": 0},
        },
        client_factory=lambda: client,
    )

    reporter.report_once()

    assert client.posts == [
        {
            "url": "http://control-center:8008/api/v1/nodes/heartbeat",
            "json": {
                "node_code": "worker-1",
                "node_type": "WORKER",
                "capacity": {"processing_tasks": 0},
            },
            "headers": {"X-API-Key": "internal-secret"},
        }
    ]


def test_disabled_reporter_does_not_start_thread():
    reporter = NodeHeartbeatReporter(
        NodeHeartbeatReporterConfig(
            control_center_base_url="",
            api_key="",
            enabled=False,
        ),
        lambda: {},
    )

    reporter.start()

    assert reporter._thread is None
