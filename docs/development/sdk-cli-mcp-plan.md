# MediaFleet SDK、CLI 与 MCP 实施方案（已确认）

## 1. 目标

为 MediaFleet 增加三个面向开发者和运维人员的入口，并保持同一安全与业务边界：

1. Python SDK：封装控制中心 REST API，提供类型、认证、超时、重试、分页和错误分类。
2. `mediafleet` CLI：通过 SDK 提供适合人工运维、CI 和冒烟测试的命令行入口。
3. MCP Server：通过 SDK 向模型提供小而明确的结构化工具，第一版只读。

SDK、CLI 和 MCP 都不得直接连接 MySQL、RabbitMQ、Redis、对象存储或节点本地目录，也不得
导入控制中心运行时、仓储或消息发布器。

## 2. 当前接口与约束

控制中心已经提供以下可复用 REST 能力：

| 领域 | 已有接口 | 第一版用途 |
| --- | --- | --- |
| 系统 | `/health`、`/api/v1/service-info` | SDK、CLI、MCP 健康和版本检查 |
| 总览 | `/api/v1/admin/overview` | 任务、节点和队列摘要 |
| 任务 | `/api/v1/admin/tasks`、`/api/v1/tasks/{task_id}` | 分页查询与单任务详情 |
| 产物 | `/api/v1/tasks/{task_id}/files` | 查询任务媒体产物 |
| 节点 | `/api/v1/admin/nodes/page` | 分页查询节点和就绪状态 |
| 绑定 | `/api/v1/admin/bindings/page` | 分页查询流绑定 |
| 录制单元 | `/api/v1/recording-servers` | 查询录制服务器和容量 |
| 写操作 | 创建任务、流绑定、录制开始/停止、失败任务重试、回调补发、录制单元状态变更 | 后续受控开放 |

当前所有受保护接口共用 `X-API-Key`。服务端尚不能区分“哪个人、哪个 CLI 配置或哪个 MCP
实例”发起了请求，也没有 `read:*`、`task:write`、`recording:write` 等权限范围。因此：

- 第一版 SDK 可以覆盖读取和写入 API，但自动重试策略必须区分读写。
- 第一版 CLI 默认交付只读命令，写命令进入第二阶段。
- 第一版 MCP 只注册只读工具。
- MCP 写工具必须等范围化授权和服务端可信审计身份完成后再开放。

## 3. 仓库与发布结构

建议在同一仓库中维护三个独立安装包，避免安装 SDK 时拉入 FastAPI、SQLAlchemy、RabbitMQ、
媒体模型等服务端依赖：

```text
packages/
  mediafleet-sdk/
    pyproject.toml
    src/mediafleet_sdk/
      client.py
      async_client.py
      config.py
      errors.py
      models/
      resources/
  mediafleet-cli/
    pyproject.toml
    src/mediafleet_cli/
      main.py
      commands/
      config.py
      output.py
services/
  mcp_server/
    pyproject.toml
    src/mediafleet_mcp/
      server.py
      settings.py
      tools/
```

根项目使用 uv workspace 管理本地包和统一锁文件。发布名称建议为：

- PyPI 包：`mediafleet-sdk`
- PyPI 包和命令：`mediafleet-cli` / `mediafleet`
- MCP 包和镜像：`mediafleet-mcp`

第一轮只验证本地构建和安装，不立即发布到 PyPI；达到版本、文档和兼容性门槛后再发布。

## 4. Python SDK 设计

### 4.1 客户端

同时提供：

- `MediaFleetClient`：同步客户端，适合脚本和普通 Python 应用。
- `AsyncMediaFleetClient`：异步客户端，供 MCP 和异步应用使用。

两者共享模型、错误类型、配置校验和 URL 构造，底层使用 `httpx`。客户端支持上下文管理器，
确保连接池关闭。

初始化参数：

```python
MediaFleetClient(
    base_url="https://mediafleet.example.com",
    api_key="...",
    timeout=30.0,
    verify=True,
    user_agent="mediafleet-sdk/0.1.0",
)
```

API Key 只通过请求头 `X-API-Key` 发送，不进入 URL、异常文本或日志。

### 4.2 资源接口

推荐的公开调用方式：

```python
client.system.health()
client.system.service_info()
client.admin.overview()
client.tasks.list(...)
client.tasks.get(task_id)
client.tasks.list_files(task_id)
client.nodes.list(...)
client.bindings.list(...)
client.recording_servers.list()
```

第二阶段增加：

```python
client.tasks.create(...)
client.tasks.retry(task_id, ...)
client.tasks.retry_callback(task_id)
client.bindings.create(...)
client.bindings.release(binding_id)
client.recordings.start(...)
client.recordings.stop(task_id)
client.recording_servers.update_status(...)
client.image_analysis.create(...)
```

