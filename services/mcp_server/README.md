# MediaFleet MCP Server

`mediafleet-mcp` 是 MediaFleet 默认只读、写工具逐项允许的 MCP Server。它使用 stdio 传输，并且只通过
[`mediafleet-sdk`](../../packages/mediafleet-sdk/README.md) 调用控制中心 REST API，不直接访问
MySQL、RabbitMQ、Redis、对象存储或节点文件系统。

当前版本为 `0.1.0` Alpha，只在仓库内构建和验证，尚未发布到 PyPI。

## 安装和启动

在仓库根目录安装 MCP 工作区包：

```bash
uv sync --package mediafleet-mcp
```

启动前通过部署环境注入控制中心地址和 API Key：

```powershell
$env:MEDIAFLEET_URL = "https://mediafleet.example.com"
$env:MEDIAFLEET_API_KEY = "replace-with-secret"
uv run --package mediafleet-mcp mediafleet-mcp
```

Linux 或 macOS：

```bash
export MEDIAFLEET_URL=https://mediafleet.example.com
export MEDIAFLEET_API_KEY=replace-with-secret
uv run --package mediafleet-mcp mediafleet-mcp
```

示例值是明确占位符。真实 API Key 应由操作系统、进程管理器、容器 Secret 或 MCP 客户端的
安全凭据机制注入，不能写入仓库、可共享配置或命令参数。

可选环境变量：

| 环境变量 | 含义 | 默认值 |
| --- | --- | --- |
| `MEDIAFLEET_CA_BUNDLE` | 自定义 CA 文件绝对路径 | 系统信任库 |
| `MEDIAFLEET_TIMEOUT` | 单次 SDK 请求超时，范围为 `(0, 120]` 秒 | `30` |
| `MEDIAFLEET_MCP_WRITE_TOOLS` | 逗号分隔的允许写工具完整名称 | 空，保持只读 |

MCP Server 只支持 stdio，不监听网络端口。远程 Streamable HTTP 需要独立的客户端身份认证和
权限设计，当前不开放。

## MCP 客户端配置

MCP 客户端需要启动以下命令，并继承已经安全注入的环境变量：

```text
uv run --directory /path/to/MediaFleet --package mediafleet-mcp mediafleet-mcp
```

通用配置示例：

```json
{
  "mcpServers": {
    "mediafleet": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/MediaFleet",
        "--package",
        "mediafleet-mcp",
        "mediafleet-mcp"
      ]
    }
  }
}
```

配置中不包含 API Key。不同 MCP 客户端的环境继承和 Secret 注入方式不同，应使用客户端或
部署平台提供的安全机制。

## 只读工具

| 工具 | 用途 |
| --- | --- |
| `mediafleet_health` | 查询控制中心健康、角色和版本 |
| `mediafleet_get_overview` | 查询任务、节点和队列总览 |
| `mediafleet_list_tasks` | 分页筛选媒体任务 |
| `mediafleet_get_task` | 查询单个任务详情 |
| `mediafleet_list_task_files` | 查询任务产物元数据 |
| `mediafleet_list_nodes` | 分页查询媒体节点 |
| `mediafleet_list_bindings` | 分页筛选流绑定 |
| `mediafleet_list_recording_servers` | 查询录制服务器和容量 |

列表页大小限制为 `1` 到 `100`，避免一次把无限数据放入模型上下文。

## 受控写工具

写工具默认不注册。部署方需要给 MCP 单独签发最小权限凭据，并通过
`MEDIAFLEET_MCP_WRITE_TOOLS` 使用完整工具名逐项允许：

| 工具 | 所需范围 | 额外约束 |
| --- | --- | --- |
| `mediafleet_create_task` | `task:write` | 稳定 `idempotency_key`，显式确认 |
| `mediafleet_retry_task` | `task:write` | 仅失败或已取消任务，显式确认 |
| `mediafleet_retry_task_callback` | `task:write` | 会再次调用外部回调，显式确认 |
| `mediafleet_create_binding` | `recording:write` | 相同流复用已有绑定，显式确认 |
| `mediafleet_release_binding` | `recording:write` | 仅明确解绑、迁移或删除时使用，显式确认 |
| `mediafleet_start_recording` | `recording:write` | 必须提供稳定 `task_id`，显式确认 |
| `mediafleet_stop_recording` | `recording:write` | 停止后继续后处理，显式确认 |

例如只允许创建任务和开始/停止录制：

```powershell
$env:MEDIAFLEET_MCP_WRITE_TOOLS = "mediafleet_create_task,mediafleet_start_recording,mediafleet_stop_recording"
```

每个写工具都要求参数 `confirmed=true`。这个确认不能替代 MCP 客户端自己的人工审批，也不能
替代控制中心的范围化授权与可信审计。写请求通过 SDK 发往控制中心，不会由 MCP 直接发布消息
或修改数据库。写请求遇到超时不会由 MCP 或 SDK 自动重试；调用方只有在确认服务端幂等语义后
才能使用相同稳定业务键重试。

MCP 不开放凭据创建、轮换、吊销、录制服务器状态变更、节点管理或任意运行配置修改。

## 输出和错误

每个工具返回稳定结构：

```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

可预期错误不会包含 API Key 或响应头，并按类型区分确认缺失、认证、权限、不存在、冲突、
限流、超时、服务暂时不可用和响应契约错误：

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "authentication_error",
    "message": "API Key 无效",
    "retryable": false,
    "http_status": 401
  }
}
```

任务参数和结果中的密钥、令牌、密码与签名字段会脱敏，URL 会移除用户名、密码、查询参数和
片段。产物工具只返回文件元数据，不向模型上下文返回签名下载 URL；节点和录制服务器工具也
不会返回内部管理地址或本地目录。

## 构建

SDK 与 MCP 尚未发布到 PyPI，测试构建产物时需要同时安装同版本的两个 Wheel：

```bash
uv build --package mediafleet-sdk
uv build --package mediafleet-mcp
```

## 安全边界

- 默认工具列表只有 8 个只读工具，写工具必须由部署方逐项允许。
- 写工具按实际语义标记只读、破坏性和幂等提示，并要求显式确认。
- 不提供任意 HTTP、SQL、Shell、文件访问、消息发布、凭据管理或环境变量修改工具。
- 不在日志、工具结果或协议错误中输出 API Key。
- 控制中心继续执行范围化授权、幂等状态迁移和可信审计；MCP 不拥有额外权限。
