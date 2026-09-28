"""recorder-node 真实依赖就绪检查测试。"""

import shutil

from services.recorder_node.application.dependency_readiness import (
    RecorderDependencyReadinessProbe,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def get(self, url, *, params):
        self.requests.append((url, params))
        return FakeResponse(self.payload)


def test_readiness_requires_zlm_writable_record_root_and_disk_space(tmp_path):
    client = FakeClient({"code": 0, "data": []})
    probe = RecorderDependencyReadinessProbe(
        zlm_api_url="http://127.0.0.1:8080",
        zlm_secret="secret-value",
        record_root=str(tmp_path),
        min_free_disk_gb=0,
        client_factory=lambda: client,
    )

    result = probe.check()

    assert result.ready is True
    assert result.details["zlm_api_ready"] is True
    assert result.details["record_root_ready"] is True
    assert result.details["errors"] == []
    assert client.requests[0][1] == {"secret": "secret-value"}
    assert list(tmp_path.iterdir()) == []


def test_readiness_rejects_low_disk_without_exposing_secret(tmp_path, monkeypatch):
    usage = shutil.disk_usage(tmp_path)
    client = FakeClient({"code": 0})
    probe = RecorderDependencyReadinessProbe(
        zlm_api_url="http://127.0.0.1:8080",
        zlm_secret="never-return-this-secret",
        record_root=str(tmp_path),
        min_free_disk_gb=(usage.free / (1024**3)) + 1,
        client_factory=lambda: client,
    )

    result = probe.check()

    assert result.ready is False
    assert "录像磁盘可用空间低于准入阈值" in result.details["errors"]
    assert "never-return-this-secret" not in str(result.details)
