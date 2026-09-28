import os
import platform
import asyncio
import subprocess
from typing import Dict, Any, Optional
from datetime import datetime
import logging

from media_platform.common.config import get_settings
from media_platform.infrastructure.media_processing.cover_types import (
    CoverExtractStrategy,
    CoverTextConfig,
)

logger = logging.getLogger(__name__)


class CoverExtractor:
    """
    封面提取服务

    支持两种提取引擎：
    1. OpenCV（推荐）：高性能，10-50ms，无进程开销
    2. FFmpeg（后备）：兼容性强，200-500ms

    通过环境变量 USE_OPENCV_EXTRACTOR 控制（默认: true）
    """

    # 类级别的并发控制信号量
    # 根据Docker容器配置（1核CPU + 2GB内存），建议2-3个并发
    # 可通过环境变量 COVER_EXTRACTION_CONCURRENCY 配置，默认3
    _extraction_concurrency = int(os.getenv("COVER_EXTRACTION_CONCURRENCY", "3"))
    _extraction_semaphore = asyncio.Semaphore(
        _extraction_concurrency
    )
    # ffmpeg命令执行超时时间（秒）
    FFMPEG_TIMEOUT = 30

    def __init__(self):
        # 优先检查Docker容器内的绝对路径
        if os.path.exists("/app/resource/images/bg.png"):
            self.bg_image_path = "/app/resource/images/bg.png"
        else:
            self.bg_image_path = "resource/images/bg.png"
        self.font_path = self._get_font_path()

        # 初始化提取引擎
        self._init_extraction_engine()

    def _init_extraction_engine(self):
        """初始化封面提取引擎"""
        # 从配置获取引擎选择
        settings = get_settings()
        self.use_opencv = settings.USE_OPENCV_EXTRACTOR

        if self.use_opencv:
            try:
                from media_platform.infrastructure.media_processing.opencv_cover_extractor import (
                    OpenCVCoverExtractor,
                )
                self.opencv_extractor = OpenCVCoverExtractor()
                logger.info(
                    f"✓ 使用 OpenCV 封面提取引擎（高性能模式）"
                    f" - 并发数: {settings.COVER_EXTRACTION_CONCURRENCY}"
                )
            except Exception as e:
                logger.warning(f"OpenCV 引擎初始化失败，降级到 FFmpeg: {str(e)}")
                self.use_opencv = False
                self.opencv_extractor = None
                logger.info("✓ 使用 FFmpeg 封面提取引擎（兼容模式）")
        else:
            self.opencv_extractor = None
            logger.info("✓ 使用 FFmpeg 封面提取引擎（兼容模式）")

    def _get_font_path(self) -> str:
        """获取合适的字体文件路径"""
        system = platform.system().lower()

        # 首先检查Docker容器内的绝对路径
        if os.path.exists("/app/resource/fonts/PingFang.ttc"):
            return "/app/resource/fonts/PingFang.ttc"

        if "windows" in system:
            configs = CoverTextConfig.FONT_CONFIGS["windows"]
        else:
            configs = CoverTextConfig.FONT_CONFIGS["linux"]

        # 优先使用项目内置字体
        primary_font = configs["primary"]
        if os.path.exists(primary_font):
            return primary_font

        # 尝试系统字体
        for font_path in configs["fallback"]:
            if os.path.exists(font_path):
                return font_path

        # 如果都找不到，使用默认字体
        logger.warning("未找到合适的字体文件，使用系统默认字体")
        return "arial.ttf" if "windows" in system else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    async def extract_cover(self,
                          video_path: str,
                          strategy: str,
                          cover_info: Dict[str, Any],
                          output_dir: str,
                          start_time: Optional[str] = None,
                          end_time: Optional[str] = None) -> Optional[str]:
        """
        提取视频封面

        Args:
            video_path: 视频文件路径
            strategy: 提取策略 (timestamp/custom)
            cover_info: 封面相关信息
            output_dir: 输出目录
            start_time: 录制开始时间（用于自定义封面生成class_time）
            end_time: 录制结束时间（用于自定义封面生成class_time）

        Returns:
            封面文件路径
        """
        try:
            if strategy == CoverExtractStrategy.TIMESTAMP.value:
                # 时间戳提取：优先使用 OpenCV（性能更好）
                if self.use_opencv and self.opencv_extractor:
                    logger.info("使用 OpenCV 引擎提取时间戳封面")
                    try:
                        result = await self.opencv_extractor.extract_timestamp_cover(
                            video_path, cover_info, output_dir
                        )
                        if result:
                            return result
                        else:
                            logger.warning("OpenCV 提取失败，尝试降级到 FFmpeg")
                    except Exception as opencv_error:
                        logger.error(f"OpenCV 提取异常: {str(opencv_error)}", exc_info=True)
                        logger.warning("降级到 FFmpeg 引擎")

                # FFmpeg 后备方案或主方案
                logger.info("使用 FFmpeg 引擎提取时间戳封面")
                return await self._extract_timestamp_cover_ffmpeg(video_path, cover_info, output_dir)

            elif strategy == CoverExtractStrategy.CUSTOM.value:
                # 自定义封面仍使用 FFmpeg（需要绘制文字）
                return await self._generate_custom_cover(cover_info, output_dir, start_time, end_time)
            else:
                logger.error(f"不支持的封面提取策略: {strategy}")
                return None

        except Exception as e:
            logger.error(f"提取封面失败: {str(e)}", exc_info=True)
            return None

    async def _extract_timestamp_cover_ffmpeg(self,
                                             video_path: str,
                                             cover_info: Dict[str, Any],
                                             output_dir: str) -> Optional[str]:
        """
        根据时间戳提取封面（FFmpeg 实现）

        Args:
            video_path: 视频文件路径
            cover_info: 封面信息，包含 timestamp (秒)
            output_dir: 输出目录

        Returns:
            封面文件路径
        """
        logger.info(f"开始时间戳封面提取 - 视频路径: {video_path}")
        logger.info(f"封面信息: {cover_info}")
        logger.info(f"输出目录: {output_dir}")

        # 获取并验证timestamp
        timestamp = cover_info.get("timestamp", 60)  # 默认第一分钟
        logger.info(f"提取时间点: {timestamp} (类型: {type(timestamp).__name__})")

        # 确保timestamp是数字类型
        if isinstance(timestamp, str):
            try:
                timestamp = float(timestamp)
                logger.info(f"时间戳字符串转换为数字: {timestamp}")
            except (ValueError, TypeError) as e:
                logger.error(f"无法将时间戳转换为数字: {timestamp}, 错误: {str(e)}")
                return None

        if not isinstance(timestamp, (int, float)) or timestamp < 0:
            logger.error(f"无效的时间戳值: {timestamp}")
            return None

        # 将数字时间戳转换为hh:mm:ss格式
        def seconds_to_timestamp(seconds):
            """将秒数转换为hh:mm:ss格式"""
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            seconds = int(seconds % 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        timestamp_str = seconds_to_timestamp(timestamp)
        logger.info(f"时间戳转换为ffmpeg格式: {timestamp} 秒 -> {timestamp_str}")

        # 检查视频文件是否存在
        if not os.path.exists(video_path):
            logger.error(f"视频文件不存在: {video_path}")
            return None
        else:
            video_size = os.path.getsize(video_path)
            logger.info(f"视频文件存在，大小: {video_size} bytes")

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)
        logger.info(f"输出目录已创建: {output_dir}")

        # 生成输出文件名
        output_file = os.path.join(output_dir, f"cover_timestamp_{int(timestamp)}.jpg")
        logger.info(f"封面输出文件: {output_file}")

        # 构建ffmpeg命令 - 提取关键帧
        cmd = [
            "ffmpeg", "-y",  # -y 覆盖已存在文件
            "-i", video_path,
            "-ss", timestamp_str,  # 使用hh:mm:ss格式的时间戳
            "-vframes", "1",  # 只输出一帧
            "-q:v", "2",  # 高质量
            output_file
        ]

        logger.info(f"构建的ffmpeg命令: {' '.join(cmd)}")

        # 检测操作系统
        is_windows = platform.system() == 'Windows'
        logger.info(f"检测到操作系统: {platform.system()}")

        # 使用信号量控制并发
        async with self._extraction_semaphore:
            # 避免访问私有属性，直接记录日志
            logger.info(f"获得封面提取信号量，开始提取封面")

            try:
                # 执行ffmpeg命令
                logger.info("开始执行ffmpeg命令...")

                if is_windows:
                    # Windows环境使用subprocess避免asyncio的NotImplementedError问题
                    import subprocess
                    logger.info("Windows环境使用subprocess执行ffmpeg命令")

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=self.FFMPEG_TIMEOUT
                    )

                    logger.info(f"ffmpeg命令执行完成，返回码: {result.returncode}")

                    if result.stdout:
                        logger.debug(f"ffmpeg标准输出: {result.stdout}")

                    if result.stderr:
                        logger.debug(f"ffmpeg错误输出: {result.stderr}")

                    # 检查输出文件
                    file_exists = os.path.exists(output_file)
                    file_size = os.path.getsize(output_file) if file_exists else 0

                    logger.info(f"输出文件检查 - 存在: {file_exists}, 大小: {file_size} bytes")

                    if result.returncode == 0 and file_exists and file_size > 0:
                        logger.info(f"时间戳封面提取成功: {output_file}")
                        return output_file
                    else:
                        logger.error(f"时间戳封面提取失败:")
                        logger.error(f"  - 返回码: {result.returncode}")
                        logger.error(f"  - 文件存在: {file_exists}")
                        logger.error(f"  - 文件大小: {file_size} bytes")
                        if result.stderr:
                            logger.error(f"  - FFmpeg错误: {result.stderr}")
                        return None
                else:
                    # 非Windows环境使用asyncio，添加超时控制
                    logger.info("非Windows环境使用asyncio执行ffmpeg命令")

                    process = None
                    try:
                        process = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )

                        logger.info(f"FFmpeg进程已启动，PID: {process.pid}")

                        # 使用asyncio.wait_for添加超时控制
                        try:
                            stdout, stderr = await asyncio.wait_for(
                                process.communicate(),
                                timeout=self.FFMPEG_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            logger.error(f"ffmpeg命令执行超时（{self.FFMPEG_TIMEOUT}秒），正在终止进程...")

                            # 确保进程被清理
                            try:
                                process.terminate()
                                await asyncio.wait_for(process.wait(), timeout=5.0)
                            except asyncio.TimeoutError:
                                logger.warning("进程无法在5秒内终止，强制kill")
                                process.kill()
                                await process.wait()

                            logger.error("ffmpeg命令执行超时，进程已终止")
                            return None

                        logger.info(f"ffmpeg命令执行完成，返回码: {process.returncode}")

                        if stdout:
                            logger.debug(f"ffmpeg标准输出: {stdout.decode()}")

                        if stderr:
                            logger.debug(f"ffmpeg错误输出: {stderr.decode()}")

                        # 检查输出文件
                        file_exists = os.path.exists(output_file)
                        file_size = os.path.getsize(output_file) if file_exists else 0

                        logger.info(f"输出文件检查 - 存在: {file_exists}, 大小: {file_size} bytes")

                        if process.returncode == 0 and file_exists and file_size > 0:
                            logger.info(f"时间戳封面提取成功: {output_file}")
                            return output_file
                        else:
                            logger.error(f"时间戳封面提取失败:")
                            logger.error(f"  - 返回码: {process.returncode}")
                            logger.error(f"  - 文件存在: {file_exists}")
                            logger.error(f"  - 文件大小: {file_size} bytes")
                            if stderr:
                                logger.error(f"  - FFmpeg错误: {stderr.decode()}")
                            return None

                    except Exception as process_error:
                        logger.error(f"启动或执行ffmpeg进程异常: {str(process_error)}", exc_info=True)

                        # 确保进程被清理
                        if process is not None:
                            try:
                                if process.returncode is None:
                                    logger.warning(f"检测到未完成的进程(PID: {process.pid})，正在终止...")
                                    process.kill()
                                    await process.wait()
                            except Exception as cleanup_error:
                                logger.error(f"清理ffmpeg进程失败: {str(cleanup_error)}")

                        return None

            except subprocess.TimeoutExpired:
                logger.error(f"ffmpeg命令执行超时（subprocess.TimeoutExpired）")
                return None
            except Exception as e:
                logger.error(f"执行ffmpeg命令异常: {str(e)}", exc_info=True)
                return None

    async def _generate_custom_cover(self,
                                   cover_info: Dict[str, Any],
                                   output_dir: str,
                                   start_time: Optional[str] = None,
                                   end_time: Optional[str] = None) -> Optional[str]:
        """
        生成自定义封面

        Args:
            cover_info: 封面信息
                - replay_name: 回放名称
                - space_name: 所属空间
                - class_time: 上课时间
            output_dir: 输出目录
            start_time: 录制开始时间（用于自定义封面生成class_time）
            end_time: 录制结束时间（用于自定义封面生成class_time）

        Returns:
            封面文件路径
        """

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)

        # 生成输出文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(output_dir, f"cover_custom_{timestamp}.jpg")

        # 详细检查前置条件
        logger.info(f"封面生成前置检查:")
        logger.info(f"- 输出目录: {output_dir} (存在: {os.path.exists(output_dir)})")
        logger.info(f"- 输出文件: {output_file}")
        logger.info(f"- 背景图路径: {self.bg_image_path} (存在: {os.path.exists(self.bg_image_path)})")
        logger.info(f"- 字体路径: {self.font_path} (存在: {os.path.exists(self.font_path)})")

        # 确保背景图存在
        if not os.path.exists(self.bg_image_path):
            logger.error(f"背景图不存在: {self.bg_image_path}")
            # 尝试寻找替代的背景图路径
            alternative_paths = [
                "/app/resource/images/bg.png",  # Docker容器内绝对路径
                "resource/images/bg.png",
                "./resource/images/bg.png",
                "../resource/images/bg.png",
                "../../resource/images/bg.png"
            ]

            found_bg = False
            for alt_path in alternative_paths:
                if os.path.exists(alt_path):
                    logger.info(f"找到替代背景图: {alt_path}")
                    self.bg_image_path = alt_path
                    found_bg = True
                    break

            if not found_bg:
                logger.error(f"所有背景图路径都不存在: {alternative_paths}")
                return None

        # 获取封面信息
        replay_name = cover_info.get("replay_name", "未知回放")
        space_name = cover_info.get("space_name", "未知空间")

        # 处理上课时间：优先使用传入的class_time，如果没有则从start_time和end_time生成
        class_time = cover_info.get("class_time")
        if not class_time and start_time and end_time:
            try:
                # 解析时间字符串，支持多种格式
                if isinstance(start_time, str):
                    # 支持格式：2024-11-18 10:00:00.000 或 2024-11-18 10:00:00
                    start_dt = datetime.fromisoformat(start_time.replace('.000', ''))
                else:
                    start_dt = start_time

                if isinstance(end_time, str):
                    end_dt = datetime.fromisoformat(end_time.replace('.000', ''))
                else:
                    end_dt = end_time

                # 生成格式：2024/11/18 10:00-11:40
                date_str = start_dt.strftime("%Y/%m/%d")
                start_time_str = start_dt.strftime("%H:%M")
                end_time_str = end_dt.strftime("%H:%M")
                class_time = f"{date_str} {start_time_str}-{end_time_str}"

                logger.info(f"自动生成class_time: {class_time}")

            except Exception as e:
                logger.warning(f"解析时间失败，使用默认时间: {str(e)}")
                class_time = "未知时间"
        elif not class_time:
            class_time = "未知时间"

        logger.info(f"封面信息: 回放={replay_name}, 空间={space_name}, 时间={class_time}")

        # 构建ffmpeg命令 - 添加文字到背景图
        drawtext_filters = []


        # 回放名称内容
        content_filter = (
            f"drawtext="
            f"fontfile='{self.font_path}':"
            f"text='{self._escape_text(replay_name)}':"
            f"x={CoverTextConfig.CONTENT_X}:"
            f"y={CoverTextConfig.CONTENT_Y}:"
            f"fontsize={CoverTextConfig.CONTENT_FONT_SIZE}:"
            f"fontcolor={CoverTextConfig.CONTENT_COLOR}"
        )
        drawtext_filters.append(content_filter)

        # 上课时间
        time_filter = (
            f"drawtext="
            f"fontfile='{self.font_path}':"
            f"text='{self._escape_text(f'上课时间：{class_time}')}':"
            f"x={CoverTextConfig.INFO_X}:"
            f"y={CoverTextConfig.TIME_Y}:"
            f"fontsize={CoverTextConfig.INFO_FONT_SIZE}:"
            f"fontcolor={CoverTextConfig.INFO_COLOR}"
        )
        drawtext_filters.append(time_filter)

        # 所属空间
        space_filter = (
            f"drawtext="
            f"fontfile='{self.font_path}':"
            f"text='{self._escape_text(f'所属空间：{space_name}')}':"
            f"x={CoverTextConfig.INFO_X}:"
            f"y={CoverTextConfig.SPACE_Y}:"
            f"fontsize={CoverTextConfig.INFO_FONT_SIZE}:"
            f"fontcolor={CoverTextConfig.INFO_COLOR}"
        )
        drawtext_filters.append(space_filter)



        # 组合所有文字滤镜
        video_filter = ",".join(drawtext_filters)
        logger.info(f"输出文件: {output_file}")
        cmd = [
            "ffmpeg", "-y",
            "-i", self.bg_image_path,
            "-vf", video_filter,
            "-frames:v", "1",
            "-update", "1",  # 添加update选项用于写入单个图像
            output_file
        ]

        logger.info(f"生成自定义封面: ffmpeg命令已构建")
        logger.info(f"完整ffmpeg命令: {' '.join(cmd)}")

        # 检测操作系统
        is_windows = platform.system() == 'Windows'
        logger.info(f"检测到操作系统: {platform.system()}")

        # 先检查ffmpeg是否可用
        try:
            if is_windows:
                import subprocess
                # Windows环境使用subprocess测试ffmpeg
                test_result = subprocess.run(
                    ["ffmpeg", "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5  # 测试命令超时时间较短
                )

                if test_result.returncode == 0:
                    logger.info(f"ffmpeg版本检查通过")
                    logger.debug(f"ffmpeg版本信息: {test_result.stdout[:200]}...")
                else:
                    logger.error(f"ffmpeg不可用: {test_result.stderr}")
                    return None
            else:
                # 非Windows环境使用asyncio
                test_process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    test_stdout, test_stderr = await asyncio.wait_for(
                        test_process.communicate(),
                        timeout=5.0  # 测试命令超时时间较短
                    )
                except asyncio.TimeoutError:
                    logger.error("ffmpeg版本检查超时")
                    test_process.kill()
                    await test_process.wait()
                    return None

                if test_process.returncode == 0:
                    logger.info(f"ffmpeg版本检查通过")
                    logger.debug(f"ffmpeg版本信息: {test_stdout.decode()[:200]}...")
                else:
                    logger.error(f"ffmpeg不可用: {test_stderr.decode()}")
                    return None

        except Exception as e:
            logger.error(f"ffmpeg命令测试失败: {str(e)}")
            return None

        # 使用信号量控制并发
        async with self._extraction_semaphore:
            current_value = getattr(self._extraction_semaphore, "_value", None)
            if current_value is None:
                logger.info(
                    f"获得封面生成信号量，并发上限: {self._extraction_concurrency}"
                )
            else:
                logger.info(
                    f"获得封面生成信号量，剩余可用: {current_value}/{self._extraction_concurrency}"
                )

            try:
                if is_windows:
                    import subprocess
                    # Windows环境使用subprocess执行ffmpeg命令
                    logger.info(f"Windows环境执行ffmpeg命令")
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=self.FFMPEG_TIMEOUT
                    )

                    # 详细记录执行结果
                    logger.info(f"ffmpeg命令执行完成:")
                    logger.info(f"- 返回码: {result.returncode}")
                    logger.info(f"- 输出文件存在: {os.path.exists(output_file)}")
                    if os.path.exists(output_file):
                        logger.info(f"- 输出文件大小: {os.path.getsize(output_file)} bytes")

                    if result.stdout:
                        logger.debug(f"ffmpeg标准输出: {result.stdout}")

                    if result.stderr:
                        logger.debug(f"ffmpeg错误输出: {result.stderr}")

                    if result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                        logger.info(f"自定义封面生成成功: {output_file}")
                        return output_file
                    else:
                        logger.error(f"自定义封面生成失败，返回码: {result.returncode}")
                        if result.stderr:
                            logger.error(f"错误信息: {result.stderr}")

                        # 尝试备用方案：使用shell=True执行命令
                        logger.info("尝试使用备用方案：shell命令执行")
                        try:
                            # 构建shell命令，将vf参数用双引号包围
                            shell_cmd = f'ffmpeg -y -i "{self.bg_image_path}" -vf "{video_filter}" -frames:v 1 -update 1 "{output_file}"'
                            logger.info(f"备用shell命令: {shell_cmd}")

                            shell_result = subprocess.run(
                                shell_cmd,
                                shell=True,
                                capture_output=True,
                                text=True,
                                timeout=self.FFMPEG_TIMEOUT
                            )

                            if shell_result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                                logger.info(f"备用方案成功生成封面: {output_file}")
                                return output_file
                            else:
                                logger.error(f"备用方案也失败，返回码: {shell_result.returncode}")
                                if shell_result.stderr:
                                    logger.error(f"备用方案错误信息: {shell_result.stderr}")
                        except subprocess.TimeoutExpired:
                            logger.error("备用方案执行超时")
                        except Exception as shell_error:
                            logger.error(f"备用方案执行异常: {str(shell_error)}")

                        return None
                else:
                    # 非Windows环境使用asyncio执行，添加超时控制
                    process = None
                    try:
                        process = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )

                        logger.info(f"FFmpeg进程已启动，PID: {process.pid}")

                        # 使用asyncio.wait_for添加超时控制
                        try:
                            stdout, stderr = await asyncio.wait_for(
                                process.communicate(),
                                timeout=self.FFMPEG_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            logger.error(f"ffmpeg命令执行超时（{self.FFMPEG_TIMEOUT}秒），正在终止进程...")

                            # 确保进程被清理
                            try:
                                process.terminate()
                                await asyncio.wait_for(process.wait(), timeout=5.0)
                            except asyncio.TimeoutError:
                                logger.warning("进程无法在5秒内终止，强制kill")
                                process.kill()
                                await process.wait()

                            logger.error("ffmpeg命令执行超时，进程已终止")
                            return None

                        # 详细记录执行结果
                        logger.info(f"ffmpeg命令执行完成:")
                        logger.info(f"- 返回码: {process.returncode}")
                        logger.info(f"- 输出文件存在: {os.path.exists(output_file)}")
                        if os.path.exists(output_file):
                            logger.info(f"- 输出文件大小: {os.path.getsize(output_file)} bytes")

                        if stdout:
                            logger.debug(f"ffmpeg标准输出: {stdout.decode()}")

                        if stderr:
                            logger.debug(f"ffmpeg错误输出: {stderr.decode()}")

                        if process.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                            logger.info(f"自定义封面生成成功: {output_file}")
                            return output_file
                        else:
                            logger.error(f"自定义封面生成失败，返回码: {process.returncode}")
                            if stderr:
                                logger.error(f"错误信息: {stderr.decode()}")

                            # 尝试备用方案：使用简化的同步命令
                            logger.info("尝试使用备用方案：简化同步命令")
                            try:
                                import subprocess
                                # 构建shell命令，将vf参数用双引号包围
                                shell_cmd = f'ffmpeg -y -i "{self.bg_image_path}" -vf "{video_filter}" -frames:v 1 -update 1 "{output_file}"'
                                logger.info(f"备用shell命令: {shell_cmd}")

                                shell_result = subprocess.run(
                                    shell_cmd,
                                    shell=True,
                                    capture_output=True,
                                    text=True,
                                    timeout=self.FFMPEG_TIMEOUT
                                )

                                if shell_result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                                    logger.info(f"备用方案成功生成封面: {output_file}")
                                    return output_file
                                else:
                                    logger.error(f"备用方案也失败，返回码: {shell_result.returncode}")
                                    if shell_result.stderr:
                                        logger.error(f"备用方案错误信息: {shell_result.stderr}")
                            except subprocess.TimeoutExpired:
                                logger.error("备用方案执行超时")
                            except Exception as shell_error:
                                logger.error(f"备用方案执行异常: {str(shell_error)}")

                            return None

                    except Exception as process_error:
                        logger.error(f"启动或执行ffmpeg进程异常: {str(process_error)}", exc_info=True)

                        # 确保进程被清理
                        if process is not None:
                            try:
                                if process.returncode is None:
                                    logger.warning(f"检测到未完成的进程(PID: {process.pid})，正在终止...")
                                    process.kill()
                                    await process.wait()
                            except Exception as cleanup_error:
                                logger.error(f"清理ffmpeg进程失败: {str(cleanup_error)}")

                        return None

            except subprocess.TimeoutExpired:
                logger.error(f"ffmpeg命令执行超时（subprocess.TimeoutExpired）")
                return None
            except Exception as e:
                logger.error(f"执行ffmpeg命令异常: {str(e)}", exc_info=True)
                return None

    def _escape_text(self, text: str) -> str:
        """
        转义文字中的特殊字符，用于ffmpeg的drawtext滤镜

        Args:
            text: 原始文字

        Returns:
            转义后的文字
        """
        if not text:
            return ""

        escaped_text = str(text)

        # 将英文冒号替换为中文冒号，避免FFmpeg转义问题
        escaped_text = escaped_text.replace(":", "：")

        # 先处理反斜杠
        escaped_text = escaped_text.replace("\\", "\\\\")

        # 然后处理其他特殊字符 (移除冒号转义，因为我们已经替换为中文冒号)
        escape_map = {
            "'": r"\'",
            '"': r'\"',
            '[': r'\[',
            ']': r'\]',
            '%': r'\%',
            '=': r'\=',
            ';': r'\;',
            ',': r'\,',
            '\n': r'\n',
            '\r': r'\r'
        }

        for char, escaped in escape_map.items():
            escaped_text = escaped_text.replace(char, escaped)

        return escaped_text

    def validate_cover_params(self, strategy: str, cover_info: Dict[str, Any]) -> bool:
        """
        验证封面参数

        Args:
            strategy: 提取策略
            cover_info: 封面信息

        Returns:
            是否有效
        """
        logger.info(f"开始验证封面参数 - 策略: {strategy}, 参数: {cover_info}")

        if strategy == CoverExtractStrategy.TIMESTAMP.value:
            timestamp = cover_info.get("timestamp", 60)
            logger.info(f"时间戳封面验证 - 原始timestamp: {timestamp} (类型: {type(timestamp).__name__})")

            # 支持字符串类型的timestamp，尝试转换为数字
            if isinstance(timestamp, str):
                try:
                    timestamp = float(timestamp)
                    logger.info(f"字符串timestamp转换成功: {timestamp}")
                    # 更新cover_info中的值为数字类型
                    cover_info["timestamp"] = timestamp
                except (ValueError, TypeError) as e:
                    logger.error(f"timestamp字符串转换失败: {timestamp}, 错误: {str(e)}")
                    return False

            # 验证是否为有效的数字类型且非负
            is_valid = isinstance(timestamp, (int, float)) and timestamp >= 0
            logger.info(f"时间戳验证结果: {is_valid} (timestamp: {timestamp}, 类型: {type(timestamp).__name__})")
            return is_valid

        elif strategy == CoverExtractStrategy.CUSTOM.value:
            required_fields = ["replay_name", "space_name"]
            missing_fields = [field for field in required_fields if field not in cover_info]

            if missing_fields:
                logger.error(f"自定义封面验证失败 - 缺少必填字段: {missing_fields}")
                return False
            else:
                logger.info(f"自定义封面验证通过 - 所有必填字段存在: {required_fields}")
                # class_time 不再是必填字段，可以从start_time和end_time自动生成
                return True

        else:
            logger.error(f"不支持的封面策略: {strategy}")
            return False
