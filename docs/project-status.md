# 项目状态

## 当前阶段

MediaFleet `0.1.0` 已迁移到个人 GitHub Public 仓库
`huxiaodong1989/MediaFleet`，当前仍按 Alpha 阶段管理，不将现有提交或镜像标记为稳定版本。
部门协作通过分支和 Pull Request 进行，仓库所有者负责最终合并。

## 已完成

- 2026-10-16：从本地 `ly-ac-media-clip-svc` 迁入分析服务运行时配置：控制中心支持
  模型连接、提示词模型和实例并发额度的类型化草稿、发布与状态查询；模型密钥加密保存，
  新任务锁定配置版本，图像/内容分析实例按版本刷新并通过心跳回报。新增 MediaFleet
  中立表名的 `20261016_0004` 迁移、管理页、Portainer 配置示例与架构说明。
- 同步原项目的 AI 评课 `taskData` 包装兼容、内容回调 `content` 字符串格式、图像课堂
  并发槽位等待、可选 RabbitMQ 消费 ACK 超时、失败分类日志及调用中心纯文本日志。

- 从既有内部源码快照导出代码，不携带原 Git 历史，并在 `main` 初始化独立仓库。
- 将项目、服务、镜像、数据库默认值和物理表改为 MediaFleet 中立命名。
- 添加 MIT 许可证、贡献指南、安全策略、路线图、部署和依赖许可证说明。
- 清理填写过真实值的环境文件、私有流水线、内部日志文档、私有集成、专有二进制资产、
  模型权重和未使用的旧实现。
- 移除停止使用的直播平台录制集成及其依赖链。
- 用单一干净 Alembic 基线 `20260928_0001` 替换历史升级链。
- 移除嵌入式凭据和环境特定地址；日志会移除 URL 用户信息、查询参数和片段。
- 确定 REST 为稳定远程契约，并规划基于 SDK 的 CLI 与 MCP，禁止其绕过授权直连数据库或 MQ。
- 更新依赖锁；截至 2026-09-29，`pip-audit` 未报告已知漏洞。
- 将 GitHub 默认 README 和仓库级协作文档改为中文。
- 新增文档导航、项目背景、总体架构、服务边界、数据与消息可靠性、接口架构、扩展开发
  指南和架构决策记录机制。
- 同步内容分析可靠性修复：评课 ORM 迁入共享数据库模型层，新增不阻塞 RabbitMQ 心跳的
  长任务消费者、数据库进度查询、终态回调补偿和更完整的执行日志。
- 删除内容分析启动时对旧模型模块的残留导入，并增加直接构建运行时的装配回归测试。
- 修复 Python 构建元数据，源码包和 Wheel 均可通过 `uv build` 生成。
- 让 15 份 Compose 定义在没有真实 `.env` 时也可执行配置校验，同时保留部署时注入环境文件的能力。
- 移除 FFmpeg 的 Shell 拼接执行和完整命令日志，修复旧视频接口与 YOLO 处理器中的未定义名称。
- 生产环境拒绝空值、公开占位符或少于 16 个字符的共享 API Key。
- 收紧直播录制请求日志，不再输出完整请求体；移除旧媒体切片器中的硬编码示例地址。
- 增加中文 Bug、功能建议模板和 GitHub 仓库与分支保护清单。
- 增加默认 CODEOWNERS，由仓库所有者审查发布、安全、契约和数据库变更。
- CI 增加 Python 3.10–3.12 测试矩阵、Ruff 高置信静态检查、构建和 Compose 校验，并固定 uv 版本。
- 仓库曾短暂迁入个人 Organization 评估团队权限，随后按当前协作决定迁回个人 Public 仓库；
  本地 `origin` 已同步到 `huxiaodong1989/MediaFleet`。
