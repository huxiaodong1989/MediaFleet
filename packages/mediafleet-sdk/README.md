# MediaFleet Python SDK

`mediafleet-sdk` 是 MediaFleet 控制中心 REST API 的类型化 Python 客户端。除只读资源外，
当前还提供范围化 API 凭据的创建、轮换、吊销与审计查询，供受控运维使用。

SDK 只依赖 `httpx` 与 `pydantic`，不会安装 FastAPI、SQLAlchemy、Pika 或媒体模型。它不会
直接访问 MySQL、RabbitMQ、对象存储或节点本地文件。

## 安装

项目尚未发布到 PyPI。可从仓库构建 Wheel 后安装：

```bash
uv build --package mediafleet-sdk
uv pip install dist/mediafleet_sdk-0.1.0-py3-none-any.whl
```

## 同步客户端

```python
from mediafleet_sdk import MediaFleetClient

with MediaFleetClient(
    base_url="https://mediafleet.example.com",
    api_key="replace-with-your-api-key",
) as client:
    health = client.system.health()
    tasks = client.tasks.list(status="completed", page=1, page_size=20)
    print(health.status, tasks.total)
```

## 异步客户端

```python
import asyncio

from mediafleet_sdk import AsyncMediaFleetClient


async def main() -> None:
    async with AsyncMediaFleetClient(
        base_url="https://mediafleet.example.com",
        api_key="replace-with-your-api-key",
    ) as client:
        overview = await client.admin.overview()
        print(overview.tasks_by_status)


asyncio.run(main())
```

## 资源

| 调用 | 用途 |
| --- | --- |
| `client.system.health()` | 查询控制中心健康状态 |
| `client.system.service_info()` | 查询服务名称、角色与版本 |
| `client.admin.overview()` | 查询任务、节点和队列总览 |
| `client.tasks.list(...)` | 分页筛选任务 |
| `client.tasks.get(task_id)` | 查询单个任务详情 |
| `client.tasks.list_files(task_id)` | 查询任务产物 |
| `client.nodes.list(...)` | 分页查询节点 |
| `client.bindings.list(...)` | 分页查询流绑定 |
| `client.recording_servers.list()` | 查询录制服务器 |
| `client.security.scopes()` | 查询可分配权限范围 |
| `client.security.list_credentials()` | 查询凭据元数据，不返回密钥明文 |
| `client.security.create_credential(...)` | 创建凭据，明文只返回一次 |
| `client.security.rotate_credential(id)` | 立即轮换凭据 |
| `client.security.revoke_credential(id)` | 吊销凭据 |
| `client.security.list_audit_events(...)` | 查询写请求审计 |
| `client.tasks.create(request, idempotency_key=...)` | 创建通用媒体任务 |
| `client.tasks.retry(task_id)` | 重新投递失败或已取消任务 |
| `client.tasks.retry_callback(task_id)` | 补发终态任务业务回调 |
| `client.bindings.create(request)` | 创建或复用流绑定 |
| `client.bindings.release(binding_id)` | 幂等释放流绑定 |
| `client.recordings.start(request)` | 开始录制 |
| `client.recordings.stop(task_id)` | 停止录制并继续后处理 |
| `client.recording_servers.update_status(code, status)` | 更新录制服务器运维状态 |

同步与异步客户端使用相同资源名；异步方法需要 `await`。

任务创建必须提供 8 到 128 个字符的稳定幂等键。同一次业务请求因网络或进程重启而再次提交
时必须复用原值。写请求模型拒绝额外字段，POST、PATCH 和 DELETE 不会自动重试。

## 认证和错误

API Key 只通过 `X-API-Key` 请求头发送。健康和服务信息端点是公开系统接口，SDK 不会向这
两个端点发送 API Key。异常只保留 HTTP 状态码、服务端 `detail`、安全请求路径和可选请求
编号，不保留完整响应头、查询参数或原始响应体。

```python
from mediafleet_sdk import AuthenticationError, NotFoundError

try:
    task = client.tasks.get("task-id")
except AuthenticationError:
    print("API Key 无效")
except NotFoundError:
    print("任务不存在")
```

默认仅对 GET/HEAD 的连接错误、超时、429、502、503 和 504 做有限重试，并尊重
`Retry-After`。可显式调整策略：

```python
from mediafleet_sdk import MediaFleetClient, RetryPolicy

client = MediaFleetClient(
    base_url="https://mediafleet.example.com",
    api_key="replace-with-your-api-key",
    retry=RetryPolicy(max_attempts=3, backoff_factor=0.25, max_delay=2.0),
)
```

服务端返回没有时区的时间时，SDK 按 UTC 解释；带时区时间会转换为 UTC。服务端新增的未知
响应字段会被保留，缺少 SDK 所需字段时会抛出 `ResponseValidationError`。
