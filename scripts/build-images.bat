@echo off
chcp 65001 >nul
setlocal

set VERSION=%~1
if "%VERSION%"=="" set VERSION=latest

set CONTROL_CENTER_IMAGE=mediafleet-control-center:%VERSION%
set RECORDER_NODE_IMAGE=mediafleet-recorder-node:%VERSION%
set MEDIA_WORKER_IMAGE=mediafleet-worker:%VERSION%

echo [1/3] 构建调用中心 %CONTROL_CENTER_IMAGE%
docker build -t %CONTROL_CENTER_IMAGE% -f deploy\dockerfiles\Dockerfile.control-center . || exit /b 1

echo [2/3] 构建录制节点 %RECORDER_NODE_IMAGE%
docker build -t %RECORDER_NODE_IMAGE% -f deploy\dockerfiles\Dockerfile.recorder-node . || exit /b 1

echo [3/3] 构建媒体处理节点 %MEDIA_WORKER_IMAGE%
docker build -t %MEDIA_WORKER_IMAGE% -f deploy\dockerfiles\Dockerfile.media-worker . || exit /b 1

echo.
echo 三个服务镜像构建完成：
echo   %CONTROL_CENTER_IMAGE%
echo   %RECORDER_NODE_IMAGE%
echo   %MEDIA_WORKER_IMAGE%
endlocal