- 首次 GitHub CI 和完整历史 Secret Scan 已通过。
- 将存在两个低危安全告警的 Torch 2.9.1 升级到 2.13.0，配套固定 Torchvision 0.28.0；
  移除没有 2.13 匹配版本的 Torchaudio，改用 `kaldi-native-fbank` 支持 FunASR 特征提取。
- CI 增加 `media-worker` 完整可选依赖安装、Torch/FunASR 导入和漏洞审计，防止默认依赖
  测试掩盖媒体栈解析错误。
- CI 增加 `image-analysis` 可选依赖安装和服务入口导入检查，防止默认依赖测试掩盖视觉
  模型客户端或图像服务装配错误。
- GitHub Actions 升级到 Node.js 24 运行时版本，并固定 Ubuntu 24.04 Runner。
- GPU Worker 对齐 CUDA 12.6、`cu126` wheel 与 Torch 2.13，CPU 与 GPU 镜像使用同一
  Torch/Torchvision 版本对。
- Torch 修复后的 GitHub CI、完整历史 Secret Scan 和 Dependabot 更新任务均通过，两个
  Torch 安全告警已自动关闭，当前开放安全告警为 0。
- 已关闭被主分支覆盖的 GitHub Actions Dependabot PR；Torchaudio PR 因依赖移除自动关闭。
- 修复容器构建对单一 HTTP Debian 镜像源的硬依赖：Debian 服务镜像默认使用官方 HTTPS
  源，并允许通过 `DEBIAN_MIRROR_HOST` 在受限网络中选择兼容镜像站；GPU Worker 的 Ubuntu
  软件源也统一升级为 HTTPS。
- 将 SQLAlchemy 约束收紧为 `<2.1` 并锁定 `2.0.54`，避免 `sqlalchemy-utils 0.42.1`
  在真实数据库初始化时引用 SQLAlchemy 2.1 已移除接口，导致所有服务启动崩溃。
- 修复 ZLMediaKit 明确拒绝 `startRecord` 后录制任务永久停留在 `processing` 的问题；录制
  节点现在立即进入统一失败尾链路、释放本机容量，并避免误发 `stopRecord` 影响同技术流
  上的其他任务。
- 迁入独立课堂图像分析能力：控制中心提供接单与 Prompt 管理 API，图像服务完成来源筛选、
  时间轴采样、HTTP seek/整片下载回退、FFmpeg 抽帧、对象存储、多图视觉模型、持久检查点、
  任务租约、结果通知补发和节点心跳。
- 新增 `image.analysis.task` 独立任务拓扑和 `image.analysis.result` 可配置结果出口；生产者使用
  Publisher Confirm，消费者使用手动 ACK、有限重试和 DLQ。
- 新增图像分析数据库迁移、服务镜像、Quickstart/Portainer Compose、配置示例和中文架构文档；
  控制中心接单与图像消费者继续默认关闭，避免未配置模型、存储和视频允许列表时误接任务。
- 同步图像分析任务终态事件：采样事件补齐稳定定位字段，任务完成或最终失败后持久化唯一汇总
  通知，并等待全部采样通知发布完成或耗尽预算后再发布 `TASK_TERMINATED`。
- 修复图像任务旧重试消息恢复：RabbitMQ 重投的 attempt 落后于 MySQL `retry_count` 时进入
  可恢复重试，推进到持久事实代次；当前代次仍在执行时继续保持租约互斥。
- 新增 `20261012_0003` 增量迁移创建任务终态通知表，MySQL 外键列跟随现有任务主键字符定义，
  未修改可能已在环境执行的 `20261009_0002`。
- 新增独立 `mediafleet-sdk` Python 包，只依赖 `httpx` 和 `pydantic`，提供同步与异步客户端，
  覆盖健康、服务信息、运维总览、任务、产物、节点、绑定和录制服务器只读查询。
- SDK 统一使用 `X-API-Key` 请求头访问受保护接口，公开系统接口不发送密钥；实现安全错误
  分类、GET/HEAD 有限重试、`Retry-After`、分页筛选、UTC 时间兼容和未知字段向后兼容。
