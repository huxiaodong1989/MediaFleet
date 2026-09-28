

from datetime import datetime, timedelta
import logging
import subprocess
import requests
import os
import platform
import tempfile
import shutil
import time
import threading
import json

logger = logging.getLogger(__name__)


class RealTimeExtractAudio:
    def __init__(self):
        pass

    def extract_audio_url(self, rtmp_url: str, callback_url: str, start_time: datetime, end_time: datetime, result_callback_url: str = None):
        """
        从RTMP直播流中提取音频并通过HTTP POST流式传输到指定的回调URL

        Args:
            rtmp_url: RTMP直播流URL
            callback_url: 回调URL，用于接收提取的音频流
            start_time: 开始提取音频的时间
            end_time: 结束提取音频的时间
            result_callback_url: 任务完成后的结果回调URL，可选
        """


        logger.info("开始提取音频")
        # 检查系统类型，确定使用的命令和路径分隔符
        is_windows = platform.system() == 'Windows'

        # 任务状态
        task_status = {
            "status": "waiting",
            "message": "等待开始时间",
            "retry_count": 0,
            "error": None
        }

        # 定义结果回调函数
        def send_result_callback(result):
            if result_callback_url:
                try:
                    requests.post(
                        result_callback_url,
                        json=result,
                        headers={"Content-Type": "application/json"}
                    )
                    print(f"结果回调成功: {result}")
                except Exception as e:
                    print(f"结果回调失败: {str(e)}")

        try:
            # 创建临时目录用于存放临时文件
            temp_dir = tempfile.mkdtemp()

            # 等待直到开始时间
            now = datetime.now()
            if now < start_time:
                wait_seconds = (start_time - now).total_seconds()
                print(f"等待 {wait_seconds} 秒后开始提取音频...")
                time.sleep(wait_seconds)

            # 计算任务最大持续时间
            max_duration = (end_time - datetime.now()).total_seconds()
            if max_duration <= 0:
                error_msg = "结束时间已过，任务取消"
                print(error_msg)
                task_status = {"status": "error", "message": error_msg}
                send_result_callback(task_status)
                return task_status

            # 设置任务结束的事件标志
            task_end_event = threading.Event()

            # 设置定时器，在结束时间触发
            def end_task():
                print("到达结束时间，停止任务")
                task_end_event.set()

            # 计算结束时间与当前时间的差值（秒）
            end_timer = threading.Timer((end_time - datetime.now()).total_seconds(), end_task)
            end_timer.daemon = True
            end_timer.start()

            # 重试逻辑
            retry_count = 0
            max_retries = 1000  # 设置一个足够大的值，实际会受到结束时间控制
            retry_interval = 3  # 重试间隔，单位秒

            while retry_count <= max_retries and not task_end_event.is_set():
                try:
                    task_status["status"] = "processing"
                    task_status["message"] = "正在提取音频"
                    task_status["retry_count"] = retry_count

                    print(f"开始提取音频，尝试次数: {retry_count + 1}")

                    # 构建FFmpeg命令，从RTMP流中提取音频
                    ffmpeg_cmd = ['ffmpeg',
                                 '-i', rtmp_url,
                                 '-vn',  # 禁用视频
                                 '-acodec', 'pcm_s16le',  # 音频编码为PCM
                                 '-ar', '44100',  # 采样率
                                 '-ac', '2',  # 双声道
                                 '-f', 'wav',  # 输出格式
                                 '-']  # 输出到标准输出

                    # 启动FFmpeg进程
                    ffmpeg_process = subprocess.Popen(
                        ffmpeg_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        bufsize=10**8,  # 使用大缓冲区
                        shell=is_windows  # Windows下需要shell=True
                    )

                    # 设置块大小
                    block_size = 4096  # 4KB块

                    # 创建一个线程来监控FFmpeg进程
                    def monitor_ffmpeg():
                        try:
                            while ffmpeg_process.poll() is None and not task_end_event.is_set():
                                time.sleep(0.5)

                            if not task_end_event.is_set() and ffmpeg_process.returncode != 0:
                                # FFmpeg进程异常退出但未到结束时间
                                print("FFmpeg进程异常退出，准备重试")
                            else:
                                # 到达结束时间或正常结束，终止FFmpeg进程
                                if ffmpeg_process.poll() is None:
                                    print("强制终止FFmpeg进程")
                                    if is_windows:
                                        subprocess.call(['taskkill', '/F', '/T', '/PID', str(ffmpeg_process.pid)])
                                    else:
                                        ffmpeg_process.terminate()
                                        try:
                                            ffmpeg_process.wait(timeout=5)
                                        except subprocess.TimeoutExpired:
                                            ffmpeg_process.kill()
                        except Exception as e:
                            print(f"监控线程错误: {str(e)}")

                    monitor_thread = threading.Thread(target=monitor_ffmpeg)
                    monitor_thread.daemon = True
                    monitor_thread.start()

                    # 流式发送音频数据到回调URL
                    try:
                        with requests.post(callback_url, data=self._stream_generator(ffmpeg_process.stdout, block_size, task_end_event),
                                        headers={'Content-Type': 'audio/wav'}, stream=True) as response:
                            print("开始传输音频...")
                            # 检查响应状态
                            if response.status_code != 200:
                                print(f"警告：回调服务器返回错误状态码: {response.status_code}，但将继续处理")
                    except Exception as stream_error:
                        print(f"音频流传输错误: {str(stream_error)}，但将继续处理")

                    print("join start")
                    # 等待监控线程完成，但设置超时时间
                    monitor_thread.join()  # 最多等待5秒
                    print("join end")

                    # 如果监控线程仍在运行，不再等待它
                    if monitor_thread.is_alive():
                        print("监控线程未在预期时间内结束，继续执行")

                    # 如果任务已结束，跳出循环
                    if task_end_event.is_set():
                        break

                    # 检查FFmpeg是否成功
                    if ffmpeg_process.returncode != 0:
                        stderr = ffmpeg_process.stderr.read().decode('utf-8', errors='ignore')
                        print(f"FFmpeg处理失败: {stderr}")
                        # 不抛出异常，而是继续重试
                        retry_count += 1
                        if not task_end_event.is_set():
                            print(f"等待 {retry_interval} 秒后重试...")
                            time.sleep(retry_interval)
                            continue
                    else:
                        # 成功完成，跳出循环
                        break

                except Exception as e:
                    # 捕获错误但不终止，准备重试
                    print(f"处理过程中出错: {str(e)}")
                    retry_count += 1
                    task_status["error"] = str(e)

                    if not task_end_event.is_set():
                        print(f"等待 {retry_interval} 秒后重试...")
                        time.sleep(retry_interval)
                    else:
                        break

            # 取消定时器（如果还未触发）
            end_timer.cancel()

            # 根据任务结束原因设置状态
            if task_end_event.is_set():
                task_status = {"status": "completed", "message": "到达结束时间，任务已完成", "retry_count": retry_count}
            elif retry_count > max_retries:
                task_status = {"status": "error", "message": "超过最大重试次数", "retry_count": retry_count}
            else:
                task_status = {"status": "success", "message": "音频提取和传输完成", "retry_count": retry_count}

            # 发送结果回调
            send_result_callback(task_status)
            return task_status

        except Exception as e:
            # 捕获并记录错误
            error_msg = f"音频提取失败: {str(e)}"
            print(error_msg)
            task_status = {"status": "error", "message": error_msg}
            send_result_callback(task_status)
            return task_status

        finally:
            # 清理临时目录
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

    def _stream_generator(self, stream, block_size, end_event=None):
        """
        生成器函数，用于流式读取和传输数据

        Args:
            stream: 数据流
            block_size: 块大小
            end_event: 结束事件，用于控制生成器停止

        Yields:
            数据块
        """
        try:
            import select
            import os
            import platform
            is_windows = platform.system() == 'Windows'

            if is_windows:
                # Windows下使用轮询方式读取，避免阻塞
                while True:
                    # 先检查是否需要结束
                    if end_event and end_event.is_set():
                        break

                    # 尝试读取数据，设置超时
                    data = None
                    try:
                        # 非阻塞方式读取，如果没有数据立即返回
                        import msvcrt
                        if msvcrt.kbhit():
                            data = stream.read(block_size)
                        else:
                            # 短暂休眠，避免CPU占用过高
                            time.sleep(0.1)
                            # 再次检查结束事件
                            if end_event and end_event.is_set():
                                break
                            # 尝试读取数据
                            data = stream.read(block_size)
                    except Exception as e:
                        print(f"读取流错误: {str(e)}")
                        time.sleep(0.1)
                        continue

                    if not data:
                        # 如果没有数据，短暂休眠后继续
                        time.sleep(0.1)
                        continue

                    yield data
            else:
                # Unix系统使用select进行非阻塞IO
                while True:
                    # 先检查是否需要结束
                    if end_event and end_event.is_set():
                        break

                    # 使用select检查流是否可读，设置超时为0.1秒
                    r, _, _ = select.select([stream], [], [], 0.1)
                    if stream in r:
                        data = stream.read(block_size)
                        if not data:
                            break
                        yield data
                    # 如果select超时，再次检查结束事件
                    elif end_event and end_event.is_set():
                        break
        except Exception as e:
            print(f"流读取错误: {str(e)}")
        finally:
            # 确保流被关闭
            try:
                stream.close()
            except:
                pass

    def extract_audio_file(self, rtmp_url: str, output_file: str):
        """
        从RTMP直播流中提取音频并保存到本地文件

        Args:
            rtmpUrl: RTMP直播流URL
            output_file: 输出音频文件路径
        """
        import subprocess
        import os
        import platform
        import tempfile
        import shutil
        import time

        # 检查系统类型，确定使用的命令和路径分隔符
        is_windows = platform.system() == 'Windows'

        try:
            # 创建临时目录用于存放临时文件
            temp_dir = tempfile.mkdtemp()

            print(f"开始提取音频，输出到: {output_file}")

            # 构建FFmpeg命令，从RTMP流中提取音频并保存到文件
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', rtmp_url,
                '-vn',  # 禁用视频
                '-acodec', 'pcm_s16le',  # 音频编码为PCM
                '-ar', '44100',  # 采样率
                '-ac', '2',  # 双声道
                '-f', 'wav',  # 输出格式
                output_file  # 输出到文件
            ]

            print("执行命令:", " ".join(ffmpeg_cmd))

            # 启动FFmpeg进程
            ffmpeg_process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=10**8,  # 使用大缓冲区
                shell=is_windows  # Windows下需要shell=True
            )

            # 监控FFmpeg输出
            print("开始提取音频...")
            while True:
                # 检查进程是否结束
                if ffmpeg_process.poll() is not None:
                    break

                # 读取进度信息
                line = ffmpeg_process.stderr.readline().decode('utf-8', errors='ignore')
                if line.strip():
                    print(line.strip())

                time.sleep(0.1)  # 避免CPU占用过高

            # 检查FFmpeg是否成功
            if ffmpeg_process.returncode != 0:
                stderr = ffmpeg_process.stderr.read().decode('utf-8', errors='ignore')
                raise Exception(f"FFmpeg处理失败: {stderr}")

            print(f"音频提取完成，保存到: {output_file}")
            return {"status": "success", "message": f"音频已保存到 {output_file}"}

        except Exception as e:
            # 捕获并记录错误
            error_msg = f"音频提取失败: {str(e)}"
            print(error_msg)
            return {"status": "error", "message": error_msg}

        finally:
            # 清理临时目录
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
