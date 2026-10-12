# MediaFleet MCP Server

`mediafleet-mcp` 是 MediaFleet 的只读 MCP Server。它使用 stdio 传输，并且只通过
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

## 输出和错误

每个工具返回稳定结构：

```json
{
  "ok": true,
  "data": {},
  "error": null
}
```

可预期错误不会包含 API Key 或响应头，并按类型区分认证、权限、不存在、冲突、限流、超时、
服务暂时不可用和响应契约错误：

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

- 工具全部标记为只读、非破坏和幂等。
- 不提供任意 HTTP、SQL、Shell、文件访问、消息发布或环境变量修改工具。
- 不在日志、工具结果或协议错误中输出 API Key。
- 写工具等待调用方身份、权限范围、可信审计和幂等策略完成后再设计。
