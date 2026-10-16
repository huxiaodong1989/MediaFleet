# MediaFleet CLI

`mediafleet-cli` 是 MediaFleet 控制中心的命令行客户端。它通过
[`mediafleet-sdk`](../mediafleet-sdk/README.md) 调用 REST API，不直接连接 MySQL、RabbitMQ、
Redis、对象存储或节点文件系统。

当前版本为 `0.1.0` Alpha，只在仓库内构建和验证，尚未发布到 PyPI。

## 安装与运行

在仓库根目录安装 CLI 工作区包：

```bash
uv sync --package mediafleet-cli
uv run --package mediafleet-cli mediafleet --help
```

构建独立 Wheel 和源码包：

```bash
uv build --package mediafleet-sdk
uv build --package mediafleet-cli
```

SDK 和 CLI 尚未发布到 PyPI。使用构建产物测试安装时，需要同时安装同版本的 SDK 与 CLI
Wheel；只安装 CLI Wheel 会因为公共索引中还没有 `mediafleet-sdk==0.1.0` 而解析失败。

## 配置 context

context 保存控制中心 URL 和可选 CA 文件，不保存 API Key：

```bash
mediafleet context set dev --url https://mediafleet.example.com
mediafleet context use dev
mediafleet context list
```

默认配置文件位置：

- Windows：`%APPDATA%\MediaFleet\config.json`
- Linux：`$XDG_CONFIG_HOME/mediafleet/config.json`，未设置时使用
  `~/.config/mediafleet/config.json`
- macOS：`~/Library/Application Support/MediaFleet/config.json`

测试或隔离运行时可通过 `MEDIAFLEET_CONFIG_FILE` 指定其他配置文件。

## 配置 API Key

本地使用时，将 API Key 从标准输入写入操作系统凭据库：

```bash
mediafleet auth login --api-key-stdin
```

在交互终端中输入不会回显；在自动化脚本中从标准输入传入。CLI 不提供
`--api-key VALUE` 参数，避免密钥进入进程列表和 Shell 历史。删除当前 context 的凭据：

```bash
mediafleet auth logout
```

CI 可直接注入环境变量：

```bash
MEDIAFLEET_URL=https://mediafleet.example.com \
MEDIAFLEET_API_KEY=replace-with-secret \
mediafleet overview --json
```

配置优先级为：

1. 命令的 `--url`、`--context` 和 `--ca-bundle`；
2. `MEDIAFLEET_URL`、`MEDIAFLEET_CONTEXT`、`MEDIAFLEET_CA_BUNDLE` 和
   `MEDIAFLEET_API_KEY`；
3. 当前 context 的非敏感配置和操作系统凭据库。

示例中的密钥是占位符，不能把真实凭据写入源码、配置示例或命令历史。

## 查询与安全管理命令

```text
mediafleet version
mediafleet health
mediafleet overview

mediafleet task list
mediafleet task get TASK_ID
mediafleet task files TASK_ID

mediafleet node list
mediafleet binding list
mediafleet recorder list

mediafleet task create --file task.json --idempotency-key STABLE_KEY
mediafleet task retry TASK_ID --yes
mediafleet task callback-retry TASK_ID --yes
mediafleet binding create --file binding.json
mediafleet binding release BINDING_ID --yes
mediafleet recording start --file recording.json
mediafleet recording stop TASK_ID --yes
mediafleet recorder set-status CODE STATUS --yes

mediafleet security scopes
mediafleet security credential list
mediafleet security credential create --caller-id ID --display-name NAME --name KEY --scope read
mediafleet security credential rotate CREDENTIAL_ID --yes
mediafleet security credential revoke CREDENTIAL_ID --yes
mediafleet security audit
```

命令支持 `--json` 和 `--no-color`。列表命令使用有界分页，任务和绑定列表提供常用筛选
参数。示例：

```bash
mediafleet task list --status completed --page 1 --page-size 20 --json
mediafleet binding list --node-id NODE_ID --status active
mediafleet task get TASK_ID --no-color
```

`health` 对应公开健康端点，只要求配置控制中心 URL。其他查询命令还要求 API Key。

创建任务、流绑定和录制请求支持 `--file FILE` 或 `--stdin`，两者必须二选一。输入必须是
UTF-8 JSON 对象，最大 1 MiB，未声明字段会被拒绝。任务创建的 `--idempotency-key` 必须在同一
业务请求的重试中保持不变。任务重试、回调补发、绑定释放、停止录制和录制服务器状态变更
必须显式传入 `--yes`。

## JSON 与退出码

成功的 `--json` 输出只在标准输出写入一个 JSON 文档。失败时标准输出保持为空，标准错误写入
一个结构化错误文档，便于脚本稳定解析。

| 退出码 | 含义 |
| ---: | --- |
| `0` | 成功 |
| `2` | 参数或本地配置错误 |
| `3` | 认证或权限失败 |
| `4` | 资源不存在 |
| `5` | 资源状态冲突 |
| `6` | 网络、超时、限流或服务暂时不可用 |
| `7` | 服务端响应不符合 SDK 契约 |
| `8` | 用户取消，预留给后续写操作 |
| `10` | 其他 API 错误 |

## 安全边界

- 配置文件只允许保存 context、URL 和 CA 文件路径。
- API Key 只从环境变量或操作系统凭据库读取。
- CLI 不提供通用 HTTP、SQL、Shell、消息发布或文件访问入口。
- 凭据创建和轮换产生的 API Key 只在成功响应中显示一次，不写入配置文件或日志。
- 凭据轮换、吊销和改变既有业务状态的命令必须显式传入 `--yes`。
- 写请求使用固定的类型化 SDK 资源，不提供通用 HTTP 代理。