### 4.3 类型和分页

- SDK 定义独立的 Pydantic 公共模型，不导入 `services.control_center`。
- 分页统一为 `Page[T]`，包含 `items`、`total`、`page`、`page_size`。
- 日期解析为带时区 `datetime`；服务端返回无时区值时必须有明确兼容策略和测试。
- 未知响应字段默认保留兼容性；缺失必需字段时抛出响应契约错误。
- SDK 模型与控制中心 OpenAPI 增加契约测试，防止字段漂移。

### 4.4 错误分类

统一异常层级：

```text
MediaFleetError
├── ConfigurationError
├── TransportError
├── TimeoutError
├── APIError
│   ├── AuthenticationError       401
│   ├── PermissionDeniedError     403
│   ├── NotFoundError             404
│   ├── ConflictError             409
│   ├── ValidationError           422
│   ├── RateLimitError            429
│   └── ServiceUnavailableError   502/503/504
└── ResponseValidationError
```

异常保留 HTTP 状态码、服务端 `detail`、请求 ID 和可安全输出的响应摘要，不保存 API Key、完整
签名 URL 或任意响应头。

### 4.5 重试

- 默认只对 GET/HEAD 的连接错误、超时、429、502、503、504 做有限指数退避。
- 401、403、404、409、422 不自动重试。
- POST/PATCH/DELETE 默认不自动重试。
- 写请求只有在端点具备稳定幂等语义且调用方提供稳定业务键时，才允许显式开启重试。
- 尊重 `Retry-After`，总尝试次数和最长等待时间必须有上限。

## 5. CLI 设计

### 5.1 框架与配置

建议使用 Typer + Rich，命令入口为：

```text
mediafleet
```

配置优先级：

1. 命令级非敏感选项。
2. `MEDIAFLEET_*` 环境变量。
3. 当前 profile 的非敏感配置。

默认配置文件只保存 profile 名、控制中心 URL、CA 文件和输出偏好，不保存明文 API Key。

- CI：从 `MEDIAFLEET_API_KEY` 读取。
- 本地：`mediafleet auth login --api-key-stdin` 写入操作系统凭据库。
- 禁止 `--api-key <value>`，避免凭据进入进程列表和 Shell 历史。

### 5.2 第一阶段只读命令

```text
mediafleet version
mediafleet health
mediafleet overview

mediafleet task list [筛选和分页参数]
mediafleet task get TASK_ID
mediafleet task files TASK_ID

mediafleet node list [分页参数]
mediafleet binding list [筛选和分页参数]
mediafleet recorder list

mediafleet context list
mediafleet context use NAME
mediafleet context set NAME --url URL [--ca-bundle PATH]
mediafleet auth login --api-key-stdin
mediafleet auth logout
```

每个查询命令支持：

- 默认 Rich 表格或对象详情。
- `--json` 输出单个稳定 JSON 文档，不混入进度和日志。
- `--page`、`--page-size`；不默认拉取无限数据。
- `--no-color`。

### 5.3 第二阶段写命令

复杂请求优先支持 `--file request.json` 和标准输入，简单字段同时提供显式选项：

```text
mediafleet task create --file task.json
mediafleet task retry TASK_ID --yes
mediafleet task callback-retry TASK_ID --yes
mediafleet binding create --file binding.json
mediafleet binding release BINDING_ID --yes
mediafleet recording start --file recording.json
mediafleet recording stop TASK_ID --yes
mediafleet recorder set-status CODE STATUS --yes
mediafleet image-analysis create --file request.json
```

- 交互终端要求确认；CI 必须显式传 `--yes`。
- JSON 模式下错误也输出结构化对象，但写入 stderr。
- CLI 不接收任意 RabbitMQ routing key、数据库语句、Shell 命令或环境变量名。

### 5.4 稳定退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 命令参数或本地配置错误 |
| 3 | 认证或权限失败 |
| 4 | 资源不存在 |
| 5 | 状态冲突或操作被业务规则拒绝 |
| 6 | 网络、超时或服务暂时不可用 |
| 7 | 服务端响应不符合 SDK 契约 |
| 8 | 用户取消确认 |
| 10 | 未分类 API 错误 |

## 6. MCP Server 设计

### 6.1 第一版传输与部署

- 使用官方 Python MCP SDK。
- 第一版只支持 stdio，适合 Codex、Claude Desktop 等本地受控客户端。
- 控制中心地址和 API Key 从 MCP 进程环境注入。
- 后续增加 Streamable HTTP 前，必须先设计 MCP 客户端身份认证，不能把共享 API Key 暴露给
  远程调用方。

### 6.2 第一版只读工具

