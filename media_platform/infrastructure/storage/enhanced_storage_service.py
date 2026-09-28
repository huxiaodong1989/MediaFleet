import asyncio
import os
import logging
import hashlib
import uuid
import aiohttp
from typing import Optional, Dict, Any
from datetime import date
from qcloud_cos import CosConfig
from qcloud_cos import CosS3Client
from minio import Minio
from minio.error import S3Error
from abc import ABC, abstractmethod

from media_platform.common.config import Settings, get_settings
from media_platform.common.redaction import sanitize_url

logger = logging.getLogger(__name__)
settings = Settings()


class StorageServiceEnhanced(ABC):
    """存储服务接口"""

    @abstractmethod
    async def upload_file_enhanced(self, file_path: str, target_path: Optional[str] = None, upload_type: str = "video")  -> str:
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

        url_path = file_url.split('?')[0]
        ext = os.path.splitext(os.path.basename(url_path))[1]
        file_name = f"{uuid.uuid4().hex}{ext}" if ext else str(uuid.uuid4().hex)
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

class UploadVerificationUtil:
    """上传验证工具类"""

    @staticmethod
    def is_upload_successful(result: Any, expected_size: int = None, expected_md5: str = None) -> bool:
        """
        通用上传成功判断方法

        :param result: 上传结果对象
        :param expected_size: 期望的文件大小
        :param expected_md5: 期望的MD5值
        :return: 是否上传成功
        """
        try:
            # 基本检查：结果对象不为空
            if not result:
                logger.error("上传结果为空")
                return False

            # 如果有大小期望，进行大小验证
            if expected_size is not None:
                actual_size = getattr(result, 'size', None)
                if actual_size is None:
                    logger.warning("无法获取上传后文件大小")
                elif actual_size != expected_size:
                    logger.error(f"文件大小不匹配，期望: {expected_size}, 实际: {actual_size}")
                    return False

            # 如果有MD5期望，进行MD5验证
            if expected_md5 is not None:
                actual_md5 = getattr(result, 'md5', None) or getattr(result, 'etag', None)
                if actual_md5:
                    # 清理ETag中的引号
                    actual_md5 = actual_md5.strip('"')
                    if actual_md5.lower() != expected_md5.lower():
                        logger.error(f"文件MD5不匹配，期望: {expected_md5}, 实际: {actual_md5}")
                        return False
                else:
                    logger.warning("无法获取上传后文件MD5")

            return True

        except Exception as e:
            logger.error(f"验证上传结果时发生错误: {str(e)}")
            return False

    @staticmethod
    def get_upload_status_info(result: Any) -> Dict[str, Any]:
        """
        获取上传状态信息

        :param result: 上传结果对象
        :return: 状态信息字典
        """
        info = {
            "success": False,
            "size": None,
            "etag": None,
            "md5": None,
            "timestamp": None
        }

        try:
            if result:
                info["success"] = True
                info["size"] = getattr(result, 'size', None)
                info["etag"] = getattr(result, 'etag', None)
                info["md5"] = getattr(result, 'md5', None)
                info["timestamp"] = getattr(result, 'last_modified', None)

        except Exception as e:
            logger.error(f"获取上传状态信息失败: {str(e)}")

        return info

def calculate_file_md5(file_path: str) -> str:
    """
    计算文件的MD5值

    :param file_path: 文件路径
    :return: 文件的MD5值
    """
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"计算文件MD5失败: {file_path}, 错误: {str(e)}")
        return ""

class UploadResult:
    """上传结果类"""
    def __init__(self, file_url: str, file_id: str = None, storage_key: str = None, md5: str = None,
                 verified: bool = False, file_size: int = None, etag: str = None, file_name: str = None,
                 bucket: str = None):
        self.file_url = file_url
        self.file_id = file_id  # 从回调接口获取的fileId
        self.storage_key = storage_key  # 存储位置key
        self.bucket = bucket  # 存储桶名称
        self.md5 = md5  # 文件MD5值
        self.verified = verified  # 是否经过验证
        self.file_size = file_size  # 文件大小
        self.etag = etag  # 上传后的ETag
        self.upload_timestamp = None  # 上传时间戳
        self.file_name = file_name  # 文件名

    def is_upload_successful(self) -> bool:
        """
        判断上传是否成功

        :return: 上传是否成功
        """
        return (
            bool(self.file_url) and  # 必须有文件URL
            bool(self.storage_key) and  # 必须有存储key
            self.verified  # 必须经过验证
        )

    def get_upload_info(self) -> Dict[str, Any]:
        """
        获取上传信息摘要

        :return: 上传信息字典
        """
        return {
            "file_url": self.file_url,
            "file_id": self.file_id,
            "storage_key": self.storage_key,
            "bucket": self.bucket,
            "md5": self.md5,
            "file_size": self.file_size,
            "etag": self.etag,
            "verified": self.verified,
            "success": self.is_upload_successful(),
            "upload_timestamp": self.upload_timestamp
        }

