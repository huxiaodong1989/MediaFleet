"""Opt-in one-call COS + Doubao vision smoke test; never runs by default."""

import os
from uuid import uuid4

import pytest


@pytest.mark.integration
def test_one_real_image_call_via_cos():
    if os.getenv("IMAGE_TEST_REAL_MODEL") != "1":
        pytest.skip("requires explicitly configured COS and vision model credentials")
    required = (
        "COS_SECRET_ID",
        "COS_SECRET_KEY",
        "COS_REGION",
        "COS_BUCKET",
        "IMAGE_ANALYSIS_OPENAI_API_KEY",
        "IMAGE_ANALYSIS_OPENAI_BASE_URL",
        "IMAGE_ANALYSIS_DEFAULT_MODEL",
        "IMAGE_TEST_VIDEO_URL",
    )
    assert all(os.getenv(name) for name in required)

    from qcloud_cos import CosConfig, CosS3Client
    from media_platform.contracts.image_analysis import BehaviorMetric
    from media_platform.infrastructure.storage.image_objects import CosImageObjectStore
    from services.image_analysis.prompt_defaults import load_bootstrap_prompt_bundle
    from services.image_analysis.video_frames import FfmpegFrameExtractor
    from services.image_analysis.vision_model import VisionModelClient

    frame = FfmpegFrameExtractor().extract(os.environ["IMAGE_TEST_VIDEO_URL"], 120_000)
    client = CosS3Client(
        CosConfig(
            Region=os.environ["COS_REGION"],
            SecretId=os.environ["COS_SECRET_ID"],
            SecretKey=os.environ["COS_SECRET_KEY"],
        )
    )
    bucket = os.environ["COS_BUCKET"]
    store = CosImageObjectStore(client, profile="local-validation", bucket=bucket)
    key = f"image-analysis/local-validation/{uuid4().hex}.jpg"
    uploaded = False
    try:
        ref = store.put_jpeg(key, frame.jpeg)
        uploaded = True
        url = store.signed_read_url(ref, lifetime_seconds=900)
        bundle = load_bootstrap_prompt_bundle(
            os.environ["IMAGE_ANALYSIS_DEFAULT_MODEL"]
        )
        metrics = VisionModelClient(
            api_key=os.environ["IMAGE_ANALYSIS_OPENAI_API_KEY"],
            base_url=os.environ["IMAGE_ANALYSIS_OPENAI_BASE_URL"],
            timeout_seconds=120,
        ).analyze(
            image_urls=(url,),
            bundle=bundle,
            requested_metrics=tuple(BehaviorMetric),
        )
        assert set(metrics) == {metric.value for metric in BehaviorMetric}
    finally:
        if uploaded:
            client.delete_object(Bucket=bucket, Key=key)
