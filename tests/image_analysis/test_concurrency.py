from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.image_analysis.concurrency import (
    ENV_FIELDS,
    ConcurrencyStage,
    ImageConcurrencySettings,
    InstanceConcurrency,
)
from services.image_analysis.main import create_image_analysis_app


def test_defaults_and_64_classroom_configuration_are_independent():
    defaults = ImageConcurrencySettings.from_env({})
    configured = ImageConcurrencySettings.from_env(
        {
            "IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS": "64",
            "IMAGE_ANALYSIS_FFMPEG_CONCURRENCY": "16",
            "IMAGE_ANALYSIS_MODEL_CONCURRENCY": "8",
        }
    )
    assert defaults.max_concurrent_classrooms == 4
    assert configured.consumer_prefetch == 64
    assert configured.ffmpeg_concurrency == 16
    assert configured.model_concurrency == 8
    assert configured.ffmpeg_threads == 1
    assert configured.download_concurrency == 1


@pytest.mark.parametrize("env_name", list(ENV_FIELDS))
@pytest.mark.parametrize(
    "value", ["", "0", "-1", "true", "2.5", "1e2", "NaN", "unlimited"]
)
def test_invalid_env_is_not_silently_replaced_by_default(env_name, value):
    with pytest.raises(ValueError, match=env_name):
        ImageConcurrencySettings.from_env({env_name: value})


@pytest.mark.parametrize("value", [True, 0, -1, 2.5, "64"])
def test_direct_settings_are_strict(value):
    with pytest.raises(ValidationError):
        ImageConcurrencySettings(max_concurrent_classrooms=value)


def test_64_slots_then_rejection_and_cleanup():
    limiter = InstanceConcurrency(
        ImageConcurrencySettings(max_concurrent_classrooms=64)
    )
    with ExitStack() as stack:
        assert all(
            stack.enter_context(limiter.try_slot(ConcurrencyStage.CLASSROOM))
            for _ in range(64)
        )
        with limiter.try_slot(ConcurrencyStage.CLASSROOM) as accepted:
            assert not accepted
        assert limiter.snapshot()["classroom"] == {"limit": 64, "active": 64}
        # 课堂满不代表FFmpeg或模型额度也满。
        with limiter.try_slot(ConcurrencyStage.MODEL) as accepted:
            assert accepted
    assert all(stage["active"] == 0 for stage in limiter.snapshot().values())


def test_exception_and_cancellation_release_slot():
    limiter = InstanceConcurrency(ImageConcurrencySettings(max_concurrent_classrooms=1))
    for failure in (RuntimeError, KeyboardInterrupt, CancelledError):
        with pytest.raises(failure):
            with limiter.try_slot(ConcurrencyStage.CLASSROOM) as accepted:
                assert accepted
                raise failure()
        assert limiter.snapshot()["classroom"]["active"] == 0


def test_stage_limits_shared_across_concurrent_threads():
    limiter = InstanceConcurrency(ImageConcurrencySettings(ffmpeg_concurrency=3))
    barrier = Barrier(16)

    def reserve(_):
        with limiter.try_slot(ConcurrencyStage.FFMPEG) as accepted:
            barrier.wait(timeout=10)
            observed = limiter.snapshot()["ffmpeg"]["active"]
            barrier.wait(timeout=10)
            return accepted, observed

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(executor.map(reserve, range(16)))
    assert sum(accepted for accepted, _ in results) == 3
    assert {observed for _, observed in results} == {3}
    assert limiter.snapshot()["ffmpeg"]["active"] == 0


def test_snapshot_is_detached_and_unknown_stage_rejected():
    limiter = InstanceConcurrency(ImageConcurrencySettings())
    snapshot = limiter.snapshot()
    snapshot["classroom"]["limit"] = 999
    assert limiter.snapshot()["classroom"]["limit"] == 4
    with pytest.raises(ValueError):
        with limiter.try_slot("unknown"):
            pytest.fail("unknown stage entered")


def test_app_reads_env_on_startup_without_claiming_readiness(monkeypatch):
    app = create_image_analysis_app()
    monkeypatch.setenv("IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS", "64")
    with TestClient(app) as client:
        assert app.state.image_concurrency_settings.max_concurrent_classrooms == 64
        assert app.state.image_concurrency.snapshot()["classroom"]["limit"] == 64
        assert client.get("/ready").status_code == 503
        assert not client.get("/ready").json()["consumer_enabled"]


def test_invalid_env_fails_startup(monkeypatch):
    monkeypatch.setenv("IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS", "0")
    with pytest.raises(ValueError, match="IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS"):
        with TestClient(create_image_analysis_app()):
            pytest.fail("invalid capacity started")


def test_injected_settings_do_not_read_environment(monkeypatch):
    monkeypatch.setenv("IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS", "invalid")
    app = create_image_analysis_app(
        ImageConcurrencySettings(max_concurrent_classrooms=2)
    )
    with TestClient(app):
        assert app.state.image_concurrency_settings.max_concurrent_classrooms == 2
