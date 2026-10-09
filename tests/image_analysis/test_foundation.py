"""第一批只验证无 IO 策略与诚实的存活/就绪边界。"""

import logging

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from services.image_analysis.main import create_image_analysis_app
from services.image_analysis import main as image_analysis_main
from services.image_analysis.read_policy import ReadAction, SeekFailure, VideoReadPolicy
from services.image_analysis.sampling import sample_offsets_ms
from media_platform.infrastructure.observability.logger import (
    configure_third_party_loggers,
)


def test_entrypoint_is_live_but_not_ready_and_has_no_business_routes():
    with TestClient(create_image_analysis_app()) as client:
        assert client.get("/health").json()["status"] == "up"
        assert client.get("/api/v1/service-info").json()["role"] == "image_analysis"
        readiness = client.get("/ready")
        assert readiness.status_code == 503
        assert readiness.json() == {
            "status": "not_ready",
            "reason": "consumer_disabled",
            "consumer_enabled": False,
        }
        paths = client.get("/api/openapi.json").json()["paths"]
        assert set(paths) == {"/", "/health", "/api/v1/service-info", "/ready"}
        assert all(set(path) == {"get"} for path in paths.values())


def test_run_configures_business_logging_like_other_services(monkeypatch):
    basic_config_calls = []
    third_party_calls = []
    uvicorn_calls = []

    monkeypatch.setenv("SERVICE_LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(
        image_analysis_main.logging,
        "basicConfig",
        lambda **kwargs: basic_config_calls.append(kwargs),
    )
    monkeypatch.setattr(
        image_analysis_main,
        "configure_third_party_loggers",
        lambda: third_party_calls.append(True),
    )
    monkeypatch.setattr(
        image_analysis_main.uvicorn,
        "run",
        lambda *args, **kwargs: uvicorn_calls.append((args, kwargs)),
    )

    image_analysis_main.run()

    assert basic_config_calls == [
        {
            "level": "DEBUG",
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        }
    ]
    assert third_party_calls == [True]
    assert uvicorn_calls[0][1]["log_level"] == "debug"
    assert logging.getLevelName(basic_config_calls[0]["level"]) == logging.DEBUG


def test_persistent_log_handler_writes_to_mounted_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVICE_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("SERVICE_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("SERVICE_LOG_BACKUP_COUNT", "2")
    root = logging.getLogger()
    handler = image_analysis_main._add_persistent_log_handler()
    try:
        assert handler is not None
        record = logging.LogRecord(
            "services.image_analysis.test",
            logging.INFO,
            __file__,
            1,
            "图像任务持久日志",
            (),
            None,
        )
        handler.emit(record)
        handler.flush()
        assert "图像任务持久日志" in (tmp_path / "image-analysis.log").read_text(
            encoding="utf-8"
        )
    finally:
        if handler is not None:
            root.removeHandler(handler)
            handler.close()


def test_third_party_logging_suppresses_cos_object_urls():
    logger = logging.getLogger("qcloud_cos.cos_client")
    previous_level = logger.level
    try:
        logger.setLevel(logging.NOTSET)
        configure_third_party_loggers()
        assert logger.getEffectiveLevel() == logging.WARNING
    finally:
        logger.setLevel(previous_level)


def test_readiness_follows_enabled_consumer_connection(monkeypatch):
    monkeypatch.setenv("IMAGE_ANALYSIS_CONSUMER_ENABLED", "true")

    class Connection:
        is_closed = False

    class Consumer:
        _connection = Connection()

    class Runtime:
        consumer = Consumer()
        started = False
        closed = False

        async def start(self):
            self.started = True

        async def close(self):
            self.closed = True

    runtime = Runtime()
    with TestClient(create_image_analysis_app(runtime_factory=lambda: runtime)) as client:
        assert runtime.started
        assert client.get("/ready").json() == {
            "status": "ready",
            "reason": "ok",
            "consumer_enabled": True,
        }
        runtime.consumer._connection.is_closed = True
        assert client.get("/ready").status_code == 503
    assert runtime.closed


@pytest.mark.parametrize(
    ("retries_used", "expected"),
    [
        (0, ReadAction.RETRY_SEEK),
        (1, ReadAction.RETRY_SEEK),
        (2, ReadAction.DOWNLOAD),
        (3, ReadAction.DOWNLOAD),
    ],
)
def test_transient_failure_has_video_level_budget(retries_used, expected):
    assert (
        VideoReadPolicy().after_seek_failure(
            SeekFailure.TRANSIENT, retries_used=retries_used
        )
        == expected
    )


@pytest.mark.parametrize("failure", list(SeekFailure))
def test_non_retryable_errors_and_no_retry_setting(failure):
    expected = (
        ReadAction.DOWNLOAD
        if failure in {SeekFailure.TRANSIENT, SeekFailure.SEEK_UNSUPPORTED}
        else ReadAction.FAIL
    )
    assert (
        VideoReadPolicy(seek_retry_limit=0).after_seek_failure(failure, retries_used=0)
        == expected
    )


def test_unsupported_seek_skips_retry_budget():
    assert (
        VideoReadPolicy().after_seek_failure(
            SeekFailure.SEEK_UNSUPPORTED, retries_used=0
        )
        == ReadAction.DOWNLOAD
    )


@pytest.mark.parametrize("value", [-1, True, 1.5, "2", None])
def test_invalid_persisted_retry_counter_is_rejected(value):
    with pytest.raises(ValueError):
        VideoReadPolicy().after_seek_failure(SeekFailure.TRANSIENT, retries_used=value)


@pytest.mark.parametrize("value", [-1, 6, True, "2", 1.5])
def test_policy_configuration_is_strict(value):
    with pytest.raises(ValidationError):
        VideoReadPolicy(seek_retry_limit=value)


def test_invalid_failure_does_not_silently_trigger_download():
    with pytest.raises(ValueError):
        VideoReadPolicy().after_seek_failure("unknown", retries_used=0)


def test_benchmark_sample_has_52_frames():
    offsets = sample_offsets_ms(6_239_400)
    assert isinstance(offsets, range)
    assert len(offsets) == 52
    assert offsets[0] == 0
    assert offsets[-1] == 6_120_000


@pytest.mark.parametrize(
    ("duration", "start", "expected"),
    [
        (0, 0, []),
        (1, 0, [0]),
        (240_000, 0, [0, 120_000]),
        (240_001, 0, [0, 120_000, 240_000]),
        (240_000, 120_000, [120_000]),
        (10, 20, []),
    ],
)
def test_sampling_boundaries(duration, start, expected):
    assert list(sample_offsets_ms(duration, start_ms=start)) == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_ms": -1},
        {"duration_ms": True},
        {"duration_ms": 1.5},
        {"duration_ms": "10"},
        {"duration_ms": 1, "interval_ms": 0},
        {"duration_ms": 1, "interval_ms": False},
        {"duration_ms": 1, "start_ms": -1},
    ],
)
def test_invalid_sampling_arguments(kwargs):
    with pytest.raises(ValueError):
        sample_offsets_ms(**kwargs)
