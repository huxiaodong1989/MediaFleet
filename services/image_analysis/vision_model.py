"""单份课堂提示词、多图请求与严格结构化结果验证。"""

from __future__ import annotations

import logging
import re
from time import perf_counter

import httpx

from media_platform.contracts.image_analysis import (
    BehaviorMetric,
    ImagePromptBundleContent,
    validate_metric_result,
)


LOGGER = logging.getLogger(__name__)


class VisionModelError(RuntimeError):
    """不携带API密钥、签名图片URL或模型原始响应。"""


def prompt_metrics(prompt: str) -> tuple[BehaviorMetric, ...]:
    names = re.findall(r"(?m)^\s*-\s*([A-Z][A-Z_]+)\s*:", prompt)
    if not names or len(names) != len(set(names)):
        raise ValueError("图像提示词指标行缺失或重复")
    try:
        return tuple(BehaviorMetric(name) for name in names)
    except ValueError:
        raise ValueError("图像提示词包含未知指标") from None


class VisionModelClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key or not base_url or timeout_seconds <= 0:
            raise ValueError("视觉模型连接配置无效")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def analyze(
        self,
        *,
        image_urls: tuple[str, ...],
        bundle: ImagePromptBundleContent,
        requested_metrics: tuple[BehaviorMetric, ...] | None = None,
    ) -> dict[str, int]:
        if not image_urls or any(
            not url.startswith(("https://", "http://")) for url in image_urls
        ):
            raise ValueError("多图模型需要可读取的HTTP图片地址")
        metrics = prompt_metrics(bundle.prompt)
        selected = requested_metrics or metrics
        if not set(selected).issubset(metrics):
            raise ValueError("请求指标不在锁定的图像提示词中")
        message = [{"type": "text", "text": bundle.prompt}]
        message.extend(
            {"type": "image_url", "image_url": {"url": url}} for url in image_urls
        )
        started = perf_counter()
        try:
            with httpx.Client(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                response = client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.api_key},
                    json={
                        "model": bundle.model,
                        "messages": [{"role": "user", "content": message}],
                        "temperature": bundle.temperature,
                        "max_tokens": bundle.max_tokens,
                    },
                )
            if response.status_code != 200:
                raise VisionModelError(f"vision_http_{response.status_code}")
            content = response.json()["choices"][0]["message"]["content"]
            complete = validate_metric_result(content, metrics)
            result = {metric.value: complete[metric.value] for metric in selected}
            LOGGER.info(
                "视觉模型请求完成: model=%s, image_count=%s, metric_count=%s, duration_ms=%s",
                bundle.model,
                len(image_urls),
                len(result),
                round((perf_counter() - started) * 1000),
            )
            return result
        except VisionModelError as error:
            LOGGER.warning(
                "视觉模型请求失败: model=%s, image_count=%s, error_type=%s, duration_ms=%s",
                bundle.model,
                len(image_urls),
                str(error),
                round((perf_counter() - started) * 1000),
            )
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            LOGGER.warning(
                "视觉模型请求失败: model=%s, image_count=%s, error_type=vision_response_invalid_or_unavailable, duration_ms=%s",
                bundle.model,
                len(image_urls),
                round((perf_counter() - started) * 1000),
            )
            raise VisionModelError("vision_response_invalid_or_unavailable") from exc


__all__ = ["VisionModelClient", "VisionModelError", "prompt_metrics"]
