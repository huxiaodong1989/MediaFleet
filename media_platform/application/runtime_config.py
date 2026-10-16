"""进程内配置刷新与可调整额度；数据库版本仍是配置事实来源。"""

from contextlib import contextmanager
from datetime import datetime
from random import uniform
from threading import Event, Lock, Thread


class AdjustableLimits:
    def __init__(self, **limits):
        self._lock = Lock()
        self._active = dict.fromkeys(limits, 0)
        self._limits = dict(limits)

    def update(self, **limits):
        if set(limits) != set(self._limits) or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 1
            for v in limits.values()
        ):
            raise ValueError("额度字段或范围无效")
        with self._lock:
            self._limits = dict(limits)

    @contextmanager
    def try_slot(self, stage):
        with self._lock:
            acquired = self._active[stage] < self._limits[stage]
            if acquired:
                self._active[stage] += 1
        try:
            yield acquired
        finally:
            if acquired:
                with self._lock:
                    self._active[stage] -= 1

    def snapshot(self):
        with self._lock:
            return {
                key: {"limit": value, "active": self._active[key]}
                for key, value in self._limits.items()
            }


class RuntimeConfigRefresher:
    def __init__(self, store, service, appliers, interval=5):
        self.store, self.service, self.appliers = store, service, appliers
        self.interval = interval
        self._stop = Event()
        self._thread = None
        self._lock = Lock()
        self._state = {}

    def refresh_once(self):
        for category, apply in self.appliers.items():
            target = None
            try:
                with self.store.session_factory() as session:
                    head = self.store.head(session, self.service, category)
                    target = head.version_id if head else None
                if not target:
                    continue
                with self._lock:
                    previous = dict(self._state.get(category, {}))
                if previous.get("applied_id") == target and not previous.get("error"):
                    continue
                snapshot = self.store.snapshot(self.service, category, target)
                apply(snapshot)
                with self._lock:
                    self._state[category] = {
                        "target_id": target,
                        "applied_id": target,
                        "revision": snapshot.revision,
                        "applied_at": datetime.now().isoformat(),
                        "error": None,
                    }
            except Exception as exc:
                # 异常正文可能含数据库参数或密钥，不上报到管理页/日志。
                with self._lock:
                    previous = self._state.get(category, {})
                    self._state[category] = {
                        **previous,
                        "target_id": target,
                        "error": type(exc).__name__,
                    }

    def status(self):
        with self._lock:
            return {key: dict(value) for key, value in self._state.items()}

    def _run(self):
        while not self._stop.wait(self.interval + uniform(0, 1)):
            self.refresh_once()

    def start(self):
        self.refresh_once()
        self._stop.clear()
        self._thread = Thread(
            target=self._run, name=f"{self.service}-config", daemon=True
        )
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2)
