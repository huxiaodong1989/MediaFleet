# 图像分析服务

`image-analysis` 是独立部署的课堂录像图像分析服务。控制中心负责接单、锁定 Prompt、
创建持久任务并发布消息；本服务负责视频探测、来源筛选、按课堂时间轴采样、FFmpeg 抽帧、
上传对象存储、多图视觉模型调用和结果通知。

完整设计和数据一致性规则见[图像分析架构](../../docs/architecture/image-analysis.md)。

## 运行边界

- MySQL 保存任务租约、视频筛选、采样点、图片、模型结果、采样通知和任务终态通知状态。
- RabbitMQ 使用独立的 `image.analysis.task` 任务拓扑，消费者手动 ACK，并提供有限重试和 DLQ。
- 结果通过可配置的 `image.analysis.result` Direct Exchange 发布，使用 Publisher Confirm。
- FFmpeg、视频下载、对象存储和视觉模型都在本服务内运行；控制中心不执行这些工作。
- 视频地址只允许访问 `IMAGE_ANALYSIS_VIDEO_ALLOWED_HOSTS` 或
  `IMAGE_ANALYSIS_VIDEO_ALLOWED_NETWORKS` 明确允许的目标。

## 启用前提

服务默认设置 `IMAGE_ANALYSIS_CONSUMER_ENABLED=false`，此时 `/health` 可用于存活检查，
`/ready` 返回 503。启用消费者前必须完成：

1. 执行 Alembic 数据库迁移。
2. 为控制中心和图像服务配置相同的 `IMAGE_ANALYSIS_ALLOWED_MODELS`。
3. 配置视觉模型、RabbitMQ、MySQL 和 COS/MinIO 凭据。
4. 配置视频来源主机、网段和端口允许列表。
5. 确认结果 Exchange 存在有效下游绑定；发布使用 `mandatory`，无路由会失败并等待补发。

Bootstrap Prompt 只在 `GLOBAL` 方案完全没有历史版本时写入数据库。已有版本不会被本地文件
覆盖；图像服务也不提供修改接口，版本管理统一走控制中心管理 API。

## 结果事件

采样结果和任务终态使用同一个结果 Exchange 与 Routing Key，下游通过消息体 `eventType`
和 AMQP `type` 区分：

| 事件 | `eventType` | AMQP `type` | 关键字段 |
| --- | --- | --- | --- |
| 采样结果 | `SAMPLE_RESULT` | `image.analysis.sample` | `internalTaskId`、`sampleId`、`sampleIndex`、`sampleTotal`、`classroomOffsetMs`、`capturedAt` |
| 任务终态 | `TASK_TERMINATED` | `image.analysis.task.terminated` | `status`、`sampleTotal`、`successCount`、`failedCount`、`unavailableCount`、`resultPublishedCount`、`resultPublishFailedCount`、`finishedAt` |

每个任务只生成一个持久终态通知。服务先处理全部采样通知，只有这些通知都已发布或耗尽有限
重试进入失败状态后，才发布任务终态事件。重复扫描和进程重启不会生成新的事件编号。

## 本地启动

在仓库根目录执行：

```powershell
Copy-Item services/image_analysis/.env.example services/image_analysis/.env
# 将需要的变量注入当前终端或部署平台；入口不会自动读取该文件。
uv run --extra image-analysis python -m services.image_analysis.main
```

默认监听 `127.0.0.1:8013`。Docker 镜像和 Compose 模板见
`deploy/dockerfiles/Dockerfile.image-analysis` 与 `deploy/compose/`。

## 关键配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMAGE_ANALYSIS_CONSUMER_ENABLED` | `false` | 显式开启任务消费 |
| `IMAGE_ANALYSIS_INSTANCE_ID` | 主机名和进程号 | 跨重启稳定的实例编号 |
| `IMAGE_ANALYSIS_MAX_CONCURRENT_CLASSROOMS` | `4` | 单实例同时处理的课堂数 |
| `IMAGE_ANALYSIS_FFMPEG_CONCURRENCY` | `4` | FFmpeg/ffprobe 共享并发 |
| `IMAGE_ANALYSIS_DOWNLOAD_CONCURRENCY` | `1` | 整片下载回退并发 |
| `IMAGE_ANALYSIS_UPLOAD_CONCURRENCY` | `4` | 图片上传并发 |
| `IMAGE_ANALYSIS_MODEL_CONCURRENCY` | `4` | 多图模型请求并发 |
| `IMAGE_ANALYSIS_FFMPEG_THREADS` | `1` | 单个 FFmpeg 进程线程数 |
| `IMAGE_ANALYSIS_RESULT_EXCHANGE` | `image.analysis.result` | 结果 Direct Exchange |
| `IMAGE_ANALYSIS_RESULT_ROUTING_KEY` | `image.analysis.sample` | 结果 Routing Key |

配置的课堂额度不是已验证的容量承诺。部署前应按实际 CPU、磁盘、网络、模型 QPS/TPM 和
对象存储吞吐压测，并让阶段并发保持有界。

## 接口与验证

- `/health`：进程存活。
- `/ready`：消费者已启用且 RabbitMQ 消费连接就绪时返回 200。
- `/api/v1/service-info`：服务角色和版本。
- `/api/docs`：系统接口文档；任务由控制中心创建，本服务不暴露创建任务 API。

```powershell
uv run python -m pytest tests/image_analysis tests/contract/test_image_analysis_contracts.py `
  tests/control_center/test_image_analysis_intake_api.py `
  tests/control_center/test_image_prompt_admin_api.py -q
```
