# 调度中心本地启动

本目录可以放置调度中心自己的 `.env`，用于模拟独立部署。
服务入口会自动加载本目录 `.env`；命令行或启动脚本已经设置的环境变量优先级更高。
真实 Docker Compose 部署应设置 `SERVICE_DOTENV_ENABLED=false`，只使用 Compose 注入
的环境变量。

```powershell
Copy-Item .env.example .env
notepad .env
```

如果根目录 `.env` 已经配置好真实连接信息，也可以在仓库根目录执行：

```powershell
./scripts/local-test/Sync-ServiceEnv.ps1 -Services control_center
```

从当前目录直接启动：

```powershell
$env:PYTHONPATH=(Resolve-Path ../..).Path
uv run --project ../.. python -m services.control_center.main
```

如果你在仓库根目录，直接执行：

```powershell
uv run python -m services.control_center.main
```

访问地址：

```text
http://127.0.0.1:8008/api/docs
http://127.0.0.1:8008/admin/
http://127.0.0.1:8008/health
```

`/admin/` 是随调用中心提供的轻量运维页面，使用同一个 `X-API-Key` 访问管理后台
接口。首次打开页面后，在右上角输入当前调用中心的 `API_KEY`，即可查看总览、任务、
节点和绑定；失败任务重试与业务回调补发也从任务列表进入。页面只把 API Key 保存在
当前浏览器会话，不保存数据库、RabbitMQ、ZLMediaKit 或对象存储凭据。

当前终端直接显示服务日志，按 `Ctrl+C` 关闭。