class EnhancedCosStorageService(StorageServiceEnhanced):
    """增强版COS存储服务"""

    async def upload_file_enhanced(self, file_path: str, target_path: Optional[str] = None, upload_type: str = "video") -> UploadResult:
        """
        增强版COS上传接口，上传完成后回调业务端获取fileId

        :param file_path: 本地文件路径
        :param target_path: 目标路径，不指定则自动生成
        :return: UploadResult对象，包含文件URL和fileId
        """
        try:
            # 计算文件MD5
            file_md5 = calculate_file_md5(file_path)
            if not file_md5:
                raise Exception("计算文件MD5失败")

            logger.info(f"计算文件MD5: {file_md5}")

            # 设置用户属性
            secret_id = settings.cos.secret_id
            secret_key = settings.cos.secret_key
            region = settings.cos.region
            token = None
            scheme = 'https'
            bucket = settings.cos.bucket
            file_name = None

            config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key, Token=token, Scheme=scheme)
            client = CosS3Client(config)

            # 生成目标文件名称
            if target_path is None:
                uuid_str = str(uuid.uuid4()).replace('-', '')
                file_extension = os.path.splitext(file_path)[1]
                if upload_type == "video":
                    destination_file = f'upload/video/{date.today()}/{uuid_str}{file_extension}'
                elif upload_type == "audio":
                    destination_file = f'upload/audio/{date.today()}/{uuid_str}{file_extension}'
                elif upload_type == "cover":
                    destination_file = f'upload/cover/{date.today()}/{uuid_str}{file_extension}'
                elif upload_type == "record":
                    destination_file = f'upload/record/{date.today()}/{uuid_str}{file_extension}'
                elif upload_type == "image":
                    destination_file = f'upload/image/{date.today()}/{uuid_str}{file_extension}'
                elif upload_type == "other":
                    destination_file = f'upload/other/{date.today()}/{uuid_str}{file_extension}'
                else:
                    # 默认处理：对于未知类型，使用 other 目录
                    logger.warning(f"未知的 upload_type: {upload_type}，使用默认路径")
                    destination_file = f'upload/other/{date.today()}/{uuid_str}{file_extension}'
                # destination_file = f'upload/{date.today()}/{uuid_str}{file_extension}'
                file_name=f'{uuid_str}{file_extension}'
            else:
                destination_file = target_path

            # 上传文件到COS
            response = client.upload_file(
                Bucket=bucket,
                LocalFilePath=file_path,
                Key=destination_file,
                PartSize=1,
                MAXThread=10,
                EnableMD5=False,
            )

            # 验证上传成功 - 检查文件是否确实存在于COS中
            try:
                # 获取本地文件大小用于对比
                local_file_size = os.path.getsize(file_path)

                # 获取上传后的文件信息
                head_response = client.head_object(Bucket=bucket, Key=destination_file)
                remote_file_size = int(head_response.get('Content-Length', 0))

                # 验证文件大小是否一致
                if remote_file_size != local_file_size:
                    raise Exception(f"上传文件大小不匹配，本地: {local_file_size}, 远程: {remote_file_size}")

                etag = response.get('ETag', '').strip('"')
                logger.info(f"✓ 文件上传成功验证通过 - COS: {destination_file}, 大小: {remote_file_size} bytes, ETag: {etag}")

            except Exception as verify_error:
                logger.error(f"上传后验证失败: {verify_error}")
                # 尝试删除可能存在的不完整文件
                try:
                    client.delete_object(Bucket=bucket, Key=destination_file)
                except:
                    pass
                raise Exception(f"上传验证失败: {verify_error}")

            logger.info(f"文件已上传到COS: {destination_file}, ETag: {response.get('ETag', '')}")

            # 生成文件URL
            #file_url = f'https://{bucket}.cos.{region}.myqcloud.com/{destination_file}'
            file_url=f'{settings.cos.fileurl_head}/{destination_file}'
            #file_url=f'{destination_file}'
            # 调用回调接口获取fileId
            # callback_data = {
            #     "fileName": file_name,
            #     "appId": settings.cos.app_id,
            #     "bucket": bucket,
            #     "region": region,
            #     "key": destination_file,
            #     "md5": file_md5
            # }

            # file_id = await self._call_cos_callback(callback_data)

            #删除本地文件
            #os.remove(file_path)

            return UploadResult(
                file_url=file_url,
                file_id=None,
                storage_key=destination_file,
                md5=file_md5,
                verified=True,
                file_size=remote_file_size,
                etag=etag,
                file_name=file_name,
                bucket=bucket
            )

        except Exception as e:
            logger.error(f"COS增强上传失败: {str(e)}")
            raise

    async def _call_cos_callback(self, callback_data: Dict[str, Any]) -> str:
        """调用COS回调接口获取fileId，支持重试和降级处理"""
        settings = get_settings()
        callback_url = settings.cos.callback_url

        if not callback_url:
            logger.warning("COS回调URL未配置，使用UUID作为fileId")
            return str(uuid.uuid4())

        max_retries = 3
        retry_delays = [1, 3, 5]  # 递增重试延迟
        last_exception = None

        for attempt in range(max_retries):
            try:
                logger.info(
                    "调用COS回调接口(尝试 %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    sanitize_url(callback_url),
                )

                # 设置连接和读取超时
                timeout = aiohttp.ClientTimeout(
                    total=45,      # 总超时时间增加到45秒
                    connect=10,    # 连接超时10秒
                    sock_read=30   # 读取超时30秒
                )

                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        limit=100,
                        ttl_dns_cache=300,
                        use_dns_cache=True,
                        keepalive_timeout=30
                    )
                ) as session:
                    async with session.post(
                        callback_url,
                        json=callback_data,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "EnhancedStorageService/1.0",
                            "Connection": "close"
                        },
                        timeout=timeout
                    ) as response:
                        response_text = await response.text()

                        if response.status == 200:
                            try:
                                result = await response.json()
                                logger.info(f"COS回调响应(尝试 {attempt + 1}): {result}")

                                # 优先尝试从data中获取fileId
                                file_id = None
                                if isinstance(result, dict):
                                    # 尝试从data字段中获取fileId
                                    data = result.get('data', {})
                                    if isinstance(data, dict) and 'fileId' in data:
                                        file_id = data['fileId']
                                    # 如果data中没有，再尝试从根级别获取
                                    elif 'fileId' in result:
                                        file_id = result['fileId']

                                if file_id:
                                    logger.info(f"✓ COS回调成功获取fileId: {file_id}")
                                    return str(file_id)
                                else:
                                    error_msg = f"COS回调响应中未找到fileId: {result}"
                                    logger.error(error_msg)
                                    last_exception = Exception(error_msg)

                            except Exception as json_error:
                                error_msg = f"解析COS回调响应JSON失败: {json_error}, 响应内容: {response_text[:500]}"
                                logger.error(error_msg)
                                last_exception = Exception(error_msg)
                        else:
                            error_msg = f"COS回调HTTP错误，状态码: {response.status}, 响应: {response_text[:500]}"
                            logger.error(error_msg)
                            last_exception = Exception(error_msg)

            except asyncio.TimeoutError as timeout_error:
                error_msg = f"COS回调接口超时(尝试 {attempt + 1}/{max_retries}): 总超时45秒"
                logger.warning(error_msg)
                last_exception = Exception(error_msg)

            except aiohttp.ClientError as client_error:
                error_msg = f"COS回调网络错误(尝试 {attempt + 1}/{max_retries}): {type(client_error).__name__} - {str(client_error)}"
                logger.warning(error_msg)
                last_exception = Exception(error_msg)

            except Exception as e:
                error_msg = f"COS回调未知错误(尝试 {attempt + 1}/{max_retries}): {type(e).__name__} - {str(e)}"
                logger.warning(error_msg)
                last_exception = Exception(error_msg)

            # 如果不是最后一次尝试，等待一段时间后重试
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"等待 {delay} 秒后重试...")
                await asyncio.sleep(delay)

        # 所有重试都失败了，根据配置决定是否降级处理
        if self._should_fallback_on_callback_failure():
            fallback_file_id = f"fallback_{uuid.uuid4().hex[:12]}"
            logger.warning(f"COS回调接口重试失败，启用降级模式，生成fallback fileId: {fallback_file_id}")
            logger.warning(f"最后一次错误: {str(last_exception) if last_exception else '未知错误'}")
            return fallback_file_id
        else:
            # 不启用降级，抛出异常
            final_error_msg = f"COS回调接口调用失败，已重试{max_retries}次"
            if last_exception:
                final_error_msg += f"，最后错误: {str(last_exception)}"
            logger.error(final_error_msg)
            raise Exception(final_error_msg)

    def _should_fallback_on_callback_failure(self) -> bool:
        """判断是否在回调失败时启用降级模式"""
        # 可以通过环境变量控制是否启用降级
        import os
        return os.getenv("COS_CALLBACK_FALLBACK_ENABLED", "true").lower() == "true"

