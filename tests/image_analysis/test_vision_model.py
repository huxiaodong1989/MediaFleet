import json

import httpx
import pytest

from media_platform.contracts.image_analysis import BehaviorMetric
from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
from services.image_analysis.vision_model import (
    VisionModelClient,
    VisionModelError,
    prompt_metrics,
)


def test_vision_model_uses_one_prompt_and_two_images(caplog):
    caplog.set_level("INFO")
    bundle = load_bootstrap_prompt_bundle("test-vision")
    expected = {metric.value: 0 for metric in prompt_metrics(bundle.prompt)}
    sent = []

    def respond(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(expected)}}]}
        )

    client = VisionModelClient(
        api_key="secret",
        base_url="https://vision.test/v1",
        transport=httpx.MockTransport(respond),
    )
    result = client.analyze(
        image_urls=("https://images.test/a", "https://images.test/b"), bundle=bundle
    )
    assert result == expected
    assert len(sent) == 1
    content = sent[0]["messages"][0]["content"]
    assert [item["type"] for item in content] == ["text", "image_url", "image_url"]
    assert content[0]["text"] == bundle.prompt
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "视觉模型请求完成" in messages
    assert "image_count=2" in messages
    assert "https://images.test/" not in messages


def test_vision_model_rejects_extra_key_without_leaking_signed_url():
    bundle = load_bootstrap_prompt_bundle("test-vision")
    expected = {metric.value: 0 for metric in prompt_metrics(bundle.prompt)}
    expected["NOT_REQUESTED"] = 1
    client = VisionModelClient(
        api_key="secret",
        base_url="https://vision.test/v1",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(expected)}}]}
            )
        ),
    )
    with pytest.raises(VisionModelError) as error:
        client.analyze(
            image_urls=("https://images.test/a?signature=secret",), bundle=bundle
        )
    assert "secret" not in str(error.value)


def test_vision_model_can_return_only_requested_subset_from_single_full_prompt():
    bundle = load_bootstrap_prompt_bundle("test-vision")
    expected = {metric.value: 0 for metric in prompt_metrics(bundle.prompt)}
    client = VisionModelClient(
        api_key="secret",
        base_url="https://vision.test/v1",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(expected)}}]},
            )
        ),
    )
    assert client.analyze(
        image_urls=("https://images.test/a",),
        bundle=bundle,
        requested_metrics=(BehaviorMetric.HEAD_UP, BehaviorMetric.TEACHER_NUM),
    ) == {"HEAD_UP": 0, "TEACHER_NUM": 0}
    with pytest.raises(ValueError, match="请求指标"):
        client.analyze(
            image_urls=("https://images.test/a",),
            bundle=bundle,
            requested_metrics=("DISCUSS",),
        )
