# 服务边界与依赖规则

## 1. 为什么需要边界

MediaFleet 的扩展性来自独立部署和明确所有权。代码放错位置会造成控制中心需要安装
FFmpeg、Worker 绕过授权直接操作控制数据，或录制任务被投递到读取不到文件的节点。
因此服务边界是运行正确性要求，不只是目录风格。

## 2. 各层职责

```text
services/* 入口与服务私有编排
    ↓
media_platform/application 应用用例与端口
    ↓
media_platform/domain + media_platform/contracts 领域规则与跨服务契约
    ↓
media_platform/infrastructure 数据库、消息、存储、媒体工具适配器
```

| 目录 | 允许包含 | 不应包含 |
| --- | --- | --- |
| `media_platform/domain` | 状态、值对象、选择规则、纯业务校验 | FastAPI、Pika、SQLAlchemy 会话、FFmpeg 调用 |
| `media_platform/contracts` | 消息信封、任务/命令/事件模型、稳定拓扑名 | 网络连接、服务私有业务流程 |
| `media_platform/application` | 可复用用例、输入命令、输出结果、基础设施端口 | HTTP 路由细节、服务启动生命周期 |
| `media_platform/infrastructure` | 仓储、消息发布消费、存储、日志和媒体工具适配器 | 对某个 API 路由的专用编排 |
| `services/<name>` | 入口、依赖装配、服务私有应用流程、API 或消费者 | 直接导入其他服务的私有模块 |

## 3. 服务职责判断

新增能力前按以下顺序判断归属：

1. 是否必须读取某个 ZLMediaKit 的本地录像目录？如果是，属于 `recorder-node`。
2. 是否是可由任意等价实例执行的媒体计算？如果是，属于 `media-worker`。
3. 是否是依赖大模型、长步骤和独立检查点的内容分析？如果是，属于 `content-analysis`。
4. 是否只负责鉴权、校验、调度、查询和持久协调？如果是，属于 `control-center`。
5. 是否会被多个入口或服务复用？抽取到 `media_platform` 对应层，但不要把服务私有业务动词塞进共享包。

## 4. 强制边界

### 控制中心

- 可以读写控制数据并发布任务、命令，不能执行 FFmpeg 或加载 ASR/YOLO 模型。
- 可以保存录像文件的元数据，不能扫描录制节点的本地路径。
- 可以选择目标节点，不能用进程内字典作为唯一绑定或任务来源。

### 录制节点

- 只消费发送给自身稳定 `RECORDER_NODE_ID` 的命令。
- 可以访问与自身配对的 ZLMediaKit 和挂载录像目录。
- 不替代控制中心做全局节点选择，也不处理不属于本节点的历史录像。

### 媒体 Worker

- 通过共享队列竞争任务，不依赖固定实例 ID 路由。
- 所有同队列实例必须能力等价；能力不等价时设计独立队列。
- 离线 ASR 模型只在 Worker 内加载，控制中心不得代理模型推理。

### 对外接口

- REST、SDK、CLI、MCP 和运维控制台应调用相同应用服务并遵循同一权限与审计规则。
- CLI 与 MCP 默认通过共享 SDK 访问控制中心 API，不直接连接数据库或 RabbitMQ。
- 不提供任意 SQL、Shell、文件系统、RabbitMQ 发布或环境变量修改接口。

## 5. 可接受的共享

适合放入共享包的内容包括消息契约、领域状态、稳定拓扑命名、仓储接口、通用存储适配器
和无业务归属的媒体元数据工具。只被一个服务使用、带有该服务生命周期或运维含义的逻辑
应留在服务目录。

新增共享模块时应通过服务入口导入边界测试，防止 `services/control_center` 导入
`services/media_worker` 等反向依赖。
