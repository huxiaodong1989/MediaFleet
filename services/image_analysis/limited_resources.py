"""图像课堂跨线程共享的阶段额度；等待时不占用额外FFmpeg/网络资源。"""

from __future__ import annotations

from time import sleep

from services.image_analysis.concurrency import ConcurrencyStage, InstanceConcurrency


class StageLimitedResource:
    def __init__(self, resource, limits: InstanceConcurrency):
        self.resource = resource
        self.limits = limits

    def _call(self, stage: ConcurrencyStage, name: str, *args, **kwargs):
        while True:
            with self.limits.try_slot(stage) as acquired:
                if acquired:
                    return getattr(self.resource, name)(*args, **kwargs)
            sleep(0.05)

    def extract(self, source, offset_ms):
        return self._call(ConcurrencyStage.FFMPEG, "extract", source, offset_ms)

    def probe(self, source):
        return self._call(ConcurrencyStage.FFMPEG, "probe", source)

    def probe_range(self, source):
        return self.resource.probe_range(source)

    def download(self, source, *, target, probe):
        return self._call(
            ConcurrencyStage.DOWNLOAD,
            "download",
            source,
            target=target,
            probe=probe,
        )

    def put_jpeg(self, key, body):
        return self._call(ConcurrencyStage.UPLOAD, "put_jpeg", key, body)

    def signed_read_url(self, ref, *, lifetime_seconds):
        return self.resource.signed_read_url(ref, lifetime_seconds=lifetime_seconds)

    def analyze(self, *, image_urls, bundle, requested_metrics):
        return self._call(
            ConcurrencyStage.MODEL,
            "analyze",
            image_urls=image_urls,
            bundle=bundle,
            requested_metrics=requested_metrics,
        )


__all__ = ["StageLimitedResource"]