class EnhancedMinioStorageService(StorageServiceEnhanced):
    """增强版MinIO存储服务"""
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


    async def upload_file_enhanced(self, file_path: str, target_path: Optional[str] = None, upload_type: str = "video") -> UploadResult:
        """
        增强版MinIO上传接口，上传完成后回调业务端获取fileId

        :param file_path: 本地文件路径
        :param target_path: 目标路径，不指定则自动生成
        :return: UploadResult对象，包含文件URL和fileId
        """
        try:
            file_name = None
            # 计算文件MD5
            file_md5 = calculate_file_md5(file_path)
            if not file_md5:
                raise Exception("计算文件MD5失败")

            logger.info(f"计算文件MD5: {file_md5}")

            # 如果未指定目标路径，则自动生成
            if target_path is None:
                ext = os.path.splitext(file_path)[1]
                uuid_str = str(uuid.uuid4()).replace('-', '')
                if upload_type == "video":
                    target_path = f"upload/video/{date.today()}/{uuid_str}{ext}"
                elif upload_type == "audio":
                    target_path = f"upload/audio/{date.today()}/{uuid_str}{ext}"
                elif upload_type == "cover":
                    target_path = f"upload/cover/{date.today()}/{uuid_str}{ext}"
                elif upload_type == "record":
                    target_path = f"upload/record/{date.today()}/{uuid_str}{ext}"
                elif upload_type == "image":
                    target_path = f"upload/image/{date.today()}/{uuid_str}{ext}"
                elif upload_type == "other":
                    target_path = f"upload/other/{date.today()}/{uuid_str}{ext}"
                else:
                    # 默认处理：对于未知类型，使用 other 目录
                    logger.warning(f"未知的 upload_type: {upload_type}，使用默认路径")
                    target_path = f"upload/other/{date.today()}/{uuid_str}{ext}"
                # target_path = f"upload/{date.today()}/{uuid_str}{ext}"
                file_name = f'{uuid_str}{ext}'

            # 获取文件大小
            file_size = os.path.getsize(file_path)

            # 上传文件到MinIO
            with open(file_path, 'rb') as file_data:
                result = self.client.put_object(
                    bucket_name=self.bucket_name,
                    object_name=target_path,
                    data=file_data,
                    length=file_size,
                    content_type=self._get_content_type(file_path)
                )

            # 验证上传成功 - 检查文件是否确实存在于MinIO中
            try:
                stat_result = self.client.stat_object(self.bucket_name, target_path)
                uploaded_size = stat_result.size

                # 验证文件大小是否一致
                if uploaded_size != file_size:
                    raise Exception(f"上传文件大小不匹配，本地: {file_size}, 远程: {uploaded_size}")

                logger.info(f"✓ 文件上传成功验证通过 - MinIO: {target_path}, 大小: {uploaded_size} bytes, ETag: {result.etag}")

            except Exception as verify_error:
                logger.error(f"上传后验证失败: {verify_error}")
                # 尝试删除可能存在的不完整文件
                try:
                    self.client.remove_object(self.bucket_name, target_path)
                except:
                    pass
                raise Exception(f"上传验证失败: {verify_error}")

            logger.info(f"文件已上传到MinIO: {target_path}")

            # 生成文件URL
            file_url = self._generate_file_url(target_path)

            # # 调用回调接口获取fileId
            # callback_data = {
            #     "fileName": file_name,
            #     "key": target_path,
            #     "md5": file_md5
            # }

            # file_id = await self._call_minio_callback(callback_data)

            #删除本地文件
            #os.remove(file_path)

            return UploadResult(
                file_url=file_url,
                file_id=None,
                storage_key=target_path,
                md5=file_md5,
                verified=True,
                file_size=file_size,
                etag=result.etag,
                file_name=file_name,
                bucket=self.bucket_name
            )

        except S3Error as e:
            logger.error(f"MinIO增强上传失败: {e}")
            raise
        except Exception as e:
            logger.error(f"MinIO增强上传失败: {e}")
            raise

    async def _call_minio_callback(self, callback_data: Dict[str, Any]) -> str:
        """调用MinIO回调接口获取fileId，支持重试和降级处理"""
        settings = get_settings()
        callback_url = settings.minio.callback_url

        if not callback_url:
            logger.warning("MinIO回调URL未配置，使用UUID作为fileId")
            return str(uuid.uuid4())

        max_retries = 3
        retry_delays = [1, 3, 5]  # 递增重试延迟
        last_exception = None

        for attempt in range(max_retries):
            try:
                logger.info(
                    "调用MinIO回调接口(尝试 %s/%s): %s",
                    attempt + 1,
                    max_retries,
                    sanitize_url(callback_url),
                )

                # 设置连接和读取超时
                timeout = aiohttp.ClientTimeout(
                    total=45,      # 总超时时间增加到45秒
                    connect=10,    # 连接超时10秒
                    sock_read=30   # 读取超时30秒
                )

                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        limit=100,
                        ttl_dns_cache=300,
                        use_dns_cache=True,
                        keepalive_timeout=30
                    )
                ) as session:
                    async with session.post(
                        callback_url,
                        json=callback_data,
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "EnhancedStorageService/1.0",
                            "Connection": "close"
                        },
                        timeout=timeout
                    ) as response:
                        response_text = await response.text()

                        if response.status == 200:
                            try:
                                result = await response.json()
                                logger.info(f"MinIO回调响应(尝试 {attempt + 1}): {result}")

                                # 优先尝试从data中获取fileId
                                file_id = None
                                if isinstance(result, dict):
                                    # 尝试从data字段中获取fileId
                                    data = result.get('data', {})
                                    if isinstance(data, dict) and 'fileId' in data:
                                        file_id = data['fileId']
                                    # 如果data中没有，再尝试从根级别获取
                                    elif 'fileId' in result:
                                        file_id = result['fileId']

                                if file_id:
                                    logger.info(f"✓ MinIO回调成功获取fileId: {file_id}")
                                    return str(file_id)
                                else:
                                    error_msg = f"MinIO回调响应中未找到fileId: {result}"
                                    logger.error(error_msg)
                                    last_exception = Exception(error_msg)

                            except Exception as json_error:
                                error_msg = f"解析MinIO回调响应JSON失败: {json_error}, 响应内容: {response_text[:500]}"
                                logger.error(error_msg)
                                last_exception = Exception(error_msg)
                        else:
                            error_msg = f"MinIO回调HTTP错误，状态码: {response.status}, 响应: {response_text[:500]}"
                            logger.error(error_msg)
                            last_exception = Exception(error_msg)

            except asyncio.TimeoutError as timeout_error:
                error_msg = f"MinIO回调接口超时(尝试 {attempt + 1}/{max_retries}): 总超时45秒"
                logger.warning(error_msg)
                last_exception = Exception(error_msg)

            except aiohttp.ClientError as client_error:
                error_msg = f"MinIO回调网络错误(尝试 {attempt + 1}/{max_retries}): {type(client_error).__name__} - {str(client_error)}"
                logger.warning(error_msg)
                last_exception = Exception(error_msg)

            except Exception as e:
                error_msg = f"MinIO回调未知错误(尝试 {attempt + 1}/{max_retries}): {type(e).__name__} - {str(e)}"
                logger.warning(error_msg)
                last_exception = Exception(error_msg)

            # 如果不是最后一次尝试，等待一段时间后重试
            if attempt < max_retries - 1:
                delay = retry_delays[attempt]
                logger.info(f"等待 {delay} 秒后重试...")
                await asyncio.sleep(delay)

        # 所有重试都失败了，根据配置决定是否降级处理
        if self._should_fallback_on_callback_failure():
            fallback_file_id = f"fallback_{uuid.uuid4().hex[:12]}"
            logger.warning(f"MinIO回调接口重试失败，启用降级模式，生成fallback fileId: {fallback_file_id}")
            logger.warning(f"最后一次错误: {str(last_exception) if last_exception else '未知错误'}")
            return fallback_file_id
        else:
            # 不启用降级，抛出异常
            final_error_msg = f"MinIO回调接口调用失败，已重试{max_retries}次"
            if last_exception:
                final_error_msg += f"，最后错误: {str(last_exception)}"
            logger.error(final_error_msg)
            raise Exception(final_error_msg)

    def _should_fallback_on_callback_failure(self) -> bool:
        """判断是否在回调失败时启用降级模式"""
        # 可以通过环境变量控制是否启用降级
        import os
        return os.getenv("MINIO_CALLBACK_FALLBACK_ENABLED", "true").lower() == "true"

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
        #protocol = "https" if settings.minio.secure else "http"
        return f"{settings.minio.fileurl_head}/{self.bucket_name}/{object_name}"
        # return f"{protocol}://{settings.minio.endpoint}/{self.bucket_name}/{object_name}"


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

