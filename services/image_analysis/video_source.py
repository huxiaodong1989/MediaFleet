"""HTTP录像探测及有界下载；DNS重绑定/FFmpeg出站仍需容器网络隔离。"""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
from typing import Callable
from urllib.parse import urlsplit

import httpx


_CONTENT_RANGE = re.compile(r"^bytes 0-0/(\d+)$")


class VideoSourceError(RuntimeError):
    """仅保存稳定错误码，绝不暴露带签名的视频URL。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RangeProbe:
    content_length: int | None
    etag: str | None
    range_supported: bool


def _system_resolver(host: str) -> tuple[str, ...]:
    return tuple(sorted({item[4][0] for item in socket.getaddrinfo(host, None)}))


class HttpVideoSource:
    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...],
        allowed_networks: tuple[str, ...],
        allowed_ports: tuple[int, ...] = (80, 443),
        max_download_bytes: int,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
        resolver: Callable[[str], tuple[str, ...]] = _system_resolver,
    ) -> None:
        if (
            not allowed_hosts
            or not allowed_networks
            or not allowed_ports
            or any(
                not isinstance(port, int)
                or isinstance(port, bool)
                or port < 1
                or port > 65535
                for port in allowed_ports
            )
            or max_download_bytes < 1
            or timeout_seconds <= 0
        ):
            raise ValueError("HTTP视频源访问策略不完整")
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self.allowed_ports = frozenset(allowed_ports)
        self.networks = tuple(ipaddress.ip_network(item) for item in allowed_networks)
        self.max_download_bytes = max_download_bytes
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.resolver = resolver

    def _validate(self, source: str) -> None:
        try:
            parsed = urlsplit(source)
            host = parsed.hostname
            if (
                parsed.scheme not in {"http", "https"}
                or not host
                or host.lower() not in self.allowed_hosts
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or "\\" in source
                or any(char.isspace() or ord(char) < 32 for char in source)
            ):
                raise ValueError
            if (
                parsed.port or (443 if parsed.scheme == "https" else 80)
            ) not in self.allowed_ports:
                raise ValueError
            addresses = self.resolver(host)
            if not addresses or any(
                not any(
                    ipaddress.ip_address(address) in network
                    for network in self.networks
                )
                for address in addresses
            ):
                raise ValueError
        except (ValueError, OSError):
            raise VideoSourceError("source_not_allowed") from None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        )

    @staticmethod
    def _reject_status(response: httpx.Response) -> None:
        if response.status_code in {301, 302, 303, 307, 308}:
            raise VideoSourceError("source_redirect_forbidden")
        if response.status_code in {401, 403}:
            raise VideoSourceError("source_access_denied")
        if response.status_code == 404:
            raise VideoSourceError("source_missing")
        if response.status_code == 429:
            raise VideoSourceError("source_rate_limited")
        if response.status_code >= 500:
            raise VideoSourceError("source_transient_error")

    def probe_range(self, source: str) -> RangeProbe:
        """只读取Range响应头；服务端忽略Range返回200时不读取整个文件。"""
        self._validate(source)
        try:
            with self._client() as client:
                with client.stream(
                    "GET", source, headers={"Range": "bytes=0-0"}
                ) as response:
                    self._reject_status(response)
                    if response.status_code == 200:
                        size_header = response.headers.get("Content-Length")
                        size = (
                            int(size_header)
                            if size_header and size_header.isdecimal()
                            else None
                        )
                        return RangeProbe(size, response.headers.get("ETag"), False)
                    if response.status_code != 206:
                        raise VideoSourceError("range_probe_failed")
                    match = _CONTENT_RANGE.fullmatch(
                        response.headers.get("Content-Range", "")
                    )
                    if match is None or int(match.group(1)) < 1:
                        raise VideoSourceError("range_response_invalid")
                    return RangeProbe(
                        int(match.group(1)), response.headers.get("ETag"), True
                    )
        except httpx.HTTPError:
            raise VideoSourceError("source_transient_error") from None

    def download(self, source: str, *, target: Path, probe: RangeProbe) -> Path:
        """下载到同目录part后原子创建目标；失败仅清理本次part。"""
        self._validate(source)
        if (
            not target.parent.is_dir()
            or target.exists()
            or not isinstance(probe, RangeProbe)
        ):
            raise ValueError("完整下载目标或探测快照无效")
        if (
            probe.content_length is not None
            and probe.content_length > self.max_download_bytes
        ):
            raise VideoSourceError("download_size_limit")
        part_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="image-video-",
                suffix=".part",
                dir=target.parent,
                delete=False,
            ) as output:
                part_path = Path(output.name)
                with self._client() as client:
                    with client.stream("GET", source) as response:
                        self._reject_status(response)
                        if response.status_code != 200:
                            raise VideoSourceError("download_response_invalid")
                        if probe.etag and response.headers.get("ETag") != probe.etag:
                            raise VideoSourceError("source_changed")
                        size_header = response.headers.get("Content-Length")
                        advertised_size = None
                        if size_header and size_header.isdecimal():
                            advertised_size = int(size_header)
                            if advertised_size > self.max_download_bytes:
                                raise VideoSourceError("download_size_limit")
                            if (
                                probe.content_length is not None
                                and advertised_size != probe.content_length
                            ):
                                raise VideoSourceError("source_changed")
                        total = 0
                        for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                            total += len(chunk)
                            if total > self.max_download_bytes:
                                raise VideoSourceError("download_size_limit")
                            output.write(chunk)
                        if (
                            total < 1
                            or (
                                advertised_size is not None and total != advertised_size
                            )
                            or (
                                probe.content_length is not None
                                and total != probe.content_length
                            )
                        ):
                            raise VideoSourceError("download_length_mismatch")
            # 目标可能在下载期间由另一执行者创建；不可覆盖已有检查点文件。
            os.link(part_path, target)
            return target
        except httpx.HTTPError:
            raise VideoSourceError("source_transient_error") from None
        finally:
            if part_path is not None and part_path.exists():
                part_path.unlink()


__all__ = ["HttpVideoSource", "RangeProbe", "VideoSourceError"]
