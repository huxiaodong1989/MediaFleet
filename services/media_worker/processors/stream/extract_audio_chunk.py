import time
import logging
import subprocess
import requests
from threading import Thread, Lock
from queue import Queue, Empty
from datetime import datetime, timedelta
import json
import io
import struct
from collections import namedtuple

# 配置参数
class Config:
    VIDEO_SOURCE = None  # 直播源地址必须由任务参数提供
    API_ENDPOINT = None  # 分块上传接口必须由任务参数提供
    CALLBACK_URL = None  # 回调地址，任务完成后调用
    START_TIME = None  # 开始时间 (datetime对象或ISO格式字符串)
    END_TIME = None  # 结束时间 (datetime对象或ISO格式字符串)
    CHUNK_DURATION = 10  # 分块时长(秒)
    AUDIO_CODEC = "wav"  # 音频编码格式 (修复：从aac改为wav)
    MAX_RETRIES = 3  # 上传失败重试次数
    MAX_QUEUE_SIZE = 10  # 处理队列最大分块数
    STREAM_RETRY_INTERVAL = 3  # 拉流失败重试间隔(秒)
    STREAM_MAX_RETRIES = 10  # 拉流最大重试次数

    # --- 新增音频参数配置 ---
    AUDIO_SAMPLE_RATE = 16000  # 采样率 (Hz)
    AUDIO_CHANNELS = 1  # 声道数
    AUDIO_SAMPLE_WIDTH = 2  # 采样位深 (字节), pcm_s16le 为 2字节/16位

# 定义内存中的音频分块结构
AudioChunk = namedtuple('AudioChunk', ['chunk_id', 'data', 'timestamp', 'format'])

# 初始化日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("audio_chunker.log"),
        logging.StreamHandler()
    ]
)