- 根项目使用 uv workspace 管理 SDK；CI 增加 Python 3.10–3.12 SDK 测试、轻量依赖隔离、
  SDK 构建和导入检查，并将 SDK 纳入 Ruff 与 Compileall。
- 新增独立 `mediafleet-cli` 包，通过 SDK 提供健康、总览、任务、产物、节点、绑定和录制
  服务器只读命令，支持 Rich 人工输出、稳定 JSON、有界分页、常用筛选和固定退出码。
- CLI context 配置只保存 URL 与可选 CA 文件并使用原子写入；API Key 只从环境变量或系统
  keyring 读取，不提供明文命令参数，也不会写入配置文件。
- CI 增加 Python 3.10–3.12 CLI 测试、轻量依赖检查、SDK/CLI Wheel 成对安装和命令入口检查。
- 新增独立 `mediafleet-mcp` 包，使用官方 Python MCP SDK 2.x 和 stdio 传输，通过异步 SDK
  提供健康、总览、任务、产物、节点、绑定和录制服务器 8 个只读工具。
- MCP 工具统一返回 `ok/data/error` 结构并限制分页；任务任意字段递归脱敏与限长，产物、节点
  和录制服务器响应不向模型暴露签名 URL、内部管理地址或本地目录。
- CI 增加 Python 3.10–3.12 MCP 测试、SDK/MCP Wheel 成对安装和服务端依赖边界检查。
- 新增持久调用方身份和范围化 API 凭据，支持 `read`、`task:write`、`recording:write`、
  `node:write` 和 `admin:write`；共享 `API_KEY` 暂时作为部署引导凭据兼容。
- API Key 使用随机秘密和 PBKDF2-SHA256 摘要，数据库不保存明文；凭据支持过期、立即轮换和
  吊销，创建与轮换时明文只返回一次。
- 受保护写请求在执行前写入可信身份审计，完成后记录 HTTP 结果；审计不可用时失败关闭，
  幂等键仅保存 SHA-256。两条旧视频处理兼容接口也补齐统一认证。
- SDK 同步和异步客户端增加安全资源；CLI 增加权限范围、凭据创建/列表/轮换/吊销和审计命令，
  轮换与吊销必须显式传入 `--yes`。MCP 继续保持只读。
- SDK 同步和异步客户端增加任务创建、失败任务重试、回调补发、流绑定创建/释放、录制
  开始/停止和录制服务器状态更新资源；任务创建要求稳定 `Idempotency-Key`，所有写请求默认
  不自动重试。
- CLI 增加对应业务写命令；复杂请求只接受二选一的 UTF-8 JSON 文件或标准输入，限制为
  1 MiB 并拒绝额外字段。重试、释放、停止录制和状态变更必须显式传入 `--yes`。
- 通用任务创建、任务重试、流绑定释放和录制服务器状态更新改用认证 principal 作为可信
  创建人或更新人；最小权限映射覆盖 `task:write`、`recording:write` 和 `admin:write`。
- 控制中心新增 `CONTROL_CENTER_BOOTSTRAP_API_KEY_ENABLED` 类型化开关；关闭共享引导密钥前
  必须在 MySQL 中存在有效的 `admin:write` 管理凭据，否则拒绝启动，避免管理入口锁死。
- 旧任务、媒体文件和录制接口 Schema 已迁移到 Pydantic V2 的 `ConfigDict`、
  `field_validator` 和 `model_dump`，原有字段、OpenAPI 示例与毫秒时间格式保持兼容。

## 验证

- 2026-10-16 迁入后全量测试 `716 passed, 3 skipped`；新增运行配置与评课专项
  `51 passed`。Ruff、`uv lock --check`、Compileall、管理页 JavaScript 语法、三份 Portainer
  Compose 配置及 `git diff --check` 通过。配置迁移在 SQLite 往返并检查 MySQL DDL；
  尚未在部门 MySQL/RabbitMQ 与真实模型上执行升级和热更新联调。