def get_enhanced_storage_service(storage_service_type:str="none"):
    """
    获取增强存储服务实例

    根据配置选择合适的增强存储服务实现
    """

    #指定存储服务类型，如果为none则使用配置文件中的存储服务类型
    if storage_service_type != "none":
        storage_type = storage_service_type.lower()
    else:
        storage_type = settings.storage.type.lower()

    logger.debug(f"获取增强存储服务类型: {storage_type}")

    if storage_type == "minio":
        return EnhancedMinioStorageService()
    elif storage_type == "cos":
        return EnhancedCosStorageService()
    else:
        raise ValueError(
            f"不支持的存储类型: {storage_type}，当前仅支持 cos 或 minio"
        )


# 使用示例
async def upload_file_with_verification_example():
    """
    上传文件并验证成功的示例用法
    """
    try:
        # 获取存储服务
        storage_service = get_enhanced_storage_service()

        # 上传文件
        file_path = "/path/to/your/file.txt"
        upload_result = await storage_service.upload_file_enhanced(file_path)

        # 方法1: 使用内置的成功判断方法
        if upload_result.is_upload_successful():
            logger.info("✅ 上传成功！")
            logger.info(f"文件URL: {upload_result.file_url}")
            logger.info(f"文件ID: {upload_result.file_id}")
            logger.info(f"存储Key: {upload_result.storage_key}")
            logger.info(f"文件大小: {upload_result.file_size} bytes")
            logger.info(f"MD5: {upload_result.md5}")
            logger.info(f"ETag: {upload_result.etag}")
        else:
            logger.error("❌ 上传失败或验证不通过！")

        # 方法2: 使用详细的上传信息
        upload_info = upload_result.get_upload_info()
        logger.info(f"上传详细信息: {upload_info}")

        # 方法3: 手动验证特定条件
        if (upload_result.verified and
            upload_result.file_url and
            upload_result.file_size and upload_result.file_size > 0):
            logger.info("✅ 手动验证通过：文件已成功上传并验证")
        else:
            logger.warning("⚠️ 手动验证未通过")

        # 方法4: 使用通用验证工具
        verification_result = UploadVerificationUtil.is_upload_successful(
            upload_result,
            expected_size=upload_result.file_size,
            expected_md5=upload_result.md5
        )

        if verification_result:
            logger.info("✅ 通用验证工具确认上传成功")
        else:
            logger.error("❌ 通用验证工具确认上传失败")

        return upload_result

    except Exception as e:
        logger.error(f"上传文件时发生错误: {str(e)}")
        return None

