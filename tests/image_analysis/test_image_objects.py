from unittest.mock import Mock

import pytest

from media_platform.infrastructure.storage.image_objects import (
    CosImageObjectStore,
    ImageObjectRef,
    MinioImageObjectStore,
)


JPEG = b"\xff\xd8pixel\xff\xd9"


def test_cos_image_upload_and_private_read_url():
    client = Mock()
    client.get_presigned_url.return_value = "https://cos.test/signed"
    store = CosImageObjectStore(client, profile="school-cos-v1", bucket="image-test")
    ref = store.put_jpeg("image/task/sample/frame.jpg", JPEG)
    assert ref == ImageObjectRef(
        "school-cos-v1", "COS", "image-test", "image/task/sample/frame.jpg"
    )
    client.put_object.assert_called_once_with(
        Bucket="image-test", Key=ref.key, Body=JPEG, ContentType="image/jpeg"
    )
    assert store.signed_read_url(ref, lifetime_seconds=60) == "https://cos.test/signed"
    with pytest.raises(ValueError):
        store.signed_read_url(
            ImageObjectRef("other", "COS", "image-test", ref.key), lifetime_seconds=60
        )


def test_minio_image_upload_does_not_create_bucket():
    client = Mock()
    client.presigned_get_object.return_value = "https://minio.test/signed"
    store = MinioImageObjectStore(client, profile="local-minio", bucket="image-test")
    ref = store.put_jpeg("image/task/sample/frame.jpg", JPEG)
    assert client.put_object.call_args.kwargs["length"] == len(JPEG)
    assert client.put_object.call_args.kwargs["data"].read() == JPEG
    client.make_bucket.assert_not_called()
    assert (
        store.signed_read_url(ref, lifetime_seconds=60) == "https://minio.test/signed"
    )


@pytest.mark.parametrize(
    "key", ["", "/absolute.jpg", "image/../other.jpg", "image\\a.jpg"]
)
def test_image_object_key_validation(key):
    with pytest.raises(ValueError):
        CosImageObjectStore(Mock(), profile="p", bucket="b").put_jpeg(key, JPEG)