- `uv lock --check` 和 `uv build` 通过。
- `python -m compileall media_platform services alembic` 通过。
- 最新全量验证：`uv run python -m pytest -q`，401 项通过。
- 内容分析、评课查询和共享表结构专项验证：33 项通过。
- 15 份 Docker Compose 定义使用公开示例环境执行 `config --quiet` 通过。
- `uv run ruff check media_platform services tests` 通过。
- 本地完整 `media-worker` 环境导入 Torch 2.13.0、Torchvision 0.28.0 和
  `kaldi-native-fbank` 通过；确认环境中不再安装 Torchaudio。
- `cu126` 官方索引包含 Linux Python 3.10 的 Torch 2.13.0 与 Torchvision 0.28.0 wheel。
- `pip-audit` 未发现已知漏洞；Gitleaks `v8.28.0` 对完整 Git 历史扫描未发现泄漏。
- 已检查旧仓库名称、Shell 执行、硬编码示例地址、敏感路径和 `git diff --check`。
- 四个 CPU 服务镜像均在 Docker Desktop 构建成功：`control-center`、`recorder-node`、
  `content-analysis` 和 `media-worker`；CPU Worker 镜像已确认包含 FFmpeg、Torch
  `2.13.0+cpu`、Torchvision `0.28.0+cpu`、FunASR `1.4.16`、ModelScope `1.32.0`
  和 Transformers `5.13.0`。
- GPU Worker 镜像在 Docker Desktop 构建成功，镜像内 Torch `2.13.0+cu126`、
  Torchvision `0.28.0+cu126` 和 CUDA `12.6` 导入通过；当前主机没有可用 NVIDIA
  运行时，`torch.cuda.is_available()` 为 `False`，因此未执行 GPU 推理。
- 使用独立临时 MySQL 8.4、RabbitMQ、S3 兼容对象存储和现有测试 ZLMediaKit 完成冒烟：
  四个服务健康检查通过，Worker、Recorder 和 Content Analysis 节点心跳均写入 MySQL；
  对象存储建桶、写入和读回通过；ZLMediaKit API 鉴权调用成功。
- 通过控制中心创建 `video.cover.extract` 任务，CPU Worker 完成真实 2 秒视频封面提取，
  MySQL 记录 `completed`、执行节点和 `COVER` 产物，对象存储中存在对应文件。
- 通过控制中心创建不存在源流的录制任务，ZLMediaKit 返回拒绝后任务进入 `failed`，RabbitMQ
  命令已消费，MySQL 保留执行节点与明确错误原因；专项回归测试覆盖“不发送 stopRecord”
  和释放容量行为。
- 通过控制中心创建 AI 评课任务，内容分析节点成功下载临时字幕、执行有限重试，并在隔离的
  不可达占位 LLM 地址上以 `failed` 终止；确认任务发布、消费、执行节点、重试和 MySQL
  终态链路有效，测试未调用真实外部模型。
- 最新全量验证：`uv run python -m pytest -q`，402 项通过；`uv lock --check`、Ruff、
  Compileall 和 `git diff --check` 通过。
- 图像分析迁移后的最新全量验证：`671 passed, 3 skipped`；Ruff、Compileall、
  `uv lock --check`、`git diff --check` 和 17 份 Compose `config --quiet` 通过。
- 使用隔离 MySQL 8.4、RabbitMQ、MinIO 和 HTTP 视频服务完成图像端到端验证：调用中心接单、
  图像服务消费、两路视频统一时间点抽帧、多图模型替身、对象存储签名读取、MySQL 终态、
  结果 Exchange 两条事件和任务队列清空均通过。
