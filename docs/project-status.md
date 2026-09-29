# 项目状态

## 当前阶段

MediaFleet `0.1.0` 已完成外部 GitHub Private 托管前的第一轮清理，可作为公司内部协作
基线。完成公开发布检查前，仓库、镜像和 Release 产物保持 Private，不将当前版本标记为
稳定公开版本。

## 已完成

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
- 增加中文 Bug、功能建议模板和 GitHub Private 仓库上线清单。
- 增加默认 CODEOWNERS，由仓库所有者审查发布、安全、契约和数据库变更。
- CI 增加 Python 3.10–3.12 测试矩阵、Ruff 高置信静态检查、构建和 Compose 校验，并固定 uv 版本。
- 已在 `huxiaodong1989/MediaFleet` 创建 Private 仓库、合并 GitHub 初始提交并推送 `main`。
- 首次 GitHub CI 和完整历史 Secret Scan 已通过。
- 将存在两个低危安全告警的 Torch 2.9.1 升级到 2.13.0，配套固定 Torchvision 0.28.0；
  移除没有 2.13 匹配版本的 Torchaudio，改用 `kaldi-native-fbank` 支持 FunASR 特征提取。
- CI 增加 `media-worker` 完整可选依赖安装、Torch/FunASR 导入和漏洞审计，防止默认依赖
  测试掩盖媒体栈解析错误。
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

## 验证

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
- Dependabot PR #5、#7、#8 均只修改 `pyproject.toml` 而未同步 `uv.lock`，当前质量检查
  因锁文件不一致失败，不能直接合并。MoviePy #5 还涉及 1.x 到 2.x 的破坏性 API 变化，
  现有代码仍导入已移除的 `moviepy.editor`，必须先迁移代码并执行媒体回归；ModelScope #7
  和 Transformers #8 的目标版本可独立导入，但仍需更新锁文件后执行与 Torch、FunASR、
  GLM-ASR 模型组合的加载和推理验证。

## 剩余风险

- 兼容 Schema 仍会产生 Pydantic V2 弃用警告，应在 Pydantic V3 前完成现代化。
- 最新 Starlette 测试客户端提示 `httpx` 集成将弃用并迁移到 `httpx2`。
- 可选模型、FFmpeg 构建、Ultralytics 部署和厂商数据库驱动需要按部署环境单独审查许可证。
- GPU Worker 实际 CUDA 推理仍需在有 NVIDIA Container Toolkit 和兼容 GPU 的 Linux
  主机验证。
- 当前冒烟使用临时数据库、消息队列和 S3 兼容服务；长时间运行、断网恢复、消息重复投递、
  数据库故障切换和生产对象存储兼容性仍需在部门环境验证。
- 部分旧兼容 API 和模型尚未迁移到统一的新应用服务。
- 容器仍以 root 用户运行，尚未增加只读文件系统、Linux capability 限制和镜像安全扫描。
- 回调、下载和流媒体 URL 尚未建立按用途配置的主机或 CIDR 允许列表。
- 当前内部接口仍使用共享 API Key，尚未提供调用方身份、权限范围、轮换和撤销机制。
- 当前仓库属于个人 GitHub Free 账号；GitHub API 明确拒绝为 Private 仓库启用分支保护，需升级 GitHub Pro、转入支持该功能的 Organization，或在改为 Public 后启用。
- MoviePy、ModelScope 和 Transformers 仍各有一个大版本升级 PR，需要结合媒体处理、模型
  加载和推理回归测试逐项评估，不能直接自动合并。

## 下一步

在部门测试环境部署当前镜像，执行长时间录制、停止后处理、重复消息和服务重启恢复测试。
MoviePy、ModelScope 和 Transformers 升级分别建立兼容迁移分支，更新锁文件并完成真实媒体
与模型回归后再合并。通过后发布 `v0.1.0-internal.1` 供部门内部验证，并决定升级 GitHub Pro
或将仓库转入公司 Organization，以启用 Private 仓库分支保护。
