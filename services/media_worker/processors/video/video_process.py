import logging
from math import dist
import os
import platform
import tempfile
import asyncio
import ffmpeg
import subprocess
from datetime import datetime
from media_platform.infrastructure.storage import get_storage_service
from typing import Optional, Dict, Any
import httpx
from media_platform.infrastructure.storage import get_enhanced_storage_service
from media_platform.infrastructure.media_processing import GetAudioInfo
from media_platform.infrastructure.media_processing import CoverExtractor
from media_platform.common.redaction import sanitize_url

logger = logging.getLogger("video_process")

class VideoProcess:
    def __init__(self,storage_service_type:str="none"):
        self.storage_service = get_enhanced_storage_service(storage_service_type)
        self.video_data = []
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self._cover_extractor: CoverExtractor | None = None

    # def set_callback(self, callback):
    #     self.callback_url:str = callback

    # def set_task_id(self, task_id):
    #     self.task_id:str = task_id

    def process(self, video_data):
        # 处理视频数据
        return self.video_data

    @property
    def cover_extractor(self) -> CoverExtractor:
        """按需创建封面提取器，避免非封面任务提前加载 OpenCV/FFmpeg 配置。"""

        if self._cover_extractor is None:
            self._cover_extractor = CoverExtractor()
        return self._cover_extractor

    async def _extract_audio_file(
        self,
        video_path: str,
        task_id: str,
        audio_format: str = "mp3",
    ) -> str:
        """从本地视频文件中提取音频。

        通用媒体 Worker 不能复用录制节点 ``StreamRecorder._extract_audio``，
        否则会把 Worker 与录制节点生命周期、ZL 配置和本地录像目录耦合在一起。
        这里保留旧 ``extract_audio_by_mp4_url`` 的行为，只负责本地 FFmpeg 提取。
        """

        if not os.path.exists(video_path):
            raise ValueError(f"视频文件不存在: {video_path}")

        normalized_format = (audio_format or "mp3").strip().lower().lstrip(".")
        if normalized_format != "mp3":
            raise ValueError("旧 extract_audio_by_mp4_url 目前仅支持 mp3")

        output_path = os.path.join(
            tempfile.gettempdir(),
            f"{task_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.mp3",
        )
        command = [
            os.getenv("MEDIA_FFMPEG_PATH", "ffmpeg"),
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-b:a",
            "192k",
            output_path,
        ]

        def _run():
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=int(os.getenv("MEDIA_AUDIO_EXTRACT_TIMEOUT_SECONDS", "600")),
                check=False,
            )

        try:
            result = await asyncio.to_thread(_run)
        except FileNotFoundError as exc:
            raise RuntimeError("未找到FFmpeg，请配置MEDIA_FFMPEG_PATH或PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg音频提取超时") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if len(stderr) > 1000:
                stderr = stderr[-1000:]
            raise RuntimeError(f"FFmpeg音频提取失败: {stderr}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
            raise RuntimeError("FFmpeg执行完成但音频文件无效")

        return output_path

    async def extract_audio_by_mp4_url(self,mp4Url:str,taskId:str):
        # if(mp4Path is None|taskId is None):
        #     print("ExtractAudioByMp4参数异常")
        # 下载视频到本地
        logger.info("开始下载视频文件")

        mp4Path=await self.storage_service.download_file(mp4Url)
        logger.info(f"文件下载成功:{mp4Path}")
        if mp4Path is None:
            raise ValueError("Expected a result, but got None")
        # 拆分音频文件
        mp3Path=await self._extract_audio_file(mp4Path,taskId,"mp3")
        if mp3Path is None:
            logger.error("拆分音频文件失败")
            raise ValueError("Expected a result, but got None")
        logger.info(f"拆分音频文件成功:{mp3Path}")
        # 上传音频文件
        file_result=await self.storage_service.upload_file_enhanced(mp3Path, upload_type="audio")
        if file_result is None:
            logger.error("拆分音频文件失败")
            raise ValueError("Expected a result, but got None")
        logger.info("音频文件上传成功")

        # 获取音频信息
        get_audio_info=GetAudioInfo()
        audio_info=await get_audio_info.get_audio_info(mp3Path)

        # 删除本地文件
        os.remove(mp4Path)
        os.remove(mp3Path)

        return {"audio_url": file_result.file_url, "audio_info": audio_info}

    async def get_video_imgs(self, video_file_path: str, interval=5):
        """
        从视频中按指定时间间隔提取帧图像

        :param video_file_path: 本地视频文件路径
        :param interval: 提取帧的时间间隔(秒)
        :return: 提取的图片本地路径列表
        """

        logger.info(f"开始提取视频帧: {video_file_path}")
        # 创建临时目录存储提取的帧
        temp_dir = tempfile.mkdtemp()
        # 创建一个字典来存储提取的图片时间和路径
        image_paths = []
        logger.info(f"创建的临时目录: {temp_dir}")

        try:
            # 检查视频文件是否存在
            if not os.path.exists(video_file_path):
                logger.error(f"视频文件不存在: {video_file_path}")
                return []
            file_size = os.path.getsize(video_file_path)
            logger.info(f"视频文件大小: {file_size}字节")
            # 检测操作系统
            is_windows = platform.system() == 'Windows'

            # 使用ffmpeg-python获取视频时长
            logger.info(f"使用ffmpeg-python获取视频时长: {video_file_path}")
            video_file_path = os.path.abspath(video_file_path)
            logger.info(f"视频文件绝对路径: {video_file_path}")
            logger.info(f"视频文件规范化路径: {video_file_path}")
            # probe = ffmpeg.probe(video_file_path)
            probe = await asyncio.to_thread(ffmpeg.probe, video_file_path)
            duration = float(probe['format']['duration'])
            logger.info(f"视频时长: {duration}秒")
            # 计算需要提取的帧的时间点
            timestamps = []
            current_time = 0
            while current_time < duration:
                timestamps.append(current_time)
                current_time += interval

            # 如果最后一个时间点与视频结束时间相差太近，可以移除
            if timestamps and duration - timestamps[-1] < 0.5:
                timestamps.pop()

            # 添加最后一帧
            if timestamps and timestamps[-1] < duration - 0.5:
                timestamps.append(max(0, duration - 0.1))

            # 提取每个时间点的帧
            for i, timestamp in enumerate(timestamps):
                output_file = os.path.join(temp_dir, f"frame_{i:04d}.jpg")

                if is_windows:
                    import subprocess

                    logger.info(f"Windows环境提取视频帧: {video_file_path}")
                    # Windows环境使用subprocess
                    cmd = [
                        "ffmpeg",
                        "-ss", str(timestamp),
                        "-i", video_file_path,
                        "-vframes", "1",
                        "-q:v", "2",
                        "-y", output_file
                    ]

                    logger.info(f"Windows环境提取视频帧: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True)

                    if result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                        image_paths.append({"time": timestamp, "path": output_file})
                    else:
                        logger.error(f"提取视频帧失败: {result.stderr}")
                else:
                    logger.info(f"非Windows环境提取视频帧: {video_file_path}")
                    # 非Windows环境使用ffmpeg-python
                    try:
                        (
                            ffmpeg
                            .input(video_file_path, ss=timestamp)
                            .output(output_file, vframes=1, q=2)
                            .overwrite_output()
                            .run(capture_stdout=True, capture_stderr=True)
                        )

                        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                            image_paths.append({"time": timestamp, "path": output_file})
                        else:
                            logger.error(f"提取视频帧失败: {output_file}")
                    except ffmpeg.Error as e:
                        logger.error(f"FFmpeg错误: {str(e)}")

            return image_paths

        except Exception as e:
            logger.error(f"提取视频帧过程异常: {str(e)}")
            return []

    async def get_video_imgs_url(self,file_url: str,interval: int = 5):
        """获取视频帧
        Args:
            file_url (str): 网络视频文件路径
            interval (int, optional): 提取帧的时间间隔(秒). Defaults to 5.
        Returns:
            list: 图片文件网络路径列表
        """
        file_path=await self.storage_service.download_file(file_url)
        if  not file_path:
            logger.error(f"文件下载失败{file_url}")
            return []
        logger.info(f"文件下载成功{file_path}")

        # 每{interval}秒提取一帧
        local_imgs =await self.get_video_imgs(file_path, interval)
        print(f"提取了{len(local_imgs)}个视频帧")

        # 上传图片到COS
        image_urls = []
        for local_img in local_imgs:
            img_result= await self.storage_service.upload_file_enhanced(local_img["path"],upload_type="cover")
            image_urls.append({"time": local_img["time"], "url": img_result.file_url})
        logger.info(f"图片上传成功")

        # 删除本地文件
        os.remove(file_path)
        logger.info(f"本地视频文件删除成功")
        for local_img in local_imgs:
            os.remove(local_img["path"])
        logger.info(f"本地图片删除成功")

        return image_urls

    async def video_set_watermark(self,video_path:str,watermark:str):
        """给视频添加水印
        Args:
            video_url (str): 视频文件路径
            watermark_url (str): 水印字符串
        Returns:
            str: 添加水印后的视频文件路径
        """
        logger.info(f"开始给视频添加水印: {video_path}, 水印文本: {watermark}")

        try:
            # 检查视频文件是否存在
            if not os.path.exists(video_path):
                logger.error(f"视频文件不存在: {video_path}")
                return None

            # 创建输出文件路径
            output_path = tempfile.mktemp(suffix=".mp4")

            # 检测操作系统
            is_windows = platform.system() == 'Windows'

            # 根据操作系统选择默认字体
            if is_windows:
                # Windows默认字体路径
                font_path = "C:\\Windows\\Fonts\\simhei.ttf"  # 使用黑体
                if not os.path.exists(font_path):
                    font_path = "C:\\Windows\\Fonts\\arial.ttf"  # 备选Arial字体
            else:
                # Linux默认字体路径
                font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                if not os.path.exists(font_path):
                    font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"  # 备选字体

            # 确保字体文件存在
            if not os.path.exists(font_path):
                logger.warning(f"默认字体文件不存在: {font_path}，尝试使用简单文本水印")
                # 如果找不到字体文件，使用简化的水印参数
                if is_windows:
                    # Windows环境使用subprocess
                    import subprocess

                    # 简化的水印参数，不使用字体
                    cmd = [
                        "ffmpeg",
                        "-i", video_path,
                        "-vcodec", "libx264",
                        "-preset", "fast",
                        "-acodec", "copy",
                        "-y", output_path
                    ]

                    logger.info(f"Windows环境简化处理命令: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True)
                else:
                    # 非Windows环境使用ffmpeg-python
                    stream = ffmpeg.input(video_path)
                    stream = ffmpeg.output(stream, output_path, vcodec="libx264", preset="fast", acodec="copy")
                    await asyncio.to_thread(stream.run, capture_stdout=True, capture_stderr=True, overwrite_output=True)

                logger.warning("由于找不到合适的字体文件，视频已处理但未添加水印")
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return output_path
                return None

            if is_windows:
                # Windows环境使用subprocess
                import subprocess

                font_path_ffmpeg = font_path.replace("\\", "/")
                if ":" in font_path_ffmpeg:
                    # 转义盘符后的冒号 (C:/ → C\:/)
                    parts = font_path_ffmpeg.split(":", 1)
                    font_path_ffmpeg = parts[0] + "\\:" + parts[1]

                # 使用drawtext滤镜添加文本水印，指定字体文件
                filter_complex = f"drawtext=text='{watermark}':fontfile='{font_path_ffmpeg}':x=10:y=10:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5"

                cmd = [
                    "ffmpeg",
                    "-i", video_path,
                    "-vf", filter_complex,
                    "-codec:a", "copy",
                    "-y", output_path
                ]

                logger.info(f"Windows环境添加水印命令: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    logger.info(f"视频添加水印成功: {output_path}")
                    return output_path
                else:
                    logger.error(f"视频添加水印失败: {result.stderr}")
                    return None
            else:
                # 非Windows环境使用ffmpeg-python
                try:
                    # 使用drawtext滤镜添加文本水印，指定字体文件
                    stream = ffmpeg.input(video_path)
                    stream = ffmpeg.filter(stream, 'drawtext', text=watermark, fontfile=font_path, x=10, y=10,
                                          fontsize=24, fontcolor='white', box=1,
                                          boxcolor='black@0.5')
                    stream = ffmpeg.output(stream, output_path, acodec='copy')
                    await asyncio.to_thread(stream.run, capture_stdout=True, capture_stderr=True, overwrite_output=True)

                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        logger.info(f"视频添加水印成功: {output_path}")
                        return output_path
                    else:
                        logger.error(f"视频添加水印失败: 输出文件无效")
                        return None
                except ffmpeg.Error as e:
                    logger.error(f"FFmpeg错误: {str(e)}")
                    return None
        except Exception as e:
            logger.error(f"添加水印过程异常: {str(e)}")
            return None

        return output_path

    async def video_set_watermark_url(self,video_url:str,watermark:str):
        """给视频添加水印
        Args:
            video_url (str): 视频文件路径
            watermark_url (str): 水印字符串
        Returns:
            str: 添加水印后的视频文件路径
        """

        # 下载视频文件
        video_file=await self.storage_service.download_file(video_url)
        logger.info(f"文件下载成功{video_file}")

        # 添加水印
        video_result_file =await self.video_set_watermark(video_file, watermark)
        print(f"视频添加水印成功：{video_result_file}")

        # 上传到COS
        video_result= await self.storage_service.upload_file(video_result_file)
        logger.info(f"上传成功:{video_result.file_url}")

        # 删除本地文件
        os.remove(video_file)
        os.remove(video_result_file)

        return video_result

    async def video_extract_cover_url(self,video_url:str,cover_strategy:str,cover_info:Dict[str, Any]):
        """提取视频封面
        Args:
            video_url (str): 视频文件路径
        Returns:
            str: 封面图片文件路径
        """
        cover_output_dir = tempfile.mkdtemp()

        if cover_strategy== "timestamp":
            video_file=await self.storage_service.download_file(video_url,cover_output_dir)
            logger.info(f"文件下载成功{video_file}")
        else:
            video_file=None

        # 提取封面
        # cover_output_dir = os.path.join(os.path.dirname(result_url), "covers")
        logger.info(f"封面输出目录: {cover_output_dir}")
        cover_file =await self.cover_extractor.extract_cover(
                            video_path=video_file,
                            strategy=cover_strategy,
                            cover_info=cover_info,
                            output_dir=cover_output_dir,
                            start_time=cover_info.get("start_time"),
                            end_time=cover_info.get("end_time")
                        )
        if not cover_file:
            logger.error(f"提取封面失败: {cover_file}")
            return None
        logger.info(f"提取封面成功：{cover_file}")


        # 上传到COS
        cover_result= await self.storage_service.upload_file_enhanced(cover_file,upload_type="cover")
        logger.info(f"上传成功:{cover_result.file_url}")

        #TODO:删除本地文件
        if video_file:
            os.remove(video_file)
        os.remove(cover_file)
        os.rmdir(cover_output_dir)
        logger.info(f"本地文件删除成功")

        return cover_result

    async def live_extract_cover_url(self, stream_url: str, cover_strategy: str, cover_info: Dict[str, Any]):
        """提取直播流封面
        Args:
            stream_url (str): 直播流地址
            cover_strategy (str): 封面提取策略
            cover_info (Dict[str, Any]): 封面相关信息
        Returns:
            dict: 封面上传结果
        """
        logger.info(f"开始提取直播流封面: {stream_url}, 策略: {cover_strategy}")

        # 创建临时目录
        cover_output_dir = tempfile.mkdtemp()
        logger.info(f"创建临时目录: {cover_output_dir}")

        try:
            if cover_strategy == "timestamp":
                # 直接从直播流提取第一帧
                cover_file = await self._extract_live_stream_first_frame(stream_url, cover_output_dir)
            elif cover_strategy == "custom":
                # 生成自定义封面（不需要视频文件）
                cover_file = await self.cover_extractor.extract_cover(
                    video_path=None,  # 自定义封面不需要视频文件
                    strategy=cover_strategy,
                    cover_info=cover_info,
                    output_dir=cover_output_dir,
                    start_time=cover_info.get("start_time"),
                    end_time=cover_info.get("end_time")
                )
            else:
                logger.error(f"不支持的封面策略: {cover_strategy}")
                return None

            if not cover_file:
                logger.error(f"提取直播流封面失败: {cover_file}")
                return None

            logger.info(f"提取直播流封面成功: {cover_file}")

            # 上传到存储服务
            cover_result = await self.storage_service.upload_file_enhanced(cover_file, upload_type="cover")
            logger.info(f"封面上传成功: {cover_result.file_url}")

            # 清理本地文件
            os.remove(cover_file)
            os.rmdir(cover_output_dir)
            logger.info(f"本地文件清理成功")

            return cover_result

        except Exception as e:
            logger.error(f"提取直播流封面异常: {str(e)}", exc_info=True)
            # 清理临时目录
            try:
                if os.path.exists(cover_output_dir):
                    import shutil
                    shutil.rmtree(cover_output_dir)
            except:
                pass
            return None

    async def _extract_live_stream_first_frame(self, stream_url: str, output_dir: str) -> Optional[str]:
        """从直播流中提取第一帧
        Args:
            stream_url (str): 直播流地址
            output_dir (str): 输出目录
        Returns:
            str: 封面文件路径
        """
        logger.info(f"开始从直播流提取第一帧: {stream_url}")

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)

        # 生成输出文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(output_dir, f"live_cover_{timestamp}.jpg")

        # 构建ffmpeg命令 - 从直播流提取第一帧
        cmd = [
            "ffmpeg", "-y",  # -y 覆盖已存在文件
            "-i", stream_url,  # 输入直播流URL
            "-vframes", "1",  # 只输出一帧
            "-q:v", "2",  # 高质量
            "-f", "image2",  # 输出格式为图片
            output_file
        ]

        logger.info(f"ffmpeg命令: {' '.join(cmd)}")

        # 检测操作系统
        is_windows = platform.system() == 'Windows'

        max_retries = 3
        for attempt in range(max_retries):
            logger.info(f"开始提取直播流第一帧，第 {attempt + 1} 次尝试...")
            try:
                if is_windows:
                    # Windows环境使用subprocess
                    import subprocess
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=10  # 设置10秒超时
                    )

                    logger.info(f"ffmpeg执行完成，返回码: {result.returncode}")

                    if result.stdout:
                        logger.debug(f"ffmpeg标准输出: {result.stdout}")
                    if result.stderr:
                        logger.debug(f"ffmpeg错误输出: {result.stderr}")

                    # 检查输出文件
                    if result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                        logger.info(f"直播流第一帧提取成功: {output_file}")
                        return output_file
                    else:
                        logger.error(f"直播流第一帧提取失败: 返回码={result.returncode}")
                        if result.stderr:
                            logger.error(f"FFmpeg错误: {result.stderr}")

                else:
                    # 非Windows环境使用asyncio
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )

                    try:
                        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.error("ffmpeg命令执行超时")
                        process.kill()
                        await process.wait()
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        else:
                            return None

                    logger.info(f"ffmpeg执行完成，返回码: {process.returncode}")

                    if stdout:
                        logger.debug(f"ffmpeg标准输出: {stdout.decode()}")
                    if stderr:
                        logger.debug(f"ffmpeg错误输出: {stderr.decode()}")

                    # 检查输出文件
                    if process.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                        logger.info(f"直播流第一帧提取成功: {output_file}")
                        return output_file
                    else:
                        logger.error(f"直播流第一帧提取失败: 返回码={process.returncode}")
                        if stderr:
                            logger.error(f"FFmpeg错误: {stderr.decode()}")

            except subprocess.TimeoutExpired:
                logger.error(f"ffmpeg命令执行超时，第 {attempt + 1} 次尝试失败")
            except Exception as e:
                logger.error(f"执行ffmpeg命令异常，第 {attempt + 1} 次尝试失败: {str(e)}", exc_info=True)

            if attempt < max_retries - 1:
                await asyncio.sleep(2)

        logger.error("提取直播流第一帧失败，已达到最大重试次数")
        return None

    def _parse_time_to_seconds(self, time_value) -> float:
        """将时间转换为秒数
        Args:
            time_value: 可以是秒数(int/float)或时间字符串(如 "00:01:01.000")
        Returns:
            float: 转换后的秒数
        """
        # 如果已经是数字，直接返回
        if isinstance(time_value, (int, float)):
            return float(time_value)

        # 如果是字符串，解析时间格式
        if isinstance(time_value, str):
            try:
                # 支持格式: "HH:MM:SS.mmm" 或 "MM:SS.mmm" 或 "SS.mmm" 或 "SS"
                time_str = time_value.strip()

                # 分离秒和毫秒部分
                if '.' in time_str:
                    time_part, ms_part = time_str.split('.')
                    milliseconds = float(f"0.{ms_part}")
                else:
                    time_part = time_str
                    milliseconds = 0.0

                # 分离时分秒
                parts = time_part.split(':')

                if len(parts) == 3:  # HH:MM:SS
                    hours, minutes, seconds = map(int, parts)
                    total_seconds = hours * 3600 + minutes * 60 + seconds + milliseconds
                elif len(parts) == 2:  # MM:SS
                    minutes, seconds = map(int, parts)
                    total_seconds = minutes * 60 + seconds + milliseconds
                elif len(parts) == 1:  # SS
                    seconds = int(parts[0])
                    total_seconds = seconds + milliseconds
                else:
                    raise ValueError(f"无效的时间格式: {time_value}")

                return total_seconds

            except Exception as e:
                logger.error(f"时间格式解析失败: {time_value}, 错误: {str(e)}")
                raise ValueError(f"无效的时间格式: {time_value}, 支持格式: 秒数(如10.5) 或 时间字符串(如'00:01:01.000')")

        raise ValueError(f"不支持的时间类型: {type(time_value)}")

    async def video_extract_clips_url(self, video_url: str, clips: list) -> Dict[str, Any]:
        """提取视频精彩片段（混合方案：FFmpeg剪辑 + OpenCV提取封面）
        Args:
            video_url (str): 视频文件URL
            clips (list): 片段时间数组，支持两种格式:
                         1. 秒数格式: [{"start_time": 10.5, "end_time": 30.2, "buss_id": "xxx"}, ...]
                         2. 时间字符串格式: [{"start_time": "00:00:10.500", "end_time": "00:00:30.200", "buss_id": "xxx"}, ...]
        Returns:
            dict: 包含所有片段URL和封面URL的结果
        """
        logger.info(f"开始提取视频精彩片段: {video_url}, 片段数量: {len(clips)}")

        # 下载视频文件
        video_file = await self.storage_service.download_file(video_url)
        logger.info(f"视频文件下载成功: {video_file}")

        if not video_file or not os.path.exists(video_file):
            logger.error(f"视频文件下载失败或不存在: {video_file}")
            raise ValueError("视频文件下载失败")

        # 创建临时目录存储片段
        temp_dir = tempfile.mkdtemp()
        logger.info(f"创建临时目录: {temp_dir}")

        clip_results = []
        local_clip_files = []

        try:
            # 检测操作系统
            is_windows = platform.system() == 'Windows'

            # 遍历每个片段并提取
            for idx, clip in enumerate(clips):
                try:
                    # 获取业务ID
                    buss_id = clip.get("buss_id", "")

                    # 保存原始时间值（保持用户传入的格式）
                    original_start_time = clip.get("start_time", 0)
                    original_end_time = clip.get("end_time", 0)

                    # 解析时间格式（支持秒数和时间字符串两种格式）
                    start_time = self._parse_time_to_seconds(original_start_time)
                    end_time = self._parse_time_to_seconds(original_end_time)
                except ValueError as e:
                    logger.error(f"片段 {idx} 时间格式解析失败: {str(e)}")
                    continue

                if start_time >= end_time:
                    logger.warning(f"片段 {idx} 时间参数无效: start_time={start_time}, end_time={end_time}")
                    continue

                duration = end_time - start_time
                output_file = os.path.join(temp_dir, f"clip_{idx:04d}.mp4")

                logger.info(f"开始提取片段 {idx}: {start_time}s - {end_time}s, 时长: {duration}s, buss_id: {buss_id}")

                try:
                    if is_windows:
                        # Windows环境使用subprocess
                        import subprocess

                        cmd = [
                            "ffmpeg",
                            "-ss", str(start_time),  # 起始时间
                            "-i", video_file,  # 输入文件
                            "-t", str(duration),  # 持续时间
                            "-c:v", "libx264",  # 视频编码
                            "-c:a", "aac",  # 音频编码
                            "-preset", "fast",  # 编码速度
                            "-y",  # 覆盖输出文件
                            output_file
                        ]

                        logger.info(f"Windows环境提取片段命令: {' '.join(cmd)}")
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

                        if result.returncode == 0 and os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                            logger.info(f"片段 {idx} 提取成功: {output_file}")
                            local_clip_files.append(output_file)
                        else:
                            logger.error(f"片段 {idx} 提取失败: {result.stderr}")
                            continue
                    else:
                        # 非Windows环境使用ffmpeg-python
                        stream = ffmpeg.input(video_file, ss=start_time, t=duration)
                        stream = ffmpeg.output(
                            stream,
                            output_file,
                            vcodec="libx264",
                            acodec="aac",
                            preset="fast"
                        )
                        await asyncio.to_thread(
                            stream.run,
                            capture_stdout=True,
                            capture_stderr=True,
                            overwrite_output=True
                        )

                        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                            logger.info(f"片段 {idx} 提取成功: {output_file}")
                            local_clip_files.append(output_file)
                        else:
                            logger.error(f"片段 {idx} 提取失败: 输出文件无效")
                            continue

                    # 上传片段到存储服务
                    logger.info(f"开始上传片段 {idx} 到存储服务")
                    upload_result = await self.storage_service.upload_file_enhanced(
                        output_file,
                        upload_type="video"
                    )

                    if not upload_result or not upload_result.file_url:
                        logger.error(f"片段 {idx} 上传失败")
                        continue

                    logger.info(f"片段 {idx} 上传成功: {upload_result.file_url}")

                    # ============ 使用 OpenCV 提取封面（优化：更高效）============
                    cover_url = None
                    try:
                        logger.info(f"开始为片段 {idx} 使用 OpenCV 提取封面")
                        cover_file = await self._extract_cover_with_opencv(
                            output_file,
                            temp_dir,
                            idx
                        )

                        if cover_file and os.path.exists(cover_file):
                            logger.info(f"片段 {idx} 封面提取成功: {cover_file}")

                            # 上传封面到存储服务
                            cover_upload_result = await self.storage_service.upload_file_enhanced(
                                cover_file,
                                upload_type="cover"
                            )

                            if cover_upload_result and cover_upload_result.file_url:
                                cover_url = cover_upload_result.file_url
                                logger.info(f"片段 {idx} 封面上传成功: {cover_url}")
                            else:
                                logger.error(f"片段 {idx} 封面上传失败")

                            # 清理封面临时文件
                            if os.path.exists(cover_file):
                                os.remove(cover_file)
                        else:
                            logger.error(f"片段 {idx} 封面提取失败")

                    except Exception as e:
                        logger.error(f"片段 {idx} 封面提取异常: {str(e)}", exc_info=True)

                    # 使用原始时间格式返回（保持与请求时的格式一致）
                    clip_results.append({
                        "buss_id": buss_id,
                        "start_time": original_start_time,
                        "end_time": original_end_time,
                        "duration": duration,
                        "clip_url": upload_result.file_url,
                        "cover_url": cover_url
                    })

                except subprocess.TimeoutExpired:
                    logger.error(f"片段 {idx} 提取超时")
                    continue
                except Exception as e:
                    logger.error(f"片段 {idx} 处理异常: {str(e)}", exc_info=True)
                    continue

            logger.info(f"所有片段处理完成，成功: {len(clip_results)}/{len(clips)}")

            return {
                "video_url": video_url,
                "total_clips": len(clips),
                "success_clips": len(clip_results),
                "clips": clip_results
            }

        finally:
            # 清理本地文件
            try:
                # 删除下载的原视频
                if os.path.exists(video_file):
                    os.remove(video_file)
                    logger.info(f"删除本地视频文件: {video_file}")

                # 删除所有片段文件
                for clip_file in local_clip_files:
                    if os.path.exists(clip_file):
                        os.remove(clip_file)
                        logger.info(f"删除本地片段文件: {clip_file}")

                # 删除临时目录
                if os.path.exists(temp_dir):
                    os.rmdir(temp_dir)
                    logger.info(f"删除临时目录: {temp_dir}")
            except Exception as e:
                logger.error(f"清理本地文件异常: {str(e)}", exc_info=True)

    async def _extract_cover_with_opencv(
        self,
        video_file: str,
        output_dir: str,
        clip_idx: int
    ) -> Optional[str]:
        """使用 OpenCV 从视频中提取封面（高效方法）

        Args:
            video_file: 视频文件路径
            output_dir: 输出目录
            clip_idx: 片段索引

        Returns:
            封面文件路径，失败返回 None
        """
        def _extract_frame():
            """同步方法：提取视频中间帧作为封面"""
            import cv2

            try:
                # 打开视频文件
                cap = cv2.VideoCapture(video_file)

                if not cap.isOpened():
                    logger.error(f"无法打开视频文件: {video_file}")
                    return None

                # 获取视频总帧数和FPS
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps = cap.get(cv2.CAP_PROP_FPS)

                if total_frames <= 0 or fps <= 0:
                    logger.error(f"无效的视频参数: frames={total_frames}, fps={fps}")
                    cap.release()
                    return None

                # 定位到中间帧
                middle_frame = total_frames // 2
                cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame)

                # 读取帧
                ret, frame = cap.read()
                cap.release()

                if not ret or frame is None:
                    logger.error(f"无法读取视频帧")
                    return None

                # 保存封面图片
                cover_file = os.path.join(output_dir, f"cover_{clip_idx:04d}.jpg")

                # 设置JPEG质量参数（85质量，较好的压缩比）
                encode_params = [cv2.IMWRITE_JPEG_QUALITY, 85]
                success = cv2.imwrite(cover_file, frame, encode_params)

                if success and os.path.exists(cover_file):
                    logger.info(f"OpenCV提取封面成功: {cover_file}, 帧位置: {middle_frame}/{total_frames}")
                    return cover_file
                else:
                    logger.error(f"保存封面图片失败")
                    return None

            except Exception as e:
                logger.error(f"OpenCV提取封面异常: {str(e)}", exc_info=True)
                return None

        # 在线程池中执行（避免阻塞）
        return await asyncio.to_thread(_extract_frame)

    async def callback_notification(self,task_id: str,callback_url:str, result: Dict[str, Any]) -> bool:
        """
        发送回调通知
        :param task_id: 任务ID
        :param result: 任务结果
        :return: 是否发送成功
        """
        try:
            if not callback_url:
                logger.info(f"没有设置回调URL，跳过回调: {task_id}")
                return False

            # 构建回调数据
            # callback_data = {
            #     "task_id": self.task_id,
            #     "status": "completed",
            #     "progress": 100,
            #     "result": result,
            #     "error_message": "",
            #     "message": "视频处理完成",
            # }

            logger.info("回调数据已构造: task_id=%s", task_id)

            # 发送回调请求，支持重试
            max_retries = 3
            retry_interval = 5  # 秒

            for retry in range(max_retries):
                try:
                    logger.info(
                        "开始发送回调通知(尝试 %s/%s): URL=%s, 任务ID=%s",
                        retry + 1,
                        max_retries,
                        sanitize_url(callback_url),
                        task_id,
                    )

                    # 发送HTTP POST请求
                    response = await self.http_client.post(
                        callback_url,
                        json=result,
                        headers={"Content-Type": "application/json"},
                        timeout=30.0
                    )

                    # 检查响应状态
                    if response.status_code < 300:
                        logger.info(f"回调通知发送成功: {task_id}")
                        return True
                    else:
                        logger.warning(f"回调通知发送失败(尝试 {retry+1}/{max_retries}): {task_id}, 状态码: {response.status_code}, 响应: {response.text}")

                        # 最后一次重试失败
                        if retry == max_retries - 1:
                            logger.error(f"回调通知达到最大重试次数，放弃发送: {task_id}")
                            return False

                        # 等待一段时间后重试
                        await asyncio.sleep(retry_interval)

                except Exception as e:
                    logger.warning(f"回调通知异常(尝试 {retry+1}/{max_retries}): {task_id}, 错误: {str(e)}")

                    # 最后一次重试失败
                    if retry == max_retries - 1:
                        logger.error(f"回调通知达到最大重试次数，放弃发送: {task_id}")
                        return False

                    # 等待一段时间后重试
                    await asyncio.sleep(retry_interval)

            return False

        except Exception as e:
            logger.error(f"发送回调通知异常: {task_id}, 错误: {str(e)}", exc_info=True)
            return False