- 在真实 MySQL 8.4 上完成 Alembic `base -> 20261009_0002 -> base` 升降级；同时修复基线
  回退先删除外键支撑索引导致 MySQL ERROR 1553 的旧问题。
- 图像分析镜像 `mediafleet-image-analysis:migration-test` 已在 Docker Desktop 完整构建；容器内
  FFmpeg 5.1.9、服务入口导入和消费者关闭状态下的 `/health` 检查通过。
- Gitleaks `v8.28.0` 对当前跟踪与未跟踪源码扫描未发现泄漏；旧业务名称、内网地址、缓存
  文件和凭据模式复核未发现新增问题。
- Dependabot PR #5、#7、#8 均只修改 `pyproject.toml` 而未同步 `uv.lock`，当前质量检查
  因锁文件不一致失败，不能直接合并。MoviePy #5 还涉及 1.x 到 2.x 的破坏性 API 变化，
  现有代码仍导入已移除的 `moviepy.editor`，必须先迁移代码并执行媒体回归；ModelScope #7
  和 Transformers #8 的目标版本可独立导入，但仍需更新锁文件后执行与 Torch、FunASR、
  GLM-ASR 模型组合的加载和推理验证。
- 仓库迁移后 Dependabot 新建 Torch #10 和 Torchvision #9 更新 PR。两者属于必须保持版本配对
  的媒体栈依赖，不能分别合并；当前检查失败，应在同一兼容分支中成对升级并重新执行 CPU、
  GPU 镜像构建及模型回归。
- 图像终态通知、旧重试代次恢复、发布器事件类型和增量迁移专项验证：`35 passed`；相关 Ruff
  检查和 `git diff --check` 通过。
- 本次同步后的全量验证：`679 passed, 3 skipped`；`uv lock --check`、Ruff、Compileall 和
  `git diff --check` 通过。
- 在临时 MySQL 8.4 上完成 `20261009_0002 -> 20261012_0003 -> 20261009_0002 -> head`
  升降级，确认新增外键按父表实际字符定义创建，最终 head 为 `20261012_0003`。
- 图像分析镜像 `mediafleet-image-analysis:terminal-events-test` 构建成功；消费者关闭时容器内
  `/health` 返回正常，FFmpeg 5.1.9 可用。
- Gitleaks `v8.28.0` 对本次涉及的应用、服务、迁移、测试和中文文档目录扫描未发现泄漏；
  旧业务名称、内部地址和凭据模式复核未发现新增内容。
- SDK 单元测试 `18 passed`，覆盖同步/异步客户端、认证头、分页参数、错误映射、密钥脱敏、
  重试、上下文关闭、UTC 时间、未知字段和全部只读资源响应解析。
- SDK 与控制中心 OpenAPI 契约及真实 FastAPI 系统接口测试 `2 passed`；SDK sdist 和 Wheel
  构建成功，独立 SDK 环境确认不包含 FastAPI、SQLAlchemy、Pika 或 Torch。
- SDK 实施后的根仓库全量验证为 `681 passed, 3 skipped`；Gitleaks `v8.28.0` 对 SDK、契约
  测试、相关文档、CI、根构建配置和锁文件扫描未发现泄漏。
- CLI 单元和 SDK 请求链路测试 `32 passed`，覆盖全部只读命令、分页与筛选、context、凭据
  标准输入、keyring 不可用、JSON 标准输出/错误输出、退出码、密钥不回显和公开健康端点不发送
  认证头。
- CLI sdist 与 Wheel 构建成功；在干净 Python 3.12 环境中成对安装 SDK/CLI Wheel 后，
  `mediafleet --help` 和 `mediafleet version --json` 通过，且环境不包含 FastAPI、SQLAlchemy、
  Pika 或 Torch。
- CLI 实施后的根仓库全量验证为 `681 passed, 3 skipped`；`uv lock --check`、Ruff、额外
  F401 检查、Compileall 和 `git diff --check` 通过。
