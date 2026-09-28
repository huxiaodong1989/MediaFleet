#!/usr/bin/env python
"""
封面提取性能基准测试脚本

比较 OpenCV vs FFmpeg 的性能差异

使用方法:
    python scripts/benchmark_cover_extraction.py /path/to/video.mp4
"""
import os
import sys
import time
import asyncio
import argparse
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from media_platform.infrastructure.media_processing.cover_extractor import CoverExtractor
from media_platform.infrastructure.media_processing.opencv_cover_extractor import (
    OpenCVCoverExtractor,
)


async def benchmark_opencv(video_path: str, output_dir: str, iterations: int = 5):
    """OpenCV 性能测试"""
    print("\n" + "="*60)
    print("OpenCV 引擎性能测试")
    print("="*60)

    extractor = OpenCVCoverExtractor()
    times = []

    for i in range(iterations):
        cover_info = {"timestamp": 5 + i * 2}

        start_time = time.time()
        result = await extractor.extract_timestamp_cover(
            video_path, cover_info, output_dir
        )
        elapsed_ms = (time.time() - start_time) * 1000
        times.append(elapsed_ms)

        status = "✓" if result else "✗"
        print(f"  [{i+1}/{iterations}] {status} 耗时: {elapsed_ms:6.1f}ms - {result}")

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print(f"\n统计:")
    print(f"  平均耗时: {avg_time:.1f}ms")
    print(f"  最快: {min_time:.1f}ms")
    print(f"  最慢: {max_time:.1f}ms")

    return avg_time


async def benchmark_ffmpeg(video_path: str, output_dir: str, iterations: int = 5):
    """FFmpeg 性能测试"""
    print("\n" + "="*60)
    print("FFmpeg 引擎性能测试")
    print("="*60)

    # 强制使用 FFmpeg
    os.environ["USE_OPENCV_EXTRACTOR"] = "false"
    extractor = CoverExtractor()
    times = []

    for i in range(iterations):
        cover_info = {"timestamp": 5 + i * 2}

        start_time = time.time()
        result = await extractor._extract_timestamp_cover_ffmpeg(
            video_path, cover_info, output_dir
        )
        elapsed_ms = (time.time() - start_time) * 1000
        times.append(elapsed_ms)

        status = "✓" if result else "✗"
        print(f"  [{i+1}/{iterations}] {status} 耗时: {elapsed_ms:6.1f}ms - {result}")

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print(f"\n统计:")
    print(f"  平均耗时: {avg_time:.1f}ms")
    print(f"  最快: {min_time:.1f}ms")
    print(f"  最慢: {max_time:.1f}ms")

    return avg_time


async def benchmark_concurrent(video_path: str, output_dir: str, concurrent: int = 10):
    """并发性能测试"""
    print("\n" + "="*60)
    print(f"OpenCV 并发测试 ({concurrent} 个任务)")
    print("="*60)

    extractor = OpenCVCoverExtractor()

    tasks = []
    for i in range(concurrent):
        cover_info = {"timestamp": i * 2}
        task = extractor.extract_timestamp_cover(
            video_path, cover_info, output_dir
        )
        tasks.append(task)

    start_time = time.time()
    results = await asyncio.gather(*tasks)
    elapsed_ms = (time.time() - start_time) * 1000

    success_count = sum(1 for r in results if r is not None)

    print(f"  总耗时: {elapsed_ms:.1f}ms")
    print(f"  成功: {success_count}/{concurrent}")
    print(f"  平均每个: {elapsed_ms/concurrent:.1f}ms")

    return elapsed_ms


async def main():
    parser = argparse.ArgumentParser(description="封面提取性能基准测试")
    parser.add_argument("video", help="测试视频路径")
    parser.add_argument("--output", "-o", default="/tmp/cover_benchmark",
                       help="输出目录")
    parser.add_argument("--iterations", "-n", type=int, default=5,
                       help="迭代次数")
    parser.add_argument("--concurrent", "-c", type=int, default=10,
                       help="并发任务数")
    parser.add_argument("--skip-ffmpeg", action="store_true",
                       help="跳过 FFmpeg 测试")

    args = parser.parse_args()

    # 验证视频文件
    if not os.path.exists(args.video):
        print(f"错误: 视频文件不存在: {args.video}")
        sys.exit(1)

    # 创建输出目录
    os.makedirs(args.output, exist_ok=True)

    print("\n" + "="*60)
    print("封面提取性能基准测试")
    print("="*60)
    print(f"视频路径: {args.video}")
    print(f"输出目录: {args.output}")
    print(f"迭代次数: {args.iterations}")

    # 获取视频信息
    try:
        extractor = OpenCVCoverExtractor()
        info = extractor.get_video_info(args.video)
        if info:
            print(f"\n视频信息:")
            print(f"  分辨率: {info['width']}x{info['height']}")
            print(f"  FPS: {info['fps']:.2f}")
            print(f"  时长: {info['duration']:.2f}秒")
    except Exception as e:
        print(f"无法获取视频信息: {e}")

    # 测试 OpenCV
    opencv_time = await benchmark_opencv(args.video, args.output, args.iterations)

    # 测试 FFmpeg
    ffmpeg_time = None
    if not args.skip_ffmpeg:
        try:
            ffmpeg_time = await benchmark_ffmpeg(args.video, args.output, args.iterations)
        except Exception as e:
            print(f"\nFFmpeg 测试失败: {e}")

    # 并发测试
    await benchmark_concurrent(args.video, args.output, args.concurrent)

    # 性能对比
    print("\n" + "="*60)
    print("性能对比总结")
    print("="*60)
    print(f"OpenCV 平均耗时: {opencv_time:.1f}ms")
    if ffmpeg_time:
        print(f"FFmpeg 平均耗时: {ffmpeg_time:.1f}ms")
        speedup = ffmpeg_time / opencv_time
        print(f"性能提升: {speedup:.1f}x")
    print("="*60)

    print(f"\n输出文件位置: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
