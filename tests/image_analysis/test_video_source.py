import httpx
import pytest

from services.image_analysis.video_source import (
    HttpVideoSource,
    RangeProbe,
    VideoSourceError,
)


def source(handler, *, max_bytes=1024):
    return HttpVideoSource(
        allowed_hosts=("video.test",),
        allowed_networks=("192.0.2.0/24",),
        max_download_bytes=max_bytes,
        transport=httpx.MockTransport(handler),
        resolver=lambda _: ("192.0.2.10",),
    )


def test_range_206_then_checked_atomic_download(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.headers.get("Range"))
        if request.headers.get("Range"):
            return httpx.Response(
                206,
                headers={"Content-Range": "bytes 0-0/4", "ETag": '"v1"'},
                content=b"d",
            )
        return httpx.Response(200, headers={"ETag": '"v1"'}, content=b"data")

    reader = source(handler)
    probe = reader.probe_range("https://video.test/class.mp4?token=secret")
    assert probe == RangeProbe(4, '"v1"', True)
    target = tmp_path / "class.mp4"
    assert (
        reader.download(
            "https://video.test/class.mp4?token=secret", target=target, probe=probe
        )
        == target
    )
    assert target.read_bytes() == b"data"
    assert calls == ["bytes=0-0", None]
    assert not list(tmp_path.glob("*.part"))


def test_range_ignored_200_does_not_read_video_body():
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("range探测不能消费完整响应体")

    reader = source(
        lambda _: httpx.Response(200, headers={"Content-Length": "1200"}, stream=Body())
    )
    assert reader.probe_range("https://video.test/a.mp4") == RangeProbe(
        1200, None, False
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        (302, "source_redirect_forbidden"),
        (403, "source_access_denied"),
        (404, "source_missing"),
        (429, "source_rate_limited"),
        (503, "source_transient_error"),
        (416, "range_probe_failed"),
    ],
)
def test_probe_classifies_status_without_leaking_token(status, expected):
    reader = source(
        lambda _: httpx.Response(status, headers={"Location": "https://evil.test/"})
    )
    with pytest.raises(VideoSourceError) as error:
        reader.probe_range("https://video.test/a.mp4?token=secret")
    assert error.value.code == expected
    assert "secret" not in str(error.value)


def test_source_allowlist_rejects_private_redirect_and_unlisted_host():
    reader = source(
        lambda _: httpx.Response(206, headers={"Content-Range": "bytes 0-0/4"})
    )
    with pytest.raises(VideoSourceError, match="source_not_allowed"):
        reader.probe_range("http://127.0.0.1/a.mp4")
    with pytest.raises(VideoSourceError, match="source_not_allowed"):
        reader.probe_range("https://video.test.evil.test/a.mp4")
    with pytest.raises(VideoSourceError, match="source_not_allowed"):
        reader.probe_range("https://user:password@video.test/a.mp4")
    with pytest.raises(VideoSourceError, match="source_not_allowed"):
        reader.probe_range("https://video.test:8080/a.mp4")


@pytest.mark.parametrize(
    "body,etag,error",
    [
        (b"abc", '"v1"', "source_changed"),
        (b"data", '"v2"', "source_changed"),
        (b"datax", '"v1"', "source_changed"),
    ],
)
def test_download_rejects_changed_or_truncated_source_and_cleans_part(
    tmp_path, body, etag, error
):
    reader = source(lambda _: httpx.Response(200, headers={"ETag": etag}, content=body))
    target = tmp_path / "class.mp4"
    with pytest.raises(VideoSourceError, match=error):
        reader.download(
            "https://video.test/a.mp4", target=target, probe=RangeProbe(4, '"v1"', True)
        )
    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))


def test_download_has_hard_byte_limit(tmp_path):
    reader = source(lambda _: httpx.Response(200, content=b"too-large"), max_bytes=4)
    with pytest.raises(VideoSourceError, match="download_size_limit"):
        reader.download(
            "https://video.test/a.mp4",
            target=tmp_path / "class.mp4",
            probe=RangeProbe(None, None, False),
        )


def test_download_rejects_oversized_probe_without_request(tmp_path):
    reader = source(lambda _: pytest.fail("不应发起下载"), max_bytes=4)
    with pytest.raises(VideoSourceError, match="download_size_limit"):
        reader.download(
            "https://video.test/a.mp4",
            target=tmp_path / "class.mp4",
            probe=RangeProbe(5, None, False),
        )


def test_download_rejects_advertised_version_change_before_body(tmp_path):
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("版本不符不能读取响应体")

    reader = source(
        lambda _: httpx.Response(200, headers={"Content-Length": "5"}, stream=Body())
    )
    target = tmp_path / "class.mp4"
    with pytest.raises(VideoSourceError, match="source_changed"):
        reader.download(
            "https://video.test/a.mp4", target=target, probe=RangeProbe(4, None, True)
        )
    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))


def test_download_does_not_overwrite_target_created_during_transfer(tmp_path):
    target = tmp_path / "class.mp4"

    def handler(_):
        target.write_bytes(b"other worker")
        return httpx.Response(200, content=b"data")

    reader = source(handler)
    with pytest.raises(FileExistsError):
        reader.download(
            "https://video.test/a.mp4",
            target=target,
            probe=RangeProbe(None, None, False),
        )
    assert target.read_bytes() == b"other worker"
    assert not list(tmp_path.glob("*.part"))
