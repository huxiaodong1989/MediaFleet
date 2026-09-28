"""录制节点真实依赖就绪检查。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tempfile
from typing import Any

import httpx


@dataclass(frozen=True)
class RecorderDependencyReadiness:
    """一次 ZL、录像目录和磁盘余量联合检查结果。"""

    ready: bool
    details: dict[str, Any]


class RecorderDependencyReadinessProbe:
    """在 recorder-node 本机检查调用中心无法观察的真实依赖。

    调用中心不访问 ZL 或录像目录，只接受 recorder-node 的检查事实。探测不会把
    ZL Secret、完整异常请求或敏感 URL 写入返回明细。
    """

    def __init__(
        self,
        *,
        zlm_api_url: str,
        zlm_secret: str,
        record_root: str | None,
        min_free_disk_gb: float,
        timeout_seconds: float = 3.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.zlm_api_url = zlm_api_url.rstrip("/")
        self.zlm_secret = zlm_secret
        self.record_root = record_root
        self.min_free_disk_gb = min_free_disk_gb
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=self.timeout_seconds)
        )

    def _check_zlm(self) -> tuple[bool, str | None]:
        if not self.zlm_api_url or not self.zlm_secret:
            return False, "ZLMediaKit API地址或Secret未配置"
        try:
            with self.client_factory() as client:
                response = client.get(
                    f"{self.zlm_api_url}/index/api/getServerConfig",
                    params={"secret": self.zlm_secret},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return False, f"ZLMediaKit API不可用: {type(exc).__name__}"
        if not isinstance(payload, dict) or payload.get("code") != 0:
            return False, "ZLMediaKit API返回非成功状态"
        return True, None

    def _check_record_root(self) -> tuple[bool, str | None, dict[str, Any]]:
        if not self.record_root:
            return False, "录像根目录未配置", {}
        root = Path(self.record_root)
        if not root.is_dir():
            return False, "录像根目录不存在或不是目录", {}

        probe_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".recorder-readiness-",
                dir=root,
                delete=False,
            ) as probe_file:
                probe_file.write(b"ready")
                probe_file.flush()
                os.fsync(probe_file.fileno())
                probe_path = probe_file.name
            Path(probe_path).unlink()
            probe_path = None
        except OSError as exc:
            return False, f"录像根目录不可写: {type(exc).__name__}", {}
        finally:
            if probe_path:
                try:
                    Path(probe_path).unlink(missing_ok=True)
                except OSError:
                    pass

        try:
            usage = shutil.disk_usage(root)
        except OSError as exc:
            return False, f"无法读取录像磁盘容量: {type(exc).__name__}", {}
        free_gb = round(usage.free / (1024**3), 2)
        usage_percent = (
            round((usage.used / usage.total) * 100, 2) if usage.total else 100.0
        )
        metrics = {
            "disk_free_gb": free_gb,
            "disk_usage_percent": usage_percent,
            "min_free_disk_gb": self.min_free_disk_gb,
        }
        if free_gb < self.min_free_disk_gb:
            return False, "录像磁盘可用空间低于准入阈值", metrics
        return True, None, metrics

    def check(self) -> RecorderDependencyReadiness:
        """执行全部探测，任何一项失败都不允许新绑定进入该录制单元。"""

        zlm_ready, zlm_error = self._check_zlm()
        record_root_ready, record_root_error, disk_metrics = self._check_record_root()
        errors = [error for error in (zlm_error, record_root_error) if error]
        return RecorderDependencyReadiness(
            ready=zlm_ready and record_root_ready,
            details={
                "zlm_api_ready": zlm_ready,
                "record_root_ready": record_root_ready,
                **disk_metrics,
                "errors": errors,
            },
        )


__all__ = ["RecorderDependencyReadiness", "RecorderDependencyReadinessProbe"]
