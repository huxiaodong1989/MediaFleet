@echo off
chcp 65001 >nul
setlocal

set COMPOSE_FILE=deploy\compose\docker-compose.yml
set COMPOSE_CMD=docker compose --env-file .env -f %COMPOSE_FILE%

if not exist .env (
    echo 未找到根目录 .env，请先根据 .env.example 创建并填写真实配置。
    exit /b 1
)

docker compose version >nul 2>&1 || (
    echo Docker Compose 不可用，请先启动 Docker Desktop。
    exit /b 1
)

echo 请选择操作：
echo   1. 校验配置
echo   2. 构建三个镜像
echo   3. 启动三个服务
echo   4. 查看状态
echo   5. 跟踪全部日志
echo   6. 停止并删除容器
set /p CHOICE=输入序号：

if "%CHOICE%"=="1" %COMPOSE_CMD% config
if "%CHOICE%"=="2" call scripts\build-images.bat stable
if "%CHOICE%"=="3" %COMPOSE_CMD% up -d
if "%CHOICE%"=="4" %COMPOSE_CMD% ps
if "%CHOICE%"=="5" %COMPOSE_CMD% logs -f
if "%CHOICE%"=="6" %COMPOSE_CMD% down

endlocal