# 批量上传验证示例
async def batch_upload_with_verification_example():
    """
    批量上传文件并统计成功率的示例
    """
    storage_service = get_enhanced_storage_service()
    file_paths = ["/path/to/file1.txt", "/path/to/file2.jpg", "/path/to/file3.pdf"]

    successful_uploads = 0
    failed_uploads = 0
    upload_results = []

    for file_path in file_paths:
        try:
            result = await storage_service.upload_file_enhanced(file_path)
            upload_results.append(result)

            if result.is_upload_successful():
                successful_uploads += 1
                logger.info(f"✅ 文件 {file_path} 上传成功")
            else:
                failed_uploads += 1
                logger.error(f"❌ 文件 {file_path} 上传失败")

        except Exception as e:
            failed_uploads += 1
            logger.error(f"❌ 文件 {file_path} 上传异常: {str(e)}")

    # 统计结果
    total_files = len(file_paths)
    success_rate = (successful_uploads / total_files) * 100 if total_files > 0 else 0

    logger.info(f"📊 批量上传统计:")
    logger.info(f"   总文件数: {total_files}")
    logger.info(f"   成功: {successful_uploads}")
    logger.info(f"   失败: {failed_uploads}")
    logger.info(f"   成功率: {success_rate:.2f}%")

    return upload_results