- MCP 单元、工具契约和 stdio 协议测试 `23 passed`，覆盖 8 个工具、只读注解、分页上限、
  筛选透传、结构化错误、敏感字段裁剪，以及通过真实 stdio 子进程和临时 HTTP 控制中心完成
  的异步 SDK 调用与认证头验证；Python 3.10、3.11、3.12 均通过。
- MCP sdist 与 Wheel 构建成功；在干净 Python 3.12 环境中成对安装 SDK/MCP Wheel 后可发现
  8 个工具，且环境不包含 FastAPI、SQLAlchemy、Pika、Torch 或 keyring。
- CLI 与 MCP 独立依赖环境执行 `pip-audit` 均未发现已知漏洞；本地未发布的三个 MediaFleet
  包按工具预期跳过公共索引查询。
- MCP 实施后的根仓库全量验证为 `681 passed, 3 skipped`；`uv lock --check`、Ruff、额外
  F401 检查、Compileall 和 `git diff --check` 通过。
- 身份权限专项、数据库迁移、SDK 和 CLI 验证共 `73 passed`；控制中心回归 `86 passed`，
  Ruff 和 `git diff --check` 通过。Alembic head 更新为 `20261019_0005`，空 SQLite 数据库
  升级与回退验证覆盖三张安全表。
- 身份权限基础层实施后的根仓库全量验证为 `685 passed, 3 skipped`；根包、SDK、CLI 和 MCP
  的 sdist/Wheel 构建、Compileall、Ruff、`uv lock --check` 和 `git diff --check` 均通过。
- 受控业务写入口专项验证：SDK、CLI 与 OpenAPI 契约 `81 passed`，控制中心任务、权限、绑定
  和录制相关 `42 passed`；覆盖同步/异步请求、路径编码、幂等键、权限拒绝、可信操作者、
  JSON 输入限制和显式确认。
- 本轮完成后的根仓库全量验证为 `689 passed, 3 skipped`；根包、SDK、CLI 和 MCP 的
  sdist/Wheel 构建、Compileall、Ruff、`uv lock --check` 和 `git diff --check` 均通过。
- 共享引导密钥开关与配置专项验证为 `26 passed`，覆盖默认兼容、关闭后拒绝共享密钥、有效
  管理凭据放行，以及只读、过期、吊销和调用方停用等防锁死场景。
- 本轮最新全量验证为 `698 passed, 3 skipped`；根包、SDK、CLI 和 MCP 的 sdist/Wheel 构建、
  17 份 Compose 配置、Compileall、Ruff、`uv lock --check` 和 `git diff --check` 均通过。
- Pydantic V2 严格弃用检查下全仓验证为 `701 passed, 3 skipped`，测试将
  `PydanticDeprecatedSince20` 提升为错误；旧录制请求校验、OpenAPI 示例和毫秒时间序列化均有
  回归覆盖。
- 稳定 Worker 租约续租回归测试：处理器仍运行超过初始租约，首次续租窗口由 100 毫秒扩大到
  1 秒，避免 Python 3.10 CI 高负载时后台线程尚未获得调度便被误判为失去租约；生产租约条件
  与过期写入保护保持不变。专项测试连续 6 次通过，全仓验证为 `701 passed, 3 skipped`。
- PR #14 已重新基于包含 SDK 与运行配置更新的最新 `main` 排列；运行配置继续使用
  `20261016_0004`，安全凭据迁移顺延为 `20261019_0005` 并依赖前者，Alembic 保持单一 head。
  冲突解决后的专项验证为 `38 passed`，全仓验证为 `738 passed, 3 skipped`；Ruff、Compileall、
  `uv lock --check`、17 份 Compose 配置、Alembic `base -> head -> base` 往返和
  `git diff --check` 通过。

## 剩余风险

