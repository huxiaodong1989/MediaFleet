import os
import logging
import uuid
import aiohttp
from abc import ABC, abstractmethod
from typing import Optional
from qcloud_cos import CosConfig
from qcloud_cos import CosS3Client
from datetime import date
from minio import Minio
from minio.error import S3Error

from media_platform.common.config import Settings

logger = logging.getLogger(__name__)
settings = Settings()

class StorageService(ABC):
    """存储服务接口"""

    @abstractmethod
    async def upload_file(self, file_path: str, target_path: Optional[str] = None) -> str:
        """
        上传文件到存储服务

        :param file_path: 本地文件路径
        :param target_path: 目标路径，不指定则自动生成
        :return: 文件URL
        """
        pass

    async def download_file(self, file_url: str, target_path: str = settings.storage.download_path) -> Optional[str]:
        """
        从存储服务下载文件（异步流式下载，避免阻塞事件循环和内存溢出）

        :param file_url: 文件URL
        :param target_path: 目标保存路径(默认下载到配置文件中设置的路径)
        :return: 本地文件路径，失败返回None
        """
        logger.info(f"download {file_url} -> {target_path}")
        os.makedirs(target_path, exist_ok=True)

        file_name = os.path.basename(file_url)
        target_file = os.path.join(target_path, file_name)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status == 200:
                        with open(target_file, 'wb') as f:
                            async for chunk in response.content.iter_chunked(64 * 1024):
                                f.write(chunk)
                        return target_file
                    else:
                        logger.error(f"Failed to download file. HTTP Status Code: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Download file failed: {e}")
            if os.path.exists(target_file):
                os.remove(target_file)
            return None

    # @abstractmethod
    # async def download_file(self, file_url: str, target_path: str) -> str:
    #     """
    #     从存储服务下载文件

    #     :param file_url: 文件URL
    #     :param target_path: 目标保存路径
    #     :return: 本地文件路径
    #     """
    #     pass

    # @abstractmethod
    # async def delete_file(self, file_url: str) -> bool:
    #     """
    #     删除存储服务中的文件

    #     :param file_url: 文件URL
    #     :return: 是否成功
    #     """
    #     pass

    # @abstractmethod
    # async def get_file_url(self, file_path: str) -> str:
    #     """
    #     获取文件URL

    #     :param file_path: 存储服务中的文件路径
    #     :return: 文件URL
    #     """
    #     pass

class CosStorageService(StorageService):
    """COS存储服务实现"""

    async def upload_file(self, file_path: str, target_path: Optional[str] = None) -> str:
        # 设置用户属性
        secret_id = settings.COS_SECRET_ID
        secret_key = settings.COS_SECRET_KEY
        region = settings.COS_REGION
        # region = 'ap-guangzhou'      # COS 支持的所有 region 列表参见 https://cloud.tencent.com/document/product/436/6224
        token = None               # 如果使用永久密钥不需要填入 token，如果使用临时密钥需要填入
        scheme = 'https'           # 指定使用 http/https 协议来访问 COS，默认为 https，可不填
        bucket = settings.COS_BUCKET_NAME
        # bucket = 'mediafleet'



        config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key, Token=token, Scheme=scheme)
        client = CosS3Client(config)

        # 生成目标文件名称
        source_file=file_path
        uuid_str= str(uuid.uuid4()).replace('-','')
        file_extension=os.path.splitext(source_file)[1]
        destination_file = f'upload/{date.today()}/{uuid_str}{file_extension}'

        # 根据文件大小自动选择简单上传或分块上传，分块上传具备断点续传功能。
        response = client.upload_file(
            Bucket=bucket,
            LocalFilePath=source_file,
            Key=destination_file,
            PartSize=1,
            MAXThread=10,
            EnableMD5=False,
            # progress_callback=upload_percentage,
        )
        print(response['ETag'])
        share_link=f'https://{bucket}.cos.{region}.myqcloud.com/{destination_file}'
        print(share_link)
        return share_link

