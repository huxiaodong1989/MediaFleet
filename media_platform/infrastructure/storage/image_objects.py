"""图像分析纯对象上传与短时授权读图；不做业务文件登记或自动建桶。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Protocol


@dataclass(frozen=True)
class ImageObjectRef:
    profile: str
    provider: str
    bucket: str
    key: str


class ImageObjectStore(Protocol):
    def put_jpeg(self, key: str, body: bytes) -> ImageObjectRef: ...
    def signed_read_url(self, ref: ImageObjectRef, *, lifetime_seconds: int) -> str: ...


def _validate(key: str, body: bytes | None = None) -> None:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise ValueError("图片对象键无效")
    if body is not None and (
        not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9")
    ):
        raise ValueError("图片内容不是完整JPEG")


class CosImageObjectStore:
    def __init__(self, client, *, profile: str, bucket: str):
        if not profile or not bucket:
            raise ValueError("COS对象存储配置不完整")
        self.client, self.profile, self.bucket = client, profile, bucket

    def put_jpeg(self, key: str, body: bytes) -> ImageObjectRef:
        _validate(key, body)
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="image/jpeg"
        )
        return ImageObjectRef(self.profile, "COS", self.bucket, key)

    def signed_read_url(self, ref: ImageObjectRef, *, lifetime_seconds: int) -> str:
        if (
            ref.profile != self.profile
            or ref.provider != "COS"
            or ref.bucket != self.bucket
        ):
            raise ValueError("图片存储引用与当前COS配置不匹配")
        _validate(ref.key)
        if lifetime_seconds < 1:
            raise ValueError("签名有效期无效")
        return self.client.get_presigned_url(
            Method="GET", Bucket=ref.bucket, Key=ref.key, Expired=lifetime_seconds
        )


class MinioImageObjectStore:
    def __init__(self, client, *, profile: str, bucket: str):
        if not profile or not bucket:
            raise ValueError("MinIO对象存储配置不完整")
        self.client, self.profile, self.bucket = client, profile, bucket

    def put_jpeg(self, key: str, body: bytes) -> ImageObjectRef:
        _validate(key, body)
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=key,
            data=BytesIO(body),
            length=len(body),
            content_type="image/jpeg",
        )
        return ImageObjectRef(self.profile, "MINIO", self.bucket, key)

    def signed_read_url(self, ref: ImageObjectRef, *, lifetime_seconds: int) -> str:
        if (
            ref.profile != self.profile
            or ref.provider != "MINIO"
            or ref.bucket != self.bucket
        ):
            raise ValueError("图片存储引用与当前MinIO配置不匹配")
        _validate(ref.key)
        if lifetime_seconds < 1:
            raise ValueError("签名有效期无效")
        return self.client.presigned_get_object(
            ref.bucket, ref.key, expires=timedelta(seconds=lifetime_seconds)
        )


__all__ = [
    "CosImageObjectStore",
    "ImageObjectRef",
    "ImageObjectStore",
    "MinioImageObjectStore",
]
