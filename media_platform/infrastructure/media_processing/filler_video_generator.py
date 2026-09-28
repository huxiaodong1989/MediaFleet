import logging
import asyncio
import os
import json
import platform
import subprocess
from typing import Dict, Any, Optional
import ffmpeg

logger = logging.getLogger("filler_video_generator")

class FillerVideoGenerator:
    """
    补帧视频生成器
    用于在视频片段之间生成参数完全匹配的空白视频（黑屏+静音）
    """

    def __init__(self):
        """初始化补帧视频生成器"""
        self.is_windows = platform.system() == 'Windows'
        self.is_docker = os.path.exists('/.dockerenv')
        logger.info(f"补帧视频生成器初始化 - 系统: {platform.system()}, Docker: {self.is_docker}")

    async def get_video_parameters(self, video_path: str) -> Optional[Dict[str, Any]]:
        """
        获取视频的详细参数

        :param video_path: 视频文件路径
        :return: 视频参数字典，失败返回None
        """
        try:
            if not os.path.exists(video_path):
                logger.error(f"视频文件不存在: {video_path}")
                return None

            logger.info(f"开始获取视频参数: {video_path}")

            # 使用ffprobe获取视频信息
            if self.is_windows:
                # Windows环境使用subprocess
                cmd = [
                    "ffprobe",
                    "-v", "error",
                    "-show_streams",
                    "-of", "json",
                    video_path
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    logger.error(f"ffprobe失败: {result.stderr}")
                    return None

                probe_data = json.loads(result.stdout)
            else:
                # 非Windows环境使用ffmpeg-python
                probe_data = await asyncio.to_thread(
                    ffmpeg.probe, video_path,
                    v='error', show_streams=None, of='json'
                )

            # 解析视频流和音频流信息
            video_stream = None
            audio_stream = None

            for stream in probe_data.get('streams', []):
                if stream.get('codec_type') == 'video' and video_stream is None:
                    video_stream = stream
                elif stream.get('codec_type') == 'audio' and audio_stream is None:
                    audio_stream = stream

            if not video_stream:
                logger.error(f"未找到视频流: {video_path}")
                return None

            # 解析视频参数
            width = int(video_stream.get('width', 0))
            height = int(video_stream.get('height', 0))

            # 解析帧率
            r_frame_rate = video_stream.get('r_frame_rate', '25/1')
            try:
                if '/' in r_frame_rate:
                    num, den = map(int, r_frame_rate.split('/'))
                    fps = num / den if den != 0 else 25.0
                else:
                    fps = float(r_frame_rate)
            except (ValueError, ZeroDivisionError):
                fps = 25.0

            # 视频编码信息
            video_codec = video_stream.get('codec_name', 'h264')
            pix_fmt = video_stream.get('pix_fmt', 'yuv420p')

            # 音频参数
            audio_params = None
            if audio_stream:
                sample_rate = int(audio_stream.get('sample_rate', 44100))
                channels = int(audio_stream.get('channels', 2))
                audio_codec = audio_stream.get('codec_name', 'aac')

                # 解析声道布局
                channel_layout = audio_stream.get('channel_layout', 'stereo')
                if channels == 1:
                    channel_layout = 'mono'
                elif channels == 2:
                    channel_layout = 'stereo'
                elif channels == 6:
                    channel_layout = '5.1'

                audio_params = {
                    'sample_rate': sample_rate,
                    'channels': channels,
                    'channel_layout': channel_layout,
                    'codec_name': audio_codec
                }

            video_params = {
                'width': width,
                'height': height,
                'fps': fps,
                'video_codec': video_codec,
                'pix_fmt': pix_fmt,
                'audio_params': audio_params,
                'has_audio': audio_params is not None
            }

            logger.info(f"视频参数获取成功: {video_params}")
            return video_params

        except Exception as e:
            logger.error(f"获取视频参数异常: {str(e)}", exc_info=True)
            return None

    async def create_filler_video(self, source_video_path: str, duration: float,
                                 output_path: str) -> bool:
        """
        根据源视频参数创建完全匹配的补帧视频

        :param source_video_path: 源视频路径
        :param duration: 补帧时长（秒）
        :param output_path: 输出文件路径
        :return: 是否成功
        """
        try:
            # 参数验证
            if duration <= 0 or duration > 3600:
                logger.error(f"补帧时长异常: {duration}秒")
                return False

            # 精确控制时长
            duration = round(duration, 3)
            logger.info(f"开始生成补帧视频: 时长={duration}秒, 输出={output_path}")

            # 获取源视频参数
            video_params = await self.get_video_parameters(source_video_path)
            if not video_params:
                logger.error(f"无法获取源视频参数: {source_video_path}")
                return False

            # 构建ffmpeg命令
            success = await self._generate_filler_video_with_params(
                video_params, duration, output_path
            )

            if success:
                # 验证生成的视频
                return await self._verify_filler_video(output_path, duration, video_params)
            else:
                return False

        except Exception as e:
            logger.error(f"创建补帧视频异常: {str(e)}", exc_info=True)
            return False

    async def _generate_filler_video_with_params(self, video_params: Dict[str, Any],
                                               duration: float, output_path: str) -> bool:
        """
        使用精确参数生成补帧视频

        :param video_params: 视频参数
        :param duration: 时长
        :param output_path: 输出路径
        :return: 是否成功
        """
        try:
            # 提取参数 - 严格按照源视频参数，不做任何调整
            width = video_params['width']
            height = video_params['height']
            fps = video_params['fps']
            video_codec = video_params['video_codec']
            pix_fmt = video_params['pix_fmt']
            has_audio = video_params['has_audio']
            audio_params = video_params.get('audio_params')

            logger.info(f"严格按照源视频参数生成补帧视频: {width}x{height}@{fps}fps, 编码:{video_codec}, 时长:{duration}秒")

            # 构建命令
            cmd = ["ffmpeg"]

            # 添加视频输入源（黑屏）- 精确控制时长
            cmd.extend([
                "-f", "lavfi",
                "-i", f"color=black:s={width}x{height}:r={fps}:d={duration}"
            ])

            # 如果有音频，添加音频输入源（静音）- 精确控制时长
            if has_audio and audio_params:
                sample_rate = audio_params['sample_rate']
                channel_layout = audio_params['channel_layout']

                cmd.extend([
                    "-f", "lavfi",
                    "-i", f"anullsrc=r={sample_rate}:cl={channel_layout}:d={duration}"
                ])

            # 添加编码参数 - 严格匹配源视频
            # 视频编码参数
            if video_codec in ['h264', 'libx264']:
                cmd.extend(["-c:v", "libx264"])
            elif video_codec in ['h265', 'hevc', 'libx265']:
                cmd.extend(["-c:v", "libx265"])
            else:
                cmd.extend(["-c:v", "libx264"])  # 默认使用h264

            # 像素格式 - 严格匹配
            cmd.extend(["-pix_fmt", pix_fmt])

            # 编码参数 - 为了快速生成，使用较快的预设但保证兼容性
            cmd.extend([
                "-preset", "ultrafast",
                "-crf", "30",
                "-r", str(fps),  # 确保输出帧率匹配
                "-video_track_timescale", "1000"  # 确保时间戳精确
            ])

            # 音频编码参数 - 严格匹配源视频
            if has_audio and audio_params:
                audio_codec = audio_params.get('codec_name', 'aac')
                sample_rate = audio_params['sample_rate']
                channels = audio_params['channels']

                if audio_codec in ['aac', 'libfdk_aac']:
                    cmd.extend(["-c:a", "aac"])
                elif audio_codec in ['mp3', 'libmp3lame']:
                    cmd.extend(["-c:a", "libmp3lame"])
                else:
                    cmd.extend(["-c:a", "aac"])  # 默认使用aac

                # 音频参数 - 严格匹配
                cmd.extend([
                    "-ar", str(sample_rate),
                    "-ac", str(channels),
                    "-b:a", "128k"
                ])

                cmd.extend(["-shortest"])

            # 其他重要参数 - 确保时长精确
            cmd.extend([
                "-t", str(duration),  # 严格限制输出时长
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart",
                "-y", output_path
            ])

            # 执行命令
            logger.info(f"执行补帧视频生成命令: {' '.join(cmd)}")

            # 设置超时时间：基于时长动态计算，每秒给10秒处理时间，最少60秒
            timeout_seconds = max(60, int(duration * 10))

            if self.is_windows:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout_seconds
                )

                if result.returncode == 0:
                    logger.info(f"补帧视频生成成功: {output_path}")
                    return True
                else:
                    logger.error(f"补帧视频生成失败: {result.stderr}")
                    return False
            else:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )

                if process.returncode == 0:
                    logger.info(f"补帧视频生成成功: {output_path}")
                    return True
                else:
                    error_msg = stderr.decode() if stderr else "Unknown error"
                    logger.error(f"补帧视频生成失败: {error_msg}")
                    return False

        except asyncio.TimeoutError:
            logger.error(f"补帧视频生成超时 (超时时间: {timeout_seconds}秒)")
            return False
        except Exception as e:
            logger.error(f"补帧视频生成异常: {str(e)}", exc_info=True)
            return False

    async def _verify_filler_video(self, video_path: str, expected_duration: float,
                                  expected_params: Dict[str, Any]) -> bool:
        """
        验证生成的补帧视频

        :param video_path: 视频路径
        :param expected_duration: 预期时长
        :param expected_params: 预期参数
        :return: 是否验证通过
        """
        try:
            if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
                logger.error(f"补帧视频文件无效: {video_path}")
                return False

            # 获取文件大小
            file_size = os.path.getsize(video_path)

            # 检查文件大小是否合理（每秒不超过500KB）- 放宽限制确保兼容性
            max_size_per_second = 500 * 1024  # 500KB/秒
            max_allowed_size = expected_duration * max_size_per_second

            if file_size > max_allowed_size:
                logger.warning(f"补帧视频文件较大: {file_size/(1024*1024):.1f}MB, 预期最大: {max_allowed_size/(1024*1024):.1f}MB")
                # 只是警告，不阻止使用

            # 验证视频参数
            actual_params = await self.get_video_parameters(video_path)
            if not actual_params:
                logger.error(f"无法获取补帧视频参数: {video_path}")
                return False

            # 验证时长
            if self.is_windows:
                cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=noprint_wrappers=1:nokey=1", video_path]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    try:
                        actual_duration = float(result.stdout.strip())
                    except ValueError:
                        actual_duration = 0
                else:
                    actual_duration = 0
            else:
                try:
                    probe = await asyncio.to_thread(ffmpeg.probe, video_path)
                    actual_duration = float(probe['format']['duration'])
                except:
                    actual_duration = 0

            # 时长验证 - 严格验证
            tolerance = max(0.1, expected_duration * 0.02)  # ±0.1秒或±2%，更严格
            duration_diff = abs(actual_duration - expected_duration)

            if duration_diff > tolerance:
                logger.error(f"补帧视频时长验证失败: 预期={expected_duration}秒, 实际={actual_duration}秒, 差异={duration_diff}秒, 允许误差={tolerance}秒")
                return False

            # 参数验证 - 严格检查关键参数
            width_match = actual_params['width'] == expected_params['width']
            height_match = actual_params['height'] == expected_params['height']
            fps_diff = abs(actual_params['fps'] - expected_params['fps'])
            audio_match = actual_params['has_audio'] == expected_params['has_audio']

            if not width_match or not height_match:
                logger.error(f"补帧视频分辨率不匹配: 预期={expected_params['width']}x{expected_params['height']}, "
                           f"实际={actual_params['width']}x{actual_params['height']}")
                return False

            if fps_diff > 1.0:  # 允许1fps误差
                logger.error(f"补帧视频帧率不匹配: 预期={expected_params['fps']}fps, 实际={actual_params['fps']}fps")
                return False

            if not audio_match:
                logger.error(f"补帧视频音频配置不匹配: 预期音频={expected_params['has_audio']}, 实际音频={actual_params['has_audio']}")
                return False

            # 如果有音频，验证音频参数
            if expected_params['has_audio'] and actual_params['has_audio']:
                expected_audio = expected_params.get('audio_params', {})
                actual_audio = actual_params.get('audio_params', {})

                if expected_audio and actual_audio:
                    sample_rate_diff = abs(expected_audio.get('sample_rate', 0) - actual_audio.get('sample_rate', 0))
                    channels_match = expected_audio.get('channels', 0) == actual_audio.get('channels', 0)

                    if sample_rate_diff > 1000:  # 允许1kHz误差
                        logger.warning(f"补帧视频音频采样率差异较大: 预期={expected_audio.get('sample_rate')}Hz, 实际={actual_audio.get('sample_rate')}Hz")

                    if not channels_match:
                        logger.warning(f"补帧视频音频声道数不匹配: 预期={expected_audio.get('channels')}, 实际={actual_audio.get('channels')}")

            logger.info(f"补帧视频验证通过: 时长={actual_duration:.3f}秒(差异{duration_diff:.3f}秒), "
                       f"分辨率={actual_params['width']}x{actual_params['height']}, "
                       f"帧率={actual_params['fps']:.2f}fps, 音频={actual_params['has_audio']}, "
                       f"文件大小={file_size/(1024*1024):.1f}MB")
            return True

        except Exception as e:
            logger.error(f"验证补帧视频异常: {str(e)}", exc_info=True)
            return False

    async def create_compatible_filler(self, source_video_path: str, duration: float,
                                     output_dir: str, output_name: str) -> Optional[str]:
        """
        创建与源视频兼容的补帧视频（主要接口方法）

        :param source_video_path: 源视频路径
        :param duration: 补帧时长
        :param output_dir: 输出目录
        :param output_name: 输出文件名
        :return: 补帧视频路径，失败返回None
        """
        try:
            # 确保输出目录存在
            os.makedirs(output_dir, exist_ok=True)

            output_path = os.path.join(output_dir, output_name)

            # 创建补帧视频
            success = await self.create_filler_video(source_video_path, duration, output_path)

            if success and os.path.exists(output_path):
                logger.info(f"补帧视频创建成功: {output_path}")
                return output_path
            else:
                logger.error(f"补帧视频创建失败")
                return None

        except Exception as e:
            logger.error(f"创建兼容补帧视频异常: {str(e)}", exc_info=True)
            return None
