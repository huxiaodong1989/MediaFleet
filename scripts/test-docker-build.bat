@echo off
chcp 65001 >nul
setlocal

set TEST_TAG=test-%RANDOM%

call scripts\build-images.bat %TEST_TAG% || exit /b 1

echo 验证调用中心入口
docker run --rm mediafleet-control-center:%TEST_TAG% .venv/bin/python -c "from services.control_center.main import app; print(app.title)" || exit /b 1

echo 验证录制节点入口和 FFmpeg
docker run --rm mediafleet-recorder-node:%TEST_TAG% sh -c ".venv/bin/python -c 'from services.recorder_node.main import app; print(app.title)' ^&^& ffmpeg -version" || exit /b 1

echo 验证媒体处理节点入口和 FFmpeg
docker run --rm mediafleet-worker:%TEST_TAG% sh -c ".venv/bin/python -c 'from services.media_worker.main import app; print(app.title)' ^&^& ffmpeg -version" || exit /b 1

docker rmi mediafleet-control-center:%TEST_TAG% >nul 2>&1
docker rmi mediafleet-recorder-node:%TEST_TAG% >nul 2>&1
docker rmi mediafleet-worker:%TEST_TAG% >nul 2>&1

echo Docker 构建和入口验证通过。
endlocal
