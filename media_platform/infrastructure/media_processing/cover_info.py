import asyncio
import os
from typing import Dict, Any
import logging

# 尝试导入PIL，如果失败则使用备用方案
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("PIL/Pillow未安装，将使用ffmpeg作为备用方案获取图片信息")

logger = logging.getLogger(__name__)

class GetCoverInfo:
    """封面（图片）信息获取工具类"""

    async def get_cover_info(self, file_path: str) -> Dict[str, Any]:
        """
        获取封面文件信息（文件大小、分辨率、图片格式等）

        :param file_path: 封面文件路径
        :return: 封面文件信息字典
        """
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.warning(f"封面文件不存在: {file_path}")
                return {"file_size": 0, "width": 0, "height": 0}

            # 检查文件权限
            if not os.access(file_path, os.R_OK):
                logger.error(f"没有封面文件读取权限: {file_path}")
                return {"file_size": 0, "width": 0, "height": 0}

            # 获取文件大小
            try:
                file_size = os.path.getsize(file_path)
                logger.debug(f"封面文件大小: {file_size} bytes, 路径: {file_path}")
            except Exception as e:
                logger.error(f"获取封面文件大小失败: {file_path}, 错误: {str(e)}")
                return {"file_size": 0, "width": 0, "height": 0}

            # 初始化结果
            result = {
                "file_size": file_size,
                "width": 0,
                "height": 0,
                "format": "",
                "mode": ""
            }

            try:
                # 使用PIL获取图片信息
                if PIL_AVAILABLE:
                    with Image.open(file_path) as img:
                        result["width"] = img.size[0]
                        result["height"] = img.size[1]
                        result["format"] = img.format or ""
                        result["mode"] = img.mode or ""

                    logger.debug(f"获取到完整封面信息: {file_path}")
                    logger.debug(f"  - 文件大小: {result['file_size']} bytes")
                    logger.debug(f"  - 分辨率: {result['width']}x{result['height']}")
                    logger.debug(f"  - 格式: {result['format']}")
                    logger.debug(f"  - 模式: {result['mode']}")
                else:
                    # PIL不可用，直接使用ffmpeg
                    raise ImportError("PIL not available, using ffmpeg")

            except Exception as e:
                logger.error(f"获取封面详细信息失败: {str(e)}")
                # 如果PIL失败，尝试其他方法或返回基本信息
                try:
                    # 尝试使用ffprobe获取图片信息（适用于一些特殊格式）
                    import ffmpeg
                    probe = await asyncio.to_thread(ffmpeg.probe, file_path)

                    # 查找视频流（对于图片文件，会被认为是单帧视频）
                    video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                    if video_stream:
                        result["width"] = int(video_stream.get('width', 0))
                        result["height"] = int(video_stream.get('height', 0))
                        result["format"] = video_stream.get('codec_name', '')

                        logger.debug(f"通过ffprobe获取封面信息: {result['width']}x{result['height']}")

                except Exception as ffmpeg_error:
                    logger.error(f"使用ffprobe获取封面信息也失败: {str(ffmpeg_error)}")

            return result

        except Exception as e:
            logger.error(f"获取封面文件信息失败: {file_path}, 错误: {str(e)}")
            return {"file_size": 0, "width": 0, "height": 0}

    async def get_cover_info_from_url(self, file_url: str, local_path: str = None) -> Dict[str, Any]:
        """
        从URL或本地路径获取封面文件信息

        :param file_url: 封面文件URL
        :param local_path: 本地文件路径（如果有）
        :return: 封面文件信息字典
        """
        try:
            # 如果有本地路径，优先使用本地路径
            if local_path and os.path.exists(local_path):
                return await self.get_cover_info(local_path)

            # 如果没有本地路径，尝试从URL下载临时文件获取信息
            if file_url:
                import httpx
                import tempfile

                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.get(file_url)
                        if response.status_code == 200:
                            # 创建临时文件
                            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
                                temp_file.write(response.content)
                                temp_path = temp_file.name

                            try:
                                # 获取图片信息
                                result = await self.get_cover_info(temp_path)
                                return result
                            finally:
                                # 清理临时文件
                                try:
                                    os.unlink(temp_path)
                                except Exception:
                                    pass
                        else:
                            logger.warning(f"下载封面文件失败: {file_url}, 状态码: {response.status_code}")

                except Exception as e:
                    logger.error(f"从URL获取封面信息失败: {file_url}, 错误: {str(e)}")

            return {"file_size": 0, "width": 0, "height": 0}

        except Exception as e:
            logger.error(f"从URL获取封面文件信息失败: {file_url}, 错误: {str(e)}")
            return {"file_size": 0, "width": 0, "height": 0}
