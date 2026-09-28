import asyncio
import os
import ffmpeg
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class GetAudioInfo:
    """音频信息获取工具类"""

    async def get_audio_info(self, file_path: str) -> Dict[str, Any]:
        """
        获取音频文件信息（时长、大小、编码、采样率、声道数等）

        :param file_path: 音频文件路径
        :return: 音频文件信息字典
        """
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.warning(f"音频文件不存在: {file_path}")
                return {"duration": 0, "file_size": 0}

            # 检查文件权限
            if not os.access(file_path, os.R_OK):
                logger.error(f"没有音频文件读取权限: {file_path}")
                return {"duration": 0, "file_size": 0}

            # 获取文件大小
            try:
                file_size = os.path.getsize(file_path)
                logger.debug(f"音频文件大小: {file_size} bytes, 路径: {file_path}")
            except Exception as e:
                logger.error(f"获取音频文件大小失败: {file_path}, 错误: {str(e)}")
                return {"duration": 0, "file_size": 0}

            # 初始化结果
            result = {
                "duration": 0,
                "file_size": file_size,
                "audio": {
                    "codec": "",
                    "bitrate": 0,
                    "sample_rate": 0,
                    "channels": 0,
                    "channel_layout": ""
                }
            }

            try:
                # 使用ffmpeg-python获取详细音频信息
                probe = await asyncio.to_thread(ffmpeg.probe, file_path)

                # 获取基本时长信息
                duration = float(probe['format']['duration'])
                result["duration"] = round(duration, 2)
                logger.debug(f"获取到音频时长: {duration}秒, 文件: {file_path}")

                # 获取音频流信息
                audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
                if audio_stream:
                    result["audio"]["codec"] = audio_stream.get('codec_name', '')

                    # 获取音频比特率
                    audio_bitrate = audio_stream.get('bit_rate')
                    if audio_bitrate:
                        result["audio"]["bitrate"] = int(audio_bitrate)

                    # 获取采样率
                    sample_rate = audio_stream.get('sample_rate')
                    if sample_rate:
                        result["audio"]["sample_rate"] = int(sample_rate)

                    # 获取声道数
                    channels = audio_stream.get('channels')
                    if channels:
                        result["audio"]["channels"] = int(channels)

                    # 获取声道布局
                    channel_layout = audio_stream.get('channel_layout', '')
                    result["audio"]["channel_layout"] = channel_layout

                logger.debug(f"获取到完整音频信息: {file_path}")
                logger.debug(f"  - 时长: {result['duration']}秒")
                logger.debug(f"  - 文件大小: {result['file_size']} bytes")
                logger.debug(f"  - 音频编码: {result['audio']['codec']}")
                logger.debug(f"  - 比特率: {result['audio']['bitrate']} bps")
                logger.debug(f"  - 采样率: {result['audio']['sample_rate']} Hz")
                logger.debug(f"  - 声道数: {result['audio']['channels']}")
                logger.debug(f"  - 声道布局: {result['audio']['channel_layout']}")

            except Exception as e:
                logger.error(f"获取音频详细信息失败: {str(e)}")
                # 保留基本的时长和大小信息
                try:
                    probe = await asyncio.to_thread(ffmpeg.probe, file_path)
                    duration = float(probe['format']['duration'])
                    result["duration"] = round(duration, 2)
                except Exception as duration_error:
                    logger.error(f"获取音频时长也失败: {str(duration_error)}")

            return result

        except Exception as e:
            logger.error(f"获取音频文件信息失败: {file_path}, 错误: {str(e)}")
            return {"duration": 0, "file_size": 0}
