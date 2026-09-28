# Portainer 现场部署模板

这里是可复制到现场修改的完整部署模板，与 `../quickstart/` 的本地 Docker Desktop 联调配置分开维护。

## 文件选择

| 文件 | 用途 |
| --- | --- |
| `docker-compose.yml` | 一台服务器上的完整 CPU 媒体包：调用中心、一个 recorder-node、一个 CPU media-worker |
| `docker-compose.control-center.yml` | 只部署调用中心 |
| `docker-compose.recorder-node.yml` | 为一组固定 ZLMediaKit 和录像盘部署一个录制节点 |
| `docker-compose.media-worker.yml` | 只部署 CPU 媒体 Worker，可多服务器、多实例部署 |
| `docker-compose.gpu.yml` | GPU 服务器上的独立 media-worker，适合 FunASR/GLM-ASR |
| `docker-compose.content-analysis.yml` | 只部署 AI 评课/内容分析服务，消费独立内容分析队列 |
| `.env.example` | Portainer Stack 级变量：镜像、端口、宿主机目录和常用依赖地址 |

## 部署步骤

1. 将本目录复制到现场，复制 `.env.example` 为 `.env`。
2. 在 `.env` 或 Portainer Stack variables 中填写真实的 MySQL、RabbitMQ、对象存储、ZLMediaKit 地址、账号、密码、Secret、节点 ID 和宿主机路径。
3. 所有容器环境变量都在对应 `docker-compose*.yml` 的 `environment` 中，变量名和默认用途可以直接在同一个文件查看，不需要额外 env 文件。
4. 将整个 `portainer` 目录作为 Git/上传 Stack 部署；如果 Portainer 使用 Web 编辑器，直接把 `.env.example` 中需要覆盖的变量填到 Stack environment variables。
5. 首次只启动一个 `control-center`，确认数据库迁移完成后，再启动 recorder-node 和 media-worker。

## 共享 Docker 网络

所有 Portainer Stack 统一加入外部网络 `mediafleet-network`，这样独立 Stack 中的服务可以通过
容器名互相访问。网络只需要创建一次：

```bash
docker network create --driver bridge --subnet 192.168.100.0/24 mediafleet-network
```

如果服务器提示地址池冲突，换一个与现有 Docker 网络不重叠的网段。不要把 `driver` 或
`ipam` 写在 `services.<service>.networks.<network>` 下；`ipam` 只能写在顶层网络定义中，
本模板因为复用已创建的外部网络，不在 Compose 中配置 `ipam`。

如果采用独立 Stack，调用中心先部署；每台 ZL 服务器使用一份
`docker-compose.recorder-node.yml`，修改 `RECORDER_NODE_ID`、`RECORDER_SERVER_ID`、
`ZLM_SERVER_ID` 和 `ZLM_RECORD_PATH`；CPU/GPU Worker 则共享相同的
`MEDIA_WORKER_QUEUE_NAME`；内容分析服务使用 `docker-compose.content-analysis.yml`，按实例修改
`CONTENT_ANALYSIS_INSTANCE_ID` 和 `CONTENT_ANALYSIS_NODE_NAME`。

## 外部依赖

本 Stack 不创建 MySQL、RabbitMQ、Redis、MinIO 或 ZLMediaKit。它们必须已经部署并且从容器网络可达：

- MySQL 使用 `mediafleet` 数据库；
- RabbitMQ 的 media-worker 实例使用相同的 `MEDIA_WORKER_QUEUE_NAME` 竞争消费；
- content-analysis 使用独立 `content.analysis.task` Exchange 和 `content-analysis.tasks`
  队列，多个内容分析实例通过同一队列竞争消费；
- recorder-node 的 `ZLM_API_URL` 指向配套 ZLMediaKit 的管理 API；
- `ZLM_RECORD_PATH` 只表示宿主机上该 ZL 实际 `www/record` 目录；recorder-node 容器内统一挂载为
  `/data/zlmediakit/record`，所以 `ZLM_RECORD_LOCAL_ROOT` 必须填写
  `/data/zlmediakit/record`，不能填写 `/home/...`、`/opt/...` 等宿主机路径；服务进程不再
  读取 `MEDIA_NODE_RECORD_ROOT`；
- 业务产物只写 COS 或 MinIO/S3，容器本地目录只用于临时文件和日志。

不要在容器内把 `DB_HOST`、`RABBITMQ_HOST` 或 `ZLM_API_URL` 写成 `127.0.0.1`；那只会指向当前容器本身。

## 迁移策略

Compose 中首次部署可以将 `DB_AUTO_MIGRATE=true`。多实例调用中心稳定运行后，应只在一个升级窗口执行迁移，再将其他实例设为 `false`，避免并发迁移。

## CPU/GPU Worker

- CPU 节点使用 `docker-compose.yml` 中的 `media-worker`，默认 `ASR_PROVIDER=funasr`、`FUNASR_DEVICE=cpu`。
- GPU 节点单独部署 `docker-compose.gpu.yml`，使用 GPU 镜像、NVIDIA Container Toolkit 和 `GPU_DEVICE_ID`；需要 GLM 时设置 `ASR_PROVIDER=glm`、`GLM_ASR_DEVICE=cuda:0`。
- CPU、GPU 及其他服务器上的所有 media-worker 必须使用相同的 `MEDIA_WORKER_QUEUE_NAME`，这样 RabbitMQ 才会按竞争消费分发任务。

## 校验与日志

在部署前可以在本目录执行：

```powershell
docker compose --env-file .env.example -f docker-compose.yml config --quiet
docker compose --env-file .env.example -f docker-compose.gpu.yml config --quiet
docker compose --env-file .env.example -f docker-compose.content-analysis.yml config --quiet
```

部署后在 Portainer 的 Stack Logs 中分别查看 `control-center`、`recorder-node`、`media-worker`
和 `content-analysis` 日志；健康检查地址分别是 `/health` 的 8008、8010、8009 和 8012 端口。