```text
mediafleet_health
mediafleet_get_overview
mediafleet_list_tasks
mediafleet_get_task
mediafleet_list_task_files
mediafleet_list_nodes
mediafleet_list_bindings
mediafleet_list_recording_servers
```

要求：

- 工具输入使用明确字段和范围，分页大小设置上限。
- 返回结构化 JSON，避免把整份 OpenAPI、日志或大响应直接塞入模型上下文。
- 业务不存在、无权限、契约错误和基础设施暂时不可用必须使用不同错误类型。
- 工具名称和描述不使用“执行任意操作”等模糊表述。
- 不提供任意 HTTP 请求、SQL、Shell、文件访问、RabbitMQ 发布或环境变量修改工具。

### 6.3 写工具开放条件

以下条件全部满足后才讨论 MCP 写工具：

1. 控制中心能识别可信调用方身份，而不是只记录请求体中的 `operator`。
2. 凭据具备至少 `read`、`task:write`、`recording:write`、`admin:write` 权限范围。
3. 每个写工具定义稳定幂等键和审计记录。
4. MCP Server 有独立允许工具列表，默认仍只读。
5. 写工具有端到端重复调用、超时重试和权限拒绝测试。

## 7. 实施阶段与审核点

### 阶段 A：SDK 基础与只读资源

范围：

- uv workspace 和独立 SDK 包。
- 同步/异步客户端、配置、认证、错误和只读重试。
- 系统、总览、任务、产物、节点、绑定、录制单元只读资源。
- 单元测试、控制中心契约测试、README 和示例。

验收：

- SDK 安装不拉入 FastAPI、SQLAlchemy、Pika 或媒体模型依赖。
- 使用 FastAPI 测试应用完成真实 HTTP 请求验证。
- API Key 不出现在日志、异常和测试快照中。
- Python 3.10、3.11、3.12 通过。

审核点：确认 SDK 公共命名、模型和错误层级后，再进入 CLI。

### 阶段 B：只读 CLI（已实现，等待独立 PR 审核）

范围：

- 独立 CLI 包、入口、profile、环境变量和操作系统凭据库。
- 第一阶段只读命令、Rich 展示、`--json` 和稳定退出码。
- CLI 到测试控制中心的端到端测试。

验收：

- 人工输出可读，JSON 输出可由脚本稳定解析。
- 非交互环境不会等待输入。
- API Key 不出现在参数、输出或错误中。
- Wheel 安装后 `mediafleet --help` 和只读命令可运行。

审核点：实现已经完成；确认命令层级、配置位置和输出格式后，再进入 MCP。

### 阶段 C：只读 MCP

范围：

- 独立 MCP 包和 stdio 入口。
- 八个只读工具及结构化错误映射。
- 本地 MCP 客户端集成示例和工具契约测试。

验收：

- MCP 只通过异步 SDK 调用控制中心。
- 默认工具列表不包含写操作。
- 大列表受分页限制，返回不包含 API Key 和敏感响应头。
- stdio 协议测试和真实测试控制中心调用通过。

审核点：决定是否先补权限模型，还是仅长期保留只读 MCP。

### 阶段 D：身份、权限与受控写操作

建议单独设计和单独 PR，不与前三阶段混合：

- 调用方身份和范围化凭据。
- 服务端可信审计字段。
- SDK 写资源和幂等策略。
- CLI 写命令。
- 经单独批准的 MCP 写工具。

## 8. PR 拆分建议

不要将 SDK、CLI、MCP 和权限模型放进一个大 PR。建议：

1. PR 1：Python SDK 基础和只读资源。
2. PR 2：只读 CLI。
3. PR 3：只读 MCP Server。
4. PR 4：调用方身份、权限范围和审计设计。
5. 后续 PR：CLI 写操作和逐个 MCP 写工具。

每个 PR 都从当时最新 `main` 创建分支，并独立通过 CI、Secret Scan、安装和契约测试。

## 9. 本轮不做

- 不自动生成完整 OpenAPI 客户端。
- 不在 SDK 中导入服务端 ORM、仓储或应用运行时。
- 不提供直接访问 MySQL、RabbitMQ、对象存储或节点文件的后门。
- 不实现通用 HTTP 代理、任意 Shell、SQL 或环境变量工具。
- 不立即发布 PyPI 包或公共 MCP 服务。
- 不在没有范围化授权时开放 MCP 写操作。

## 10. 已确认决策

1. 采用“同仓库三个独立安装包”的结构。
2. 第一版 CLI 和 MCP 都只读。
3. CLI 采用 Typer + Rich，并使用操作系统凭据库保存本地 API Key。
4. MCP 第一版只提供 stdio，不提供远程 HTTP。
5. SDK、CLI、MCP 分三个 PR 实施和审核。