class MinioStorageService(StorageService):
    """MinIO存储服务实现"""

    def __init__(self):

        self.client = Minio(
            endpoint=settings.minio.endpoint,
            access_key=settings.minio.access_key,
            secret_key=settings.minio.secret_key,
            secure=settings.minio.secure,
            region=settings.minio.region
        )
        self.bucket_name = settings.minio.bucket_name
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """确保存储桶存在"""
        try:
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name, location=settings.minio.region)
                logger.info(f"创建MinIO存储桶: {self.bucket_name}")
        except S3Error as e:
            logger.error(f"创建MinIO存储桶失败: {e}")
            raise

    async def upload_file(self, file_path: str, target_path: Optional[str] = None) -> str:
        """
        上传文件到MinIO存储

        :param file_path: 本地文件路径
        :param target_path: 目标路径，不指定则自动生成
        :return: 文件URL
        """
        try:
            # 如果未指定目标路径，则自动生成
            if target_path is None:
                ext = os.path.splitext(file_path)[1]
                uuid_str = str(uuid.uuid4()).replace('-', '')
                target_path = f"upload/{date.today()}/{uuid_str}{ext}"

            # 获取文件大小
            file_size = os.path.getsize(file_path)

            # 上传文件到MinIO
            with open(file_path, 'rb') as file_data:
                self.client.put_object(
                    bucket_name=self.bucket_name,
                    object_name=target_path,
                    data=file_data,
                    length=file_size,
                    content_type=self._get_content_type(file_path)
                )

            logger.info(f"文件已上传到MinIO: {target_path}")

            # 返回文件URL
            return self._generate_file_url(target_path)

        except S3Error as e:
            logger.error(f"MinIO上传文件失败: {e}")
            raise
        except Exception as e:
            logger.error(f"上传文件时发生错误: {e}")
            raise

    # async def download_file(self, file_url: str, target_path: str = settings.storage.download_path) -> Optional[str]:
    #     """
    #     从MinIO存储下载文件

    #     :param file_url: 文件URL
    #     :param target_path: 目标保存路径
    #     :return: 本地文件路径
    #     """
    #     try:
    #         # 从URL中提取对象名称
    #         object_name = self._url_to_object_name(file_url)

    #         # 确保目标目录存在
    #         os.makedirs(target_path, exist_ok=True)

    #         # 生成本地文件路径
    #         file_name = os.path.basename(object_name)
    #         local_file_path = os.path.join(target_path, file_name)

    #         # 从MinIO下载文件
    #         self.client.fget_object(
    #             bucket_name=self.bucket_name,
    #             object_name=object_name,
    #             file_path=local_file_path
    #         )

    #         logger.info(f"文件已从MinIO下载: {local_file_path}")
    #         return local_file_path

    #     except S3Error as e:
    #         logger.error(f"MinIO下载文件失败: {e}")
    #         return None
    #     except Exception as e:
    #         logger.error(f"下载文件时发生错误: {e}")
    #         return None

    def _get_content_type(self, file_path: str) -> str:
        """根据文件扩展名获取Content-Type"""
        ext = os.path.splitext(file_path)[1].lower()
        content_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.mp4': 'video/mp4',
            '.avi': 'video/avi',
            '.mov': 'video/quicktime',
            '.wmv': 'video/x-ms-wmv',
            '.flv': 'video/x-flv',
            '.webm': 'video/webm',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.flac': 'audio/flac',
            '.aac': 'audio/aac',
            '.ogg': 'audio/ogg',
            '.pdf': 'application/pdf',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.txt': 'text/plain',
            '.json': 'application/json',
            '.xml': 'application/xml',
            '.zip': 'application/zip',
            '.tar': 'application/x-tar',
            '.gz': 'application/gzip'
        }
        return content_types.get(ext, 'application/octet-stream')

    def _generate_file_url(self, object_name: str) -> str:
        """生成文件访问URL"""
        # 如果使用HTTPS，则使用https协议
        protocol = "https" if settings.minio.secure else "http"
        return f"{protocol}://{settings.minio.endpoint}/{self.bucket_name}/{object_name}"

    def _url_to_object_name(self, file_url: str) -> str:
        """从URL中提取对象名称"""
        # 移除协议前缀和域名部分，获取对象路径
        if file_url.startswith(('http://', 'https://')):
            # 解析URL，提取路径部分
            from urllib.parse import urlparse
            parsed_url = urlparse(file_url)
            path = parsed_url.path

            # 移除bucket名称部分
            if path.startswith(f'/{self.bucket_name}/'):
                return path[len(f'/{self.bucket_name}/'):]
            elif path.startswith('/'):
                return path[1:]
            else:
                return path
        else:
            # 如果不是HTTP URL，直接返回文件名
            return os.path.basename(file_url)

def get_storage_service() -> StorageService:
    """
    获取存储服务实例

    根据配置选择合适的存储服务实现
    """
    storage_type = settings.storage.type.lower()
    logger.debug("storage_type:",storage_type)
    if storage_type == "minio":
        return MinioStorageService()
    elif storage_type == "cos":
        return CosStorageService()
    else:
        raise ValueError(
            f"不支持的存储类型: {storage_type}，当前仅支持 cos 或 minio"
        )
