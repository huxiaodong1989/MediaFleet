from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from services.image_analysis.concurrency import (
    ImageConcurrencySettings,
    InstanceConcurrency,
)
from services.image_analysis.limited_resources import StageLimitedResource


def test_model_stage_limit_serializes_two_classrooms():
    limits = InstanceConcurrency(ImageConcurrencySettings(model_concurrency=1))
    first_started = Event()
    release_first = Event()
    state = {"active": 0, "maximum": 0}
    lock = Lock()

    class Model:
        def analyze(self, **kwargs):
            with lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
                first_started.set()
            release_first.wait(2)
            with lock:
                state["active"] -= 1
            return {"HEAD_UP": 0}

    limited = StageLimitedResource(Model(), limits)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            limited.analyze, image_urls=(), bundle=None, requested_metrics=()
        )
        assert first_started.wait(2)
        second = pool.submit(
            limited.analyze, image_urls=(), bundle=None, requested_metrics=()
        )
        assert limits.snapshot()["model"]["active"] == 1
        release_first.set()
        assert first.result(timeout=2) == second.result(timeout=2)
    assert state["maximum"] == 1
    assert limits.snapshot()["model"]["active"] == 0
