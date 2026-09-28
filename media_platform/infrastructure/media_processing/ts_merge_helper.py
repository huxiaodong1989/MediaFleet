#!/usr/bin/env python3
"""
TS转换合并工具

将视频文件先转换为TS格式以统一参数，然后合并，最后转换为MP4格式
解决concat demuxer在处理参数不一致视频时的时长异常问题
"""

import os
import subprocess
import tempfile
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

class TSMergeHelper:
    """TS转换合并助手"""

    def __init__(self):
        self.temp_dir = None

    async def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """获取视频详细信息"""
        try:
            if not os.path.exists(video_path):
                logger.error(f"视频文件不存在: {video_path}")
                return {}

            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_streams",
                "-show_format",
                "-of", "json",
                video_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"ffprobe失败: {result.stderr}")
                return {}

            data = json.loads(result.stdout)

            # 解析视频流和音频流
            video_stream = None
            audio_stream = None

            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video' and video_stream is None:
                    video_stream = stream
                elif stream.get('codec_type') == 'audio' and audio_stream is None:
                    audio_stream = stream

            format_info = data.get('format', {})

            # 解析帧率
            fps = 0.0
            if video_stream:
                r_frame_rate = video_stream.get('r_frame_rate', '0/1')
                try:
                    if '/' in r_frame_rate:
                        num, den = map(int, r_frame_rate.split('/'))
                        fps = num / den if den != 0 else 0.0
                    else:
                        fps = float(r_frame_rate)
                except (ValueError, ZeroDivisionError):
                    fps = 25.0  # 默认帧率

            info = {
                'file_path': video_path,
                'file_name': os.path.basename(video_path),
                'file_size': int(format_info.get('size', 0)),
                'duration': float(format_info.get('duration', 0)),
                'bitrate': int(format_info.get('bit_rate', 0)),
                'format_name': format_info.get('format_name', ''),
                'video_stream': video_stream,
                'audio_stream': audio_stream,
                'has_video': video_stream is not None,
                'has_audio': audio_stream is not None,
                'width': int(video_stream.get('width', 0)) if video_stream else 0,
                'height': int(video_stream.get('height', 0)) if video_stream else 0,
                'fps': fps,
                'video_codec': video_stream.get('codec_name', '') if video_stream else '',
                'audio_codec': audio_stream.get('codec_name', '') if audio_stream else '',
                'sample_rate': int(audio_stream.get('sample_rate', 0)) if audio_stream else 0,
                'channels': int(audio_stream.get('channels', 0)) if audio_stream else 0,
                'pix_fmt': video_stream.get('pix_fmt', '') if video_stream else ''
            }

            return info

        except Exception as e:
            logger.error(f"获取视频信息失败: {e}")
            return {}

    def _determine_unified_params(self, video_infos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """确定统一的视频参数（以第一个非补帧视频为基准）"""
        # 寻找第一个非补帧视频作为基准
        reference_info = None
        for info in video_infos:
            if info and not self._is_filler_video(info['file_path']):
                reference_info = info
                break

        # 如果没有找到非补帧视频，使用第一个视频
        if not reference_info and video_infos:
            reference_info = video_infos[0]

        if not reference_info:
            # 默认参数
            return {
                'width': 1920,
                'height': 1080,
                'fps': 25.0,
                'has_audio': True,
                'sample_rate': 44100,
                'channels': 2
            }

        unified_params = {
            'width': reference_info['width'],
            'height': reference_info['height'],
            'fps': reference_info['fps'],
            'has_audio': reference_info['has_audio'],
            'sample_rate': reference_info['sample_rate'] if reference_info['has_audio'] else 44100,
            'channels': reference_info['channels'] if reference_info['has_audio'] else 2
        }

        logger.info(f"统一参数确定: {unified_params['width']}x{unified_params['height']}@{unified_params['fps']:.2f}fps")
        if unified_params['has_audio']:
            logger.info(f"音频参数: {unified_params['sample_rate']}Hz, {unified_params['channels']}声道")
        else:
            logger.info("无音频流")

        return unified_params

    def _is_filler_video(self, file_path: str) -> bool:
        """判断是否为补帧视频"""
        return 'filler_' in os.path.basename(file_path)

    async def _convert_to_ts(self, input_path: str, output_ts_path: str,
                           unified_params: Dict[str, Any],
                           original_duration: float) -> bool:
        """将视频转换为TS格式，使用统一参数"""
        try:
            is_filler = self._is_filler_video(input_path)

            logger.info(f"转换{'补帧' if is_filler else '视频'}文件: {os.path.basename(input_path)} -> TS")

            # 基础转换命令
            cmd = [
                "ffmpeg",
                "-i", input_path,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "18" if not is_filler else "28",  # 补帧文件可以使用更高压缩
            ]

            # 视频参数
            cmd.extend([
                "-s", f"{unified_params['width']}x{unified_params['height']}",
                "-r", str(unified_params['fps']),
                "-g", str(int(unified_params['fps'] * 2)),  # GOP大小 = 2秒
                "-keyint_min", str(int(unified_params['fps'])),
                "-sc_threshold", "0",
                "-force_key_frames", f"expr:gte(t,n_forced*2)",
            ])

            # 音频参数
            if unified_params['has_audio']:
                cmd.extend([
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-ar", str(unified_params['sample_rate']),
                    "-ac", str(unified_params['channels']),
                ])
            else:
                cmd.extend(["-an"])

            # 输出参数
            cmd.extend([
                "-f", "mpegts",
                "-avoid_negative_ts", "make_zero",
                "-fflags", "+genpts",
                "-y", output_ts_path
            ])

            logger.debug(f"执行转换命令: {' '.join(cmd[:8])}...")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                logger.error(f"TS转换失败: {result.stderr}")
                return False

            if not os.path.exists(output_ts_path):
                logger.error(f"TS文件未生成: {output_ts_path}")
                return False

            # 验证转换结果
            ts_info = await self.get_video_info(output_ts_path)
            if ts_info:
                actual_duration = ts_info['duration']
                duration_diff = abs(actual_duration - original_duration)
                tolerance = max(0.2, original_duration * 0.05)  # 5%误差或0.2秒

                if duration_diff <= tolerance:
                    logger.info(f"TS转换成功: {actual_duration:.3f}秒 (差异{duration_diff:.3f}秒)")
                    return True
                else:
                    logger.warning(f"TS转换时长异常: 预期{original_duration:.3f}秒, 实际{actual_duration:.3f}秒")
                    return False
            else:
                logger.warning(f"无法获取TS文件信息，但转换似乎成功")
                return True

        except subprocess.TimeoutExpired:
            logger.error(f"TS转换超时: {input_path}")
            return False
        except Exception as e:
            logger.error(f"TS转换异常: {e}")
            return False

    async def _merge_ts_files(self, ts_files: List[str], output_ts_path: str) -> bool:
        """合并TS文件"""
        try:
            logger.info(f"合并{len(ts_files)}个TS文件")

            # 方法1: 尝试concat协议
            concat_input = "concat:" + "|".join(ts_files)

            merge_cmd = [
                "ffmpeg",
                "-i", concat_input,
                "-c", "copy",
                "-f", "mpegts",
                "-avoid_negative_ts", "make_zero",
                "-y", output_ts_path
            ]

            logger.debug(f"执行TS合并命令")
            result = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0 and os.path.exists(output_ts_path):
                logger.info(f"concat协议合并成功")
                return True

            logger.warning(f"concat协议合并失败: {result.stderr}")

            # 方法2: 备用filter_complex方案
            logger.info(f"尝试filter_complex备用方案")

            inputs = []
            for ts_file in ts_files:
                inputs.extend(["-i", ts_file])

            # 构建filter_complex字符串
            filter_str = ""
            for i in range(len(ts_files)):
                filter_str += f"[{i}:v][{i}:a]"
            filter_str += f"concat=n={len(ts_files)}:v=1:a=1[outv][outa]"

            backup_cmd = ["ffmpeg"] + inputs + [
                "-filter_complex", filter_str,
                "-map", "[outv]",
                "-map", "[outa]",
                "-c:v", "copy",
                "-c:a", "copy",
                "-f", "mpegts",
                "-y", output_ts_path
            ]

            result = subprocess.run(backup_cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0 and os.path.exists(output_ts_path):
                logger.info(f"filter_complex合并成功")
                return True
            else:
                logger.error(f"filter_complex合并也失败: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"TS合并超时")
            return False
        except Exception as e:
            logger.error(f"TS合并异常: {e}")
            return False

    async def _convert_ts_to_mp4(self, ts_path: str, output_mp4_path: str, has_audio: bool = True) -> bool:
        """将TS文件转换为MP4格式"""
        try:
            logger.info(f"TS转MP4: {os.path.basename(ts_path)} -> {os.path.basename(output_mp4_path)}")

            # 尝试直接copy转换
            cmd = [
                "ffmpeg",
                "-i", ts_path,
                "-c", "copy",
                "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero",
                "-y", output_mp4_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0 and os.path.exists(output_mp4_path):
                logger.info(f"直接copy转换成功")
                return True

            logger.warning(f"copy转换失败，尝试重新编码: {result.stderr}")

            # 重新编码转换
            fallback_cmd = [
                "ffmpeg",
                "-i", ts_path,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
            ]

            if has_audio:
                fallback_cmd.extend([
                    "-c:a", "aac",
                    "-b:a", "128k"
                ])

            fallback_cmd.extend([
                "-movflags", "+faststart",
                "-y", output_mp4_path
            ])

            result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=300)

            if result.returncode == 0 and os.path.exists(output_mp4_path):
                logger.info(f"重新编码转换成功")
                return True
            else:
                logger.error(f"重新编码转换失败: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"TS转MP4超时")
            return False
        except Exception as e:
            logger.error(f"TS转MP4异常: {e}")
            return False

    async def merge_videos_via_ts(self, video_paths: List[str], output_path: str,
                                 temp_dir: Optional[str] = None) -> Tuple[bool, str]:
        """
        通过TS转换策略合并视频

        Args:
            video_paths: 输入视频文件路径列表
            output_path: 输出文件路径
            temp_dir: 临时目录，如果不提供会自动创建

        Returns:
            (success, error_message): 成功标志和错误信息
        """
        logger.info(f"开始TS转换合并，输入文件数量: {len(video_paths)}")

        self.temp_dir = temp_dir
        cleanup_temp = False

        if not self.temp_dir:
            self.temp_dir = tempfile.mkdtemp(prefix="ts_merge_")
            cleanup_temp = True
            logger.info(f"创建临时目录: {self.temp_dir}")

        try:
            # 步骤1: 获取所有视频信息
            logger.info("步骤1: 分析输入视频信息")
            video_infos = []
            total_expected_duration = 0

            for i, video_path in enumerate(video_paths):
                if not os.path.exists(video_path):
                    error_msg = f"输入文件不存在: {video_path}"
                    logger.error(error_msg)
                    return False, error_msg

                info = await self.get_video_info(video_path)
                if not info:
                    error_msg = f"无法获取视频{i+1}信息: {video_path}"
                    logger.error(error_msg)
                    return False, error_msg

                video_infos.append(info)
                total_expected_duration += info['duration']

                file_type = "补帧文件" if self._is_filler_video(video_path) else "视频文件"
                logger.info(f"  {file_type}{i+1}: {info['file_name']}, "
                           f"时长={info['duration']:.3f}秒, "
                           f"分辨率={info['width']}x{info['height']}, "
                           f"帧率={info['fps']:.2f}fps")

            logger.info(f"预期总时长: {total_expected_duration:.3f}秒")

            # 步骤2: 确定统一参数
            logger.info("步骤2: 确定统一视频参数")
            unified_params = self._determine_unified_params(video_infos)

            # 步骤3: 转换所有视频为TS格式
            logger.info("步骤3: 转换所有视频为统一TS格式")
            ts_files = []

            for i, (video_path, video_info) in enumerate(zip(video_paths, video_infos)):
                ts_file = os.path.join(self.temp_dir, f"unified_{i+1:03d}.ts")
                ts_files.append(ts_file)

                success = await self._convert_to_ts(
                    video_path, ts_file, unified_params, video_info['duration']
                )

                if not success:
                    error_msg = f"视频{i+1}转换TS失败"
                    logger.error(error_msg)
                    return False, error_msg

            logger.info(f"所有视频已转换为TS格式")

            # 步骤4: 合并TS文件
            logger.info("步骤4: 合并统一参数的TS文件")
            merged_ts = os.path.join(self.temp_dir, "merged_unified.ts")

            success = await self._merge_ts_files(ts_files, merged_ts)
            if not success:
                error_msg = "TS文件合并失败"
                logger.error(error_msg)
                return False, error_msg

            # 验证合并后的时长
            merged_info = await self.get_video_info(merged_ts)
            if merged_info:
                actual_duration = merged_info['duration']
                duration_diff = abs(actual_duration - total_expected_duration)
                tolerance = max(0.5, total_expected_duration * 0.02)  # 2%误差或0.5秒

                logger.info(f"合并后时长: {actual_duration:.3f}秒, 差异: {duration_diff:.3f}秒")

                if duration_diff > tolerance:
                    logger.warning(f"时长差异较大，但继续转换最终格式")

            # 步骤5: 转换为最终MP4格式
            logger.info("步骤5: 转换为最终MP4格式")
            success = await self._convert_ts_to_mp4(merged_ts, output_path, unified_params['has_audio'])

            if not success:
                error_msg = "TS转MP4失败"
                logger.error(error_msg)
                return False, error_msg

            # 验证最终结果
            final_info = await self.get_video_info(output_path)
            if final_info:
                final_duration = final_info['duration']
                final_diff = abs(final_duration - total_expected_duration)
                final_size = final_info['file_size'] / (1024 * 1024)

                logger.info(f"最终输出: {final_duration:.3f}秒, {final_size:.1f}MB, "
                           f"时长差异: {final_diff:.3f}秒")

                if final_diff <= max(1.0, total_expected_duration * 0.05):  # 5%误差或1秒
                    logger.info("✅ TS转换合并成功，时长验证通过")
                    return True, ""
                else:
                    logger.warning(f"⚠️ TS转换合并完成，但时长差异较大: {final_diff:.3f}秒")
                    return True, f"时长差异较大: {final_diff:.3f}秒"
            else:
                logger.warning("无法获取最终输出文件信息")
                return True, "无法验证最终文件"

        except Exception as e:
            error_msg = f"TS转换合并过程异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg

        finally:
            # 清理临时TS文件
            try:
                if hasattr(self, 'temp_dir') and self.temp_dir:
                    for ts_file in ts_files:
                        if os.path.exists(ts_file):
                            os.remove(ts_file)

                    merged_ts_path = os.path.join(self.temp_dir, "merged_unified.ts")
                    if os.path.exists(merged_ts_path):
                        os.remove(merged_ts_path)

                    if cleanup_temp and os.path.exists(self.temp_dir):
                        os.rmdir(self.temp_dir)
                        logger.info("临时文件清理完成")
            except Exception as e:
                logger.warning(f"临时文件清理失败: {e}")


# 创建全局实例
ts_merge_helper = TSMergeHelper()


async def merge_videos_ts_strategy(video_paths: List[str], output_path: str,
                                  temp_dir: Optional[str] = None) -> Tuple[bool, str]:
    """
    TS转换合并策略的便捷函数

    Args:
        video_paths: 输入视频文件路径列表（包含补帧文件和正常视频文件）
        output_path: 输出MP4文件路径
        temp_dir: 临时目录

    Returns:
        (success, error_message): 成功标志和错误信息
    """
    return await ts_merge_helper.merge_videos_via_ts(video_paths, output_path, temp_dir)
