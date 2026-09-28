"""AI 评课使用的字幕、行为分析、模型和回调客户端。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from media_platform.common.redaction import sanitize_url


LOGGER = logging.getLogger(__name__)


def safe_url(value: str) -> str:
    return sanitize_url(value) or ""


class SubtitleClient:
    def __init__(self, *, timeout_seconds: float = 60, max_size_mb: int = 100):
        self.timeout_seconds = timeout_seconds
        self.max_size_bytes = max_size_mb * 1024 * 1024

    async def download(self, url: str) -> str:
        LOGGER.info("下载评课字幕: url=%s", safe_url(url))
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and int(length) > self.max_size_bytes:
                    raise RuntimeError("字幕文件超过大小限制")
                content = await response.text(encoding="utf-8")
        if not content.strip():
            raise RuntimeError("字幕文件为空")
        return content


class BehaviorAnalysisClient:
    def __init__(self, base_url: str | None, *, timeout_seconds: float = 30):
        self.base_url = (base_url or "").strip()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _compress_time_series(value: Any) -> Any:
        if not isinstance(value, list) or not value or not all(isinstance(item, dict) for item in value):
            return value
        if not all("value" in item for item in value):
            return value
        compressed: list[dict[str, Any]] = []
        for item in value:
            if not compressed or item.get("value") != compressed[-1].get("value"):
                compressed.append(item)
        return compressed

    async def get(self, *, classroom_id: str, tenant_id: int | None) -> dict[str, Any] | None:
        if not self.base_url:
            return None
        headers = {"Content-Type": "application/json", "User-Agent": "Content-Analysis-Service/1.0"}
        if tenant_id is not None:
            headers["Tenant-Id"] = str(tenant_id)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.base_url, params={"classroomId": classroom_id}, headers=headers) as response:
                    if response.status == 404:
                        return None
                    response.raise_for_status()
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOGGER.warning("行为分析接口暂不可用: classroom_id=%s", classroom_id, exc_info=True)
            return None
        if payload.get("code") not in (0, 200):
            LOGGER.warning("行为分析接口返回业务错误: classroom_id=%s", classroom_id)
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        return {key: self._compress_time_series(value) for key, value in data.items()}


class EvaluationLlmClient:
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float):
        if not api_key.strip():
            raise ValueError("CONTENT_ANALYSIS_OPENAI_API_KEY 未配置")
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _extract_json(response_text: str) -> dict[str, Any]:
        try:
            value = json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r"```json\s*({.*?})\s*```", response_text, re.DOTALL) or re.search(r"{.*}", response_text, re.DOTALL)
            if not match:
                raise RuntimeError("模型返回结果无法解析为JSON")
            value = json.loads(match.group(1) if match.lastindex else match.group(0))
        if not isinstance(value, dict):
            raise RuntimeError("模型返回结果不是JSON对象")
        return value

    async def analyze(self, *, model: str, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> tuple[dict[str, Any], dict[str, int]]:
        response = await asyncio.wait_for(
            self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                stream=False,
            ),
            timeout=self.timeout_seconds,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        return self._extract_json(text), {
            "prompt_tokens": usage.prompt_tokens if usage else 0,
            "completion_tokens": usage.completion_tokens if usage else 0,
            "total_tokens": usage.total_tokens if usage else 0,
        }


class ProgressCallbackClient:
    def __init__(self, *, timeout_seconds: float = 10):
        self.timeout_seconds = timeout_seconds

    async def notify(self, callback_url: str | None, payload: dict[str, Any], token: str | None = None) -> bool:
        if not callback_url:
            return False
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(callback_url, json=payload, headers=headers) as response:
                    return 200 <= response.status < 300
        except (aiohttp.ClientError, asyncio.TimeoutError):
            LOGGER.warning("评课进度回调失败: callback_url=%s", safe_url(callback_url), exc_info=True)
            return False


__all__ = [
    "BehaviorAnalysisClient",
    "EvaluationLlmClient",
    "ProgressCallbackClient",
    "SubtitleClient",
    "safe_url",
]
