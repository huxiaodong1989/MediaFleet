import asyncio
import os
import ffmpeg
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class GetVideoInfo:

    async def get_video_info(self, file_path: str) -> Dict[str, Any]:
        """
        获取文件信息（时长、大小、分辨率、格式、帧率、音频质量等）

        :param file_path: 文件路径
        :return: 文件信息字典
        """
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                logger.warning(f"文件不存在: {file_path}")
                return {"duration": 0, "size": 0}

            # 检查文件权限
            if not os.access(file_path, os.R_OK):
                logger.error(f"没有文件读取权限: {file_path}")
                return {"duration": 0, "size": 0}

            # 获取文件大小
            try:
                file_size = os.path.getsize(file_path)
                logger.debug(f"文件大小: {file_size} bytes, 路径: {file_path}")
            except Exception as e:
                logger.error(f"获取文件大小失败: {file_path}, 错误: {str(e)}")
                return {"duration": 0, "size": 0}

            # 初始化结果
            result = {
                "duration": 0,
                "size": file_size,
                "width": 0,
                "height": 0,
                "video_codec": "",
                "video_bitrate": 0,
                "frame_rate": 0.0,
                "audio_codec": "",
                "audio_bitrate": 0,
                "audio_sample_rate": 0,
                "audio_channels": 0
            }

            try:
                # 使用ffmpeg-python获取详细视频信息
                probe = await asyncio.to_thread(ffmpeg.probe, file_path)

                # 获取基本时长信息
                duration = float(probe['format']['duration'])
                result["duration"] = duration
                logger.debug(f"获取到视频时长: {duration}秒, 文件: {file_path}")

                # 获取视频流信息
                video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                if video_stream:
                    result["width"] = int(video_stream.get('width', 0))
                    result["height"] = int(video_stream.get('height', 0))
                    result["video_codec"] = video_stream.get('codec_name', '')

                    # 获取视频比特率
                    video_bitrate = video_stream.get('bit_rate')
                    if video_bitrate:
                        result["video_bitrate"] = int(video_bitrate)

                    # 获取帧率
                    r_frame_rate = video_stream.get('r_frame_rate', '0/1')
                    if r_frame_rate and '/' in r_frame_rate:
                        try:
                            numerator, denominator = map(int, r_frame_rate.split('/'))
                            if denominator > 0:
                                result["frame_rate"] = round(numerator / denominator, 2)
                        except (ValueError, ZeroDivisionError):
                            # 尝试avg_frame_rate
                            avg_frame_rate = video_stream.get('avg_frame_rate', '0/1')
                            if avg_frame_rate and '/' in avg_frame_rate:
                                try:
                                    numerator, denominator = map(int, avg_frame_rate.split('/'))
                                    if denominator > 0:
                                        result["frame_rate"] = round(numerator / denominator, 2)
                                except (ValueError, ZeroDivisionError):
                                    pass

                # 获取音频流信息
                audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
                if audio_stream:
                    result["audio_codec"] = audio_stream.get('codec_name', '')

                    # 获取音频比特率
                    audio_bitrate = audio_stream.get('bit_rate')
                    if audio_bitrate:
                        result["audio_bitrate"] = int(audio_bitrate)

                    # 获取采样率
                    sample_rate = audio_stream.get('sample_rate')
                    if sample_rate:
                        result["audio_sample_rate"] = int(sample_rate)

                    # 获取声道数
                    channels = audio_stream.get('channels')
                    if channels:
                        result["audio_channels"] = int(channels)

                logger.debug(f"获取到完整视频信息: {file_path}")
                logger.debug(f"  - 分辨率: {result['width']}x{result['height']}")
                logger.debug(f"  - 视频编码: {result['video_codec']}, 帧率: {result['frame_rate']}fps")
                logger.debug(f"  - 音频编码: {result['audio_codec']}, 采样率: {result['audio_sample_rate']}Hz, 声道: {result['audio_channels']}")

            except Exception as e:
                logger.error(f"获取视频详细信息失败: {str(e)}")
                # 保留基本的时长和大小信息
                try:
                    probe = await asyncio.to_thread(ffmpeg.probe, file_path)
                    duration = float(probe['format']['duration'])
                    result["duration"] = duration
                except Exception as duration_error:
                    logger.error(f"获取视频时长也失败: {str(duration_error)}")

            return result

        except Exception as e:
            logger.error(f"获取文件信息失败: {file_path}, 错误: {str(e)}")
            return {"duration": 0, "size": 0}
