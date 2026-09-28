# 通用媒体 Worker 本地启动

本目录可以放置媒体 Worker 自己的 `.env`，用于模拟独立部署。
服务入口会自动加载本目录 `.env`；命令行或启动脚本已经设置的环境变量优先级更高。
真实 Docker Compose 部署应设置 `SERVICE_DOTENV_ENABLED=false`，只使用 Compose 注入
的环境变量。

```powershell
Copy-Item .env.example .env
notepad .env
```

如果根目录 `.env` 已经配置好真实连接信息，也可以在仓库根目录执行：

```powershell
./scripts/local-test/Sync-ServiceEnv.ps1 -Services media_worker
```

从当前目录直接启动：

```powershell
$env:PYTHONPATH=(Resolve-Path ../..).Path
uv run --project ../.. python -m services.media_worker.main
```

如果你在仓库根目录，直接执行：

```powershell
uv run python -m services.media_worker.main
```

访问地址：

```text
http://127.0.0.1:8009/health
```

当前终端直接显示服务日志，按 `Ctrl+C` 关闭。

Worker 运行实例 ID 默认自动使用“主机名-进程号”，无需人工配置。该编号只用于
心跳、日志、任务租约和排障，不参与 RabbitMQ 路由；
`MEDIA_WORKER_QUEUE_NAME` 必须保持为所有 Worker 实例相同的共享队列名。Worker
启动后默认连接 RabbitMQ 并消费任务，只有特殊排障才需要设置
`MEDIA_WORKER_CONSUMER_ENABLED=false`。当前新 Worker 已迁移视频封面提取、
视频音频提取、视频片段提取、视频水印、图片目标检测、视频目标轨迹图和离线语音识别。
