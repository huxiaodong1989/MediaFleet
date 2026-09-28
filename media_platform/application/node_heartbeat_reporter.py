"""媒体节点心跳上报器。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import logging
from threading import Event, Thread
from typing import Any

import httpx


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeHeartbeatReporterConfig:
    """节点心跳上报配置。"""

    control_center_base_url: str
    api_key: str
    interval_seconds: float = 30.0
    timeout_seconds: float = 5.0
    enabled: bool = False
    endpoint_path: str = "/api/v1/nodes/heartbeat"

    def __post_init__(self) -> None:
        if self.enabled and not self.control_center_base_url.strip():
            raise ValueError("启用节点心跳时必须配置调用中心地址")
        if self.enabled and not self.api_key.strip():
            raise ValueError("启用节点心跳时必须配置内部API密钥")
        if self.interval_seconds <= 0:
            raise ValueError("节点心跳间隔必须大于0")
        if self.timeout_seconds <= 0:
            raise ValueError("节点心跳超时时间必须大于0")
        if not self.endpoint_path.startswith("/"):
            raise ValueError("节点心跳接口路径必须以 / 开头")

    @property
    def heartbeat_url(self) -> str:
        """返回完整心跳接口地址。"""

        return f"{self.control_center_base_url.rstrip('/')}{self.endpoint_path}"


class NodeHeartbeatReporter:
    """在服务生命周期内周期性向调用中心上报节点状态。"""

    def __init__(
        self,
        config: NodeHeartbeatReporterConfig,
        payload_factory: Callable[[], Mapping[str, Any]],
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.config = config
        self.payload_factory = payload_factory
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=self.config.timeout_seconds)
        )
        self._stop_event = Event()
        self._thread: Thread | None = None

    def report_once(self) -> None:
        """立即上报一次心跳，供后台循环和测试复用。"""

        payload = dict(self.payload_factory())
        node_code = str(payload.get("node_code") or "")
        node_type = str(payload.get("node_type") or "")
        with self.client_factory() as client:
            response = client.post(
                self.config.heartbeat_url,
                json=payload,
                headers={"X-API-Key": self.config.api_key},
            )
            response.raise_for_status()
        LOGGER.info(
            "媒体节点心跳上报成功: node_code=%s, node_type=%s",
            node_code,
            node_type,
        )

    def _run_loop(self) -> None:
        """后台循环：启动后立即上报一次，之后按固定间隔上报。"""

        while not self._stop_event.is_set():
            try:
                self.report_once()
            except Exception:
                LOGGER.exception("媒体节点心跳上报失败")
            self._stop_event.wait(self.config.interval_seconds)

    def start(self) -> None:
        """幂等启动心跳线程；未启用时只记录日志。"""

        if not self.config.enabled:
            LOGGER.info("媒体节点心跳上报未启用")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run_loop,
            name="media-node-heartbeat-reporter",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "媒体节点心跳上报已启动: url=%s, interval=%s",
            self.config.heartbeat_url,
            self.config.interval_seconds,
        )

    def close(self) -> None:
        """停止心跳线程并等待退出。"""

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(min(self.config.timeout_seconds + 1, 10))
        self._thread = None


__all__ = ["NodeHeartbeatReporter", "NodeHeartbeatReporterConfig"]
