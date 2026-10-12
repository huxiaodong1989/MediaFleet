# MediaFleet Python SDK

`mediafleet-sdk` 是 MediaFleet 控制中心 REST API 的类型化 Python 客户端。当前版本只提供
查询能力，适合运维脚本、后续 `mediafleet` CLI 和 MCP Server 复用。

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

## 只读资源

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

同步与异步客户端使用相同资源名；异步方法需要 `await`。

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