- 新增运行配置表须在服务升级前由单个迁移执行者部署。首次配置导入、现有 RabbitMQ
  队列 ACK 超时策略、在线实例热更新回执和任务锁版仍需在部门环境验证；配置加密密钥
  必须在控制中心及两个分析服务一致并由部署平台保管。
- 最新 Starlette 测试客户端提示 `httpx` 集成将弃用并迁移到 `httpx2`。
- 可选模型、FFmpeg 构建、Ultralytics 部署和厂商数据库驱动需要按部署环境单独审查许可证。
- GPU Worker 实际 CUDA 推理仍需在有 NVIDIA Container Toolkit 和兼容 GPU 的 Linux
  主机验证。
- 当前冒烟使用临时数据库、消息队列和 S3 兼容服务；长时间运行、断网恢复、消息重复投递、
  数据库故障切换和生产对象存储兼容性仍需在部门环境验证。
- 部分旧兼容 API 和模型尚未迁移到统一的新应用服务。
- 容器仍以 root 用户运行，尚未增加只读文件系统、Linux capability 限制和镜像安全扫描。
- 回调、下载和流媒体 URL 尚未建立按用途配置的主机或 CIDR 允许列表。
- 共享引导 `API_KEY` 为兼容升级默认启用；部门部署必须先给管理方和全部节点签发、验证最小
  权限凭据，再主动关闭。节点迁移完成前关闭会导致心跳和内部调用认证失败。
- 当前仓库已公开，公开历史仍需持续执行 Secret Scan；分支保护和 required checks 需要在
  GitHub 仓库规则中确认已应用到 `main`。
- 图像分析尚未在部门真实录像、对象存储和视觉模型上完成容量及故障恢复验收，接单与消费
  开关保持关闭。结果 Exchange 的下游绑定也必须由部署环境明确配置。
- 图像结果通知当前冻结带时效的签名读取地址；长时间下游中断可能使补发消息中的地址过期，
  应在正式启用结果出口前改为按稳定对象键重新签名，或由下游通过受控接口换取读取地址。
- 新增任务终态事件尚未在真实 RabbitMQ 下游绑定和连接中断场景完成部门环境验收，启用前应
  验证采样事件严格先于任务终态，并确认下游按 `eventType` 幂等消费。
- MoviePy、ModelScope 和 Transformers 仍各有一个大版本升级 PR，需要结合媒体处理、模型
  加载和推理回归测试逐项评估，不能直接自动合并。
- SDK 已支持共享引导密钥和范围化凭据，但共享 `API_KEY` 仍具有全权限；正式部署应为 CLI、
  节点和自动化分别签发最小权限凭据。SDK 尚未发布 PyPI，也未承诺稳定公共 API。
- CLI 尚未连接部门真实控制中心验证证书、keyring 后端和人工表格显示；SDK 与 CLI 均未发布
  PyPI，因此当前安装验证需要同时提供两个同版本本地 Wheel。
- MCP 尚未接入部门真实 MCP 客户端和控制中心验证长时间 stdio 会话、自定义 CA 与模型调用
  体验；官方 MCP SDK 包同时包含未启用的 HTTP 传输依赖，发布前仍需持续进行依赖审计。

## 下一步

在部门测试环境备份 MySQL 后升级到 Alembic head，依次应用运行配置 `20261016_0004` 与安全
凭据 `20261019_0005`，再由单实例导入两类分析服务原部署配置，验证配置发布/回滚、真实任务
锁版、RabbitMQ ACK 超时与回调兼容。图像接单继续保持关闭，直到录像、对象存储、视觉模型、
终态通知和签名地址补发通过联调。
在部门测试控制中心验证真实证书、keyring、最小权限凭据、范围拒绝、轮换、吊销、审计和
全部 CLI 写命令，并补充重复请求、连接中断和回调补发的环境验收。MCP 默认继续只读；确认
是否需要模型触发写操作前，先完成独立允许列表和逐工具风险审核。三个包进入 PyPI 前继续
保持 `0.1.0` Alpha。
