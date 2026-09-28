# 录制节点本地启动

当前录制节点已接入 `media.command` 定向命令队列，用于保留独立服务边界。
当前已注册 `record.start` 和 `record.stop`，后续再逐项接入必须在录制节点本机
执行的本地后处理和安全清理命令。
摄像头拉流和断流由 RTC 在取得调度中心返回的绑定/ZL 地址后直接调用 ZLMediaKit
API，不属于录制节点命令消费者职责。
服务入口会自动加载本目录 `.env`；命令行或启动脚本已经设置的环境变量优先级更高。
真实 Docker Compose 部署应设置 `SERVICE_DOTENV_ENABLED=false`，只使用 Compose 注入
的环境变量。

```powershell
Copy-Item .env.example .env
notepad .env
```

如果根目录 `.env` 已经配置好真实连接信息，也可以在仓库根目录执行：

```powershell
./scripts/local-test/Sync-ServiceEnv.ps1 -Services recorder_node
```

从当前目录直接启动：

```powershell
$env:PYTHONPATH=(Resolve-Path ../..).Path
uv run --project ../.. python -m services.recorder_node.main
```

如果你在仓库根目录，直接执行：

```powershell
uv run python -m services.recorder_node.main
```

访问地址：

```text
http://127.0.0.1:8010/health
```

当前终端直接显示服务日志，按 `Ctrl+C` 关闭。

## 命令消费者

命令消费者默认启用。生产环境必须为每个录制节点配置稳定 `RECORDER_NODE_ID`，
调用中心会按该值投递到：

```text
exchange: media.command
routing_key: recorder.<RECORDER_NODE_ID>
queue: recorder.<RECORDER_NODE_ID>.commands
```

特殊排障时才需要显式关闭命令消费：

```text
RECORDER_NODE_COMMAND_CONSUMER_ENABLED=false
```

`RECORDER_NODE_COMMAND_ACCEPT_TIMEOUT_SECONDS` 只控制命令被录制器接受的等待时间，
不代表整段录像完成时间。录像开始命令 ACK 后，实际录制和后处理继续在录制节点
自己的异步事件循环里执行。

## 并发录制与录像目录

多路录制任务共享 app 级 ZLMediaKit 活跃流快照，不会每路都调用
`getMediaList`。ZL 状态请求失败时按“未知”处理，不会把全部任务误判为断流。

停止录制后，recorder-node 立即把完整尾链路交给本机后处理队列：

```text
等待 MP4 收尾 -> ZL API + 本机目录扫描 -> ffprobe -> 必要时合并
-> 音频/封面 -> 上传 -> 落库 -> MQ/HTTP 回调 -> 清理派生文件
```

`ZLM_RECORD_LOCAL_ROOT` 是 recorder-node 进程内可读的 ZL 录像根目录，也是心跳就绪检查、
磁盘检查和录制文件扫描唯一使用的目录变量。Docker 部署时它固定为容器内路径；宿主机
路径只配置 Compose 的 `ZLM_RECORD_PATH` 挂载源，不再配置第二个 `MEDIA_NODE_RECORD_ROOT`。
`RECORDER_SERVER_ID` 标识固定配对的 ZLMediaKit、recorder-node 和录像目录；正式
多节点部署必须为每个录制单元配置不同的稳定值。节点每次心跳会实际检查 ZL API、
录像目录写入能力和磁盘余量，任一检查失败即上报 `NOT_READY`，调用中心不会为该
录制单元创建新绑定。`DRAINING/MAINTENANCE/DISABLED` 是调用中心保存的运维状态，
不会被后续心跳自动改回启用。
Docker 中应把 ZL 录像盘挂载到 recorder 容器，并填写容器内路径。
ZL API 超时、返回空列表或漏项时，会继续扫描该映射目录。

`POST_PROCESSING_MAX_WORKERS` 限制同时处理的录制结果数；
`POST_PROCESSING_MEDIA_CONCURRENCY` 进一步限制扫描、探测、合并和媒体提取等重操作。
单个 MP4 直接使用源文件，不再复制；任务成功后只清理任务工作目录的派生文件，
ZL 原始分片由定期清理器按保留期统一处理。
