# Docker Compose 部署

`deploy/compose/` 下的 Compose 文件用于开发、评估和现场模板。复制 `.env.example` 到
未跟踪的 `.env`，替换所有占位符，并在启动前检查端口和网络暴露范围。

```bash
cp .env.example .env
docker compose --env-file .env -f deploy/compose/docker-compose.yml config
docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d
```

Windows PowerShell 可使用 `Copy-Item .env.example .env`。

## 部署拓扑

- 控制中心可以多实例部署，共享 MySQL 与 RabbitMQ，不要求请求粘性。
- 每个录制节点必须与一套固定 ZLMediaKit 和录像目录配对，并使用唯一稳定节点 ID。
- 能力等价的媒体 Worker 使用同一个队列名竞争消费。
- 内容分析使用独立队列，避免与媒体计算资源相互影响。
- MySQL、RabbitMQ、Redis、对象存储和 ZLMediaKit 可以由外部平台提供。

## 生产部署要求

- 数据库迁移作为独立发布步骤执行，不让每个控制中心副本并发执行 DDL。
- 为每个录制节点配置唯一且稳定的 `RECORDER_NODE_ID`、`RECORDER_SERVER_ID` 和 `ZLM_SERVER_ID`。
- 将同一录像目录同时提供给 ZLMediaKit 和配对录制节点，容器内路径使用
  `ZLM_RECORD_LOCAL_ROOT`，宿主机挂载源使用 `ZLM_RECORD_PATH`。
- 通过部署平台注入密钥，不把真实 `.env` 提交到仓库。
- 固定镜像版本，在迁移前备份 MySQL，并保留可回滚版本。
- 上线前验证磁盘、网络、CPU/GPU、模型加载和录制后处理容量。
- 默认只将管理端口绑定到可信网络或 `127.0.0.1`，由网关负责 TLS 和访问控制。

## 启动顺序

1. 确认 MySQL、RabbitMQ、对象存储和 ZLMediaKit 可达。
2. 在单独发布步骤执行 Alembic 迁移。
3. 启动一个控制中心并检查 `/health`。
4. 启动录制节点，确认心跳报告 ZL API、录像目录和磁盘均就绪。
5. 启动媒体 Worker 和可选内容分析服务。
6. 再扩展控制中心副本或 Worker 数量。

## 校验

```bash
docker compose --env-file .env.example -f deploy/compose/docker-compose.yml config --quiet
docker compose --env-file deploy/compose/quickstart/.env.example -f deploy/compose/quickstart/docker-compose.yml config --quiet
```

Portainer 的完整现场模板和多 Stack 说明见
[deploy/compose/portainer/README.md](../../deploy/compose/portainer/README.md)。