class ExtractAudioChunk:
    def __init__(self, video_source=None, api_endpoint=None, callback_url=None,
                 start_time=None, end_time=None, chunk_duration=None):
        # 允许通过参数覆盖配置
        self.video_source = video_source or Config.VIDEO_SOURCE
        self.api_endpoint = api_endpoint or Config.API_ENDPOINT
        if not self.video_source or not self.api_endpoint:
            raise ValueError("video_source 和 api_endpoint 必须由任务参数提供")
        self.callback_url = callback_url or Config.CALLBACK_URL
        self.chunk_duration = chunk_duration or Config.CHUNK_DURATION

        # 处理时间参数
        self.start_time = self._parse_time(start_time or Config.START_TIME)
        self.end_time = self._parse_time(end_time or Config.END_TIME)

        # 使用内存队列存储AudioChunk对象
        self.chunk_queue = Queue(maxsize=Config.MAX_QUEUE_SIZE)
        self.lock = Lock()
        self.ffmpeg_process = None
        self.running = False
        self.task_started = False
        self.task_completed = False
        self.session = requests.Session()
        self.stream_retry_count = 0
        self.total_chunks_processed = 0
        self.total_chunks_uploaded = 0
        self.start_actual_time = None
        self.end_actual_time = None
        self.chunk_counter = 0  # 分块计数器

        # --- 新增：计算每个分块的精确字节数 ---
        bytes_per_second = Config.AUDIO_SAMPLE_RATE * Config.AUDIO_CHANNELS * Config.AUDIO_SAMPLE_WIDTH
        self.bytes_per_chunk = bytes_per_second * self.chunk_duration

    def _parse_time(self, time_input):
        """解析时间输入，支持datetime对象和ISO格式字符串"""
        if time_input is None:
            return None
        if isinstance(time_input, datetime):
            return time_input
        if isinstance(time_input, str):
            try:
                return datetime.fromisoformat(time_input.replace('Z', '+00:00'))
            except ValueError:
                try:
                    return datetime.strptime(time_input, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    logging.error(f"无法解析时间格式: {time_input}")
                    return None
        return None

    def _should_start_task(self):
        """检查是否应该开始任务"""
        if self.start_time is None:
            return True
        return datetime.now() >= self.start_time

    def _should_stop_task(self):
        """检查是否应该停止任务"""
        if self.end_time is None:
            return False
        return datetime.now() >= self.end_time

    def start(self):
        """启动分块处理流程"""
        print("开始分块处理流程")
        self.running = True

        Thread(target=self._time_monitor, daemon=True).start()
        Thread(target=self._upload_worker, daemon=True).start()

        logging.info(f"音频分块处理器已启动")
        if self.start_time:
            logging.info(f"计划开始时间: {self.start_time}")
        if self.end_time:
            logging.info(f"计划结束时间: {self.end_time}")

    def stop(self):
        """停止处理流程"""
        self.running = False
        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=5)
            except:
                if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
                    self.ffmpeg_process.kill()

        if not self.task_completed:
            self.end_actual_time = datetime.now()
            self.task_completed = True
            self._send_callback('stopped')

        logging.info("音频分块处理器已停止")

    def _time_monitor(self):
        """时间监控线程"""
        while self.running:
            try:
                if not self.task_started and self._should_start_task():
                    self.task_started = True
                    self.start_actual_time = datetime.now()
                    logging.info(f"任务开始执行，实际开始时间: {self.start_actual_time}")
                    Thread(target=self._run_ffmpeg_chunker, daemon=True).start()

                if self.task_started and not self.task_completed and self._should_stop_task():
                    self.end_actual_time = datetime.now()
                    self.task_completed = True
                    logging.info(f"到达结束时间，任务完成。实际结束时间: {self.end_actual_time}")
                    self._send_callback('completed')
                    self.stop()
                    break

                time.sleep(1)
            except Exception as e:
                logging.error(f"时间监控线程错误: {e}")

    def _run_ffmpeg_chunker(self):
        """使用FFmpeg生成音频流，支持自动重试"""
        while self.task_started and not self.task_completed and self.running:
            try:
                if self._should_stop_task():
                    logging.info("到达结束时间，停止FFmpeg分块")
                    break

                ffmpeg_cmd = [
                    'ffmpeg',
                    '-hide_banner', '-loglevel', 'error', # 精简日志输出
                    '-analyzeduration', '10M',
                    '-probesize', '10M',
                    '-i', self.video_source,
                    '-reconnect', '1',
                    '-reconnect_at_eof', '1',
                    '-reconnect_streamed', '1',
                    '-reconnect_delay_max', '5',
                    '-vn',
                    '-acodec', 'pcm_s16le',
                    '-ac', str(Config.AUDIO_CHANNELS),
                    '-ar', str(Config.AUDIO_SAMPLE_RATE),
                    '-f', 'wav',
                    '-'  # 输出到stdout
                ]

                logging.info(f"启动FFmpeg进程，拉流地址: {self.video_source}")
                self.ffmpeg_process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

                self.stream_retry_count = 0

                data_reader_thread = Thread(target=self._read_audio_data, daemon=True)
                data_reader_thread.start()

                error_monitor_thread = Thread(target=self._monitor_ffmpeg_stderr, daemon=True)
                error_monitor_thread.start()

                return_code = self.ffmpeg_process.wait() # 等待进程结束
                data_reader_thread.join() # 确保读取线程也结束

                if self.running and not self.task_completed:
                    if return_code != 0:
                        raise Exception(f"FFmpeg进程异常退出，返回码: {return_code}")
                    else:
                        logging.info("FFmpeg进程正常结束，但任务未完成，可能是流中断")
                        raise Exception("FFmpeg进程意外结束")

            except Exception as e:
                logging.error(f"FFmpeg主循环错误: {e}")
                self.stream_retry_count += 1

                if self._should_stop_task():
                    logging.info("到达结束时间，停止重试")
                    break

                if self.stream_retry_count <= Config.STREAM_MAX_RETRIES:
                    logging.info(f"拉流失败，{Config.STREAM_RETRY_INTERVAL}秒后进行第{self.stream_retry_count}次重试")
                    time.sleep(Config.STREAM_RETRY_INTERVAL)
                else:
                    logging.error(f"拉流重试次数达到上限({Config.STREAM_MAX_RETRIES})，任务终止")
                    self.task_completed = True
                    self.end_actual_time = datetime.now()
                    self._send_callback('failed', error='拉流重试次数超限')
                    self.stop()
                    break

    def _generate_wav_header(self, pcm_data_size):
        """为给定的PCM数据大小生成一个44字节的WAV文件头"""
        header = io.BytesIO()
        # RIFF chunk descriptor
        header.write(b'RIFF')
        # chunk size (36 + pcm_data_size)
        header.write(struct.pack('<I', 36 + pcm_data_size))
        header.write(b'WAVE')
        # "fmt " sub-chunk
        header.write(b'fmt ')
        header.write(struct.pack('<I', 16))  # sub-chunk 1 size (16 for PCM)
        header.write(struct.pack('<H', 1))   # audio format (1 for PCM)
        header.write(struct.pack('<H', Config.AUDIO_CHANNELS))
        header.write(struct.pack('<I', Config.AUDIO_SAMPLE_RATE))
        byte_rate = Config.AUDIO_SAMPLE_RATE * Config.AUDIO_CHANNELS * Config.AUDIO_SAMPLE_WIDTH
        header.write(struct.pack('<I', byte_rate))
        block_align = Config.AUDIO_CHANNELS * Config.AUDIO_SAMPLE_WIDTH
        header.write(struct.pack('<H', block_align))
        header.write(struct.pack('<H', Config.AUDIO_SAMPLE_WIDTH * 8)) # bits per sample
        # "data" sub-chunk
        header.write(b'data')
        header.write(struct.pack('<I', pcm_data_size))
        return header.getvalue()

    def _read_exact(self, stream, num_bytes):
        """从流中精确读取指定数量的字节"""
        buf = io.BytesIO()
        bytes_remaining = num_bytes
        while bytes_remaining > 0 and self.running:
            chunk = stream.read(bytes_remaining)
            if not chunk:
                # 流已结束
                break
            buf.write(chunk)
            bytes_remaining -= len(chunk)
        return buf.getvalue()

    def _read_audio_data(self):
        """从FFmpeg stdout读取音频数据并分块处理 (已重写)"""
        if not self.ffmpeg_process or not self.ffmpeg_process.stdout:
            return

        try:
            # 1. 读取并丢弃FFmpeg生成的整体WAV头 (44字节)
            # 这个头是针对整个（可能无限长的）流的，我们不需要它
            self._read_exact(self.ffmpeg_process.stdout, 44)
            logging.info("已读取并忽略FFmpeg初始WAV头，开始分块处理。")

            while self.running and not self.task_completed:
                if self.ffmpeg_process.poll() is not None:
                    logging.info("FFmpeg进程已停止，停止读取数据。")
                    break

                # 2. 精确读取一个分块所需的PCM数据
                pcm_data = self._read_exact(self.ffmpeg_process.stdout, self.bytes_per_chunk)

                if not pcm_data:
                    # 如果读不到数据，说明流已结束
                    break

                # 3. 为这个PCM数据块生成一个新的、独立的文件头
                wav_header = self._generate_wav_header(len(pcm_data))

                # 4. 拼接文件头和数据，形成一个完整的WAV文件
                full_wav_data = wav_header + pcm_data

                # 5. 创建内存分块并放入队列
                self._create_memory_chunk(full_wav_data)

        except Exception as e:
            logging.error(f"音频数据读取线程错误: {e}", exc_info=True)

    def _create_memory_chunk(self, chunk_data):
        """创建内存中的音频分块"""
        try:
            self.chunk_counter += 1
            chunk_id = f"chunk_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.chunk_counter:06d}"

            audio_chunk = AudioChunk(
                chunk_id=chunk_id,
                data=chunk_data,
                timestamp=datetime.now(),
                format=Config.AUDIO_CODEC # 现在是 'wav'
            )

            with self.lock:
                if not self.chunk_queue.full():
                    self.chunk_queue.put_nowait(audio_chunk)
                    self.total_chunks_processed += 1
                    logging.info(f"创建内存分块: {chunk_id} ({len(chunk_data)} 字节), 格式: {audio_chunk.format}")
                else:
                    logging.warning(f"队列已满，丢弃分块: {chunk_id}")

        except Exception as e:
            logging.error(f"创建内存分块时出错: {e}")

    def _monitor_ffmpeg_stderr(self):
        """监控FFmpeg错误输出"""
        if not self.ffmpeg_process or not self.ffmpeg_process.stderr:
            return
        try:
            for line in iter(self.ffmpeg_process.stderr.readline, b''):
                line_str = line.decode('utf-8', errors='ignore').strip()
                if line_str:
                    logging.error(f"FFmpeg stderr: {line_str}")
        except Exception as e:
            logging.debug(f"FFmpeg错误输出监控异常: {e}")

    def _upload_worker(self):
        """分块上传工作线程"""
        while self.running or not self.chunk_queue.empty():
            try:
                audio_chunk = self.chunk_queue.get(timeout=1.0)

                success = False
                retry_count = 0

                while retry_count <= Config.MAX_RETRIES and not success:
                    file_obj = None
                    try:
                        file_obj = io.BytesIO(audio_chunk.data)
                        files = {
                            'file': (
                                f"{audio_chunk.chunk_id}.{audio_chunk.format}",
                                file_obj,
                                f'audio/{audio_chunk.format}'
                            )
                        }

                        response = self.session.post(
                            self.api_endpoint,
                            files=files,
                            data={
                                'chunk_id': audio_chunk.chunk_id,
                                'timestamp': audio_chunk.timestamp.isoformat(),
                                'format': audio_chunk.format,
                                'size': len(audio_chunk.data)
                            },
                            timeout=15
                        )

                        if response.status_code == 200:
                            success = True
                            self.total_chunks_uploaded += 1
                            logging.info(f"分块上传成功: {audio_chunk.chunk_id}")
                        else:
                            raise Exception(f"服务器返回状态码 {response.status_code}")

                    except Exception as e:
                        retry_count += 1
                        if retry_count <= Config.MAX_RETRIES:
                            logging.warning(f"分块上传失败: {audio_chunk.chunk_id}, 错误: {e}, 重试 {retry_count}/{Config.MAX_RETRIES}")
                            time.sleep(1)
                        else:
                            logging.error(f"分块上传失败: {audio_chunk.chunk_id}, 错误: {e}, 已达最大重试次数")
                    finally:
                        if file_obj:
                            file_obj.close()

                self.chunk_queue.task_done()

            except Empty:
                if not self.running:
                    break # 如果程序已停止且队列为空，则退出
                continue
            except Exception as e:
                logging.error(f"上传工作线程错误: {e}")

    def _send_callback(self, status, error=None):
        """发送回调通知"""
        if not self.callback_url:
            return

        try:
            callback_data = {
                'status': status,
                'start_time': self.start_actual_time.isoformat() if self.start_actual_time else None,
                'end_time': self.end_actual_time.isoformat() if self.end_actual_time else None,
                'total_chunks_processed': self.total_chunks_processed,
                'total_chunks_uploaded': self.total_chunks_uploaded,
                'video_source': self.video_source,
                'timestamp': datetime.now().isoformat()
            }

            if error:
                callback_data['error'] = error

            response = self.session.post(
                self.callback_url,
                json=callback_data,
                timeout=10
            )

            if response.status_code == 200:
                logging.info(f"回调通知发送成功: {status}")
            else:
                logging.warning(f"回调通知发送失败: {response.status_code}")

        except Exception as e:
            logging.error(f"发送回调通知时出错: {e}")

    def get_status(self):
        """获取当前状态"""
        return {
            'running': self.running,
            'task_started': self.task_started,
            'task_completed': self.task_completed,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'start_actual_time': self.start_actual_time.isoformat() if self.start_actual_time else None,
            'end_actual_time': self.end_actual_time.isoformat() if self.end_actual_time else None,
            'total_chunks_processed': self.total_chunks_processed,
            'total_chunks_uploaded': self.total_chunks_uploaded,
            'stream_retry_count': self.stream_retry_count,
            'video_source': self.video_source,
            'callback_url': self.callback_url,
            'ffmpeg_running': self.ffmpeg_process is not None and self.ffmpeg_process.poll() is None,
            'queue_size': self.chunk_queue.qsize(),
            'chunk_counter': self.chunk_counter
        }

# 使用示例 (保持不变)
# def main():
# ...
