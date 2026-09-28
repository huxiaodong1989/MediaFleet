#!/bin/bash
# FFmpeg 和 FFprobe 诊断脚本

echo "=========================================="
echo "FFmpeg 环境诊断"
echo "=========================================="

echo ""
echo "1. 检查 ffmpeg 命令"
if command -v ffmpeg &> /dev/null; then
    echo "✓ ffmpeg 已安装"
    ffmpeg -version | head -n 1
else
    echo "✗ ffmpeg 未找到"
fi

echo ""
echo "2. 检查 ffprobe 命令"
if command -v ffprobe &> /dev/null; then
    echo "✓ ffprobe 已安装"
    ffprobe -version | head -n 1
else
    echo "✗ ffprobe 未找到"
fi

echo ""
echo "3. 检查 PATH 环境变量"
echo "PATH=$PATH"

echo ""
echo "4. 查找 ffmpeg 和 ffprobe 文件位置"
echo "ffmpeg 位置:"
which ffmpeg || echo "未找到"
echo "ffprobe 位置:"
which ffprobe || echo "未找到"

echo ""
echo "5. 检查 /usr/bin 目录"
ls -la /usr/bin/ff* 2>/dev/null || echo "未找到 ff* 相关文件"

echo ""
echo "6. 测试 ffprobe 功能"
if command -v ffprobe &> /dev/null; then
    # 创建一个测试文件路径（如果存在的话）
    TEST_FILE="/usr/local/zlmediakit/www/record/computerDesktops/2001478074700423168/2025-12-19/16-24-00-0.mp4"
    if [ -f "$TEST_FILE" ]; then
        echo "测试文件: $TEST_FILE"
        ffprobe -v quiet -print_format json -show_format "$TEST_FILE" | head -n 20
    else
        echo "测试文件不存在: $TEST_FILE"
    fi
else
    echo "跳过测试（ffprobe 未安装）"
fi

echo ""
echo "=========================================="
echo "诊断完成"
echo "=========================================="
