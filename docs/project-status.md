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
- 更新依赖锁；截至 2026-09-28，`pip-audit` 未报告已知漏洞。
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
- CI 增加 Python 3.10–3.12 测试矩阵、Ruff 高置信静态检查、构建和 Compose 校验，并固定 uv 版本。

## 验证

- `uv lock --check` 和 `uv build` 通过。
- `python -m compileall media_platform services alembic` 通过。
- 最新全量验证：`uv run python -m pytest -q`，401 项通过。
- 内容分析、评课查询和共享表结构专项验证：33 项通过。
- 15 份 Docker Compose 定义使用公开示例环境执行 `config --quiet` 通过。
- `uv run ruff check media_platform services tests` 通过。
- `pip-audit` 未发现已知漏洞；Gitleaks `v8.28.0` 对 4 个提交和当前待提交文件扫描均未发现泄漏。
- 已检查旧仓库名称、Shell 执行、硬编码示例地址、敏感路径和 `git diff --check`。

## 剩余风险

- 兼容 Schema 仍会产生 Pydantic V2 弃用警告，应在 Pydantic V3 前完成现代化。
- 最新 Starlette 测试客户端提示 `httpx` 集成将弃用并迁移到 `httpx2`。
- 可选模型、FFmpeg 构建、Ultralytics 部署和厂商数据库驱动需要按部署环境单独审查许可证。
- 真实 MySQL、RabbitMQ、ZLMediaKit 和对象存储集成测试仍依赖外部环境。
- 部分旧兼容 API 和模型尚未迁移到统一的新应用服务。
- 容器仍以 root 用户运行，尚未增加只读文件系统、Linux capability 限制和镜像安全扫描。
- 回调、下载和流媒体 URL 尚未建立按用途配置的主机或 CIDR 允许列表。
- 当前内部接口仍使用共享 API Key，尚未提供调用方身份、权限范围、轮换和撤销机制。
- GitHub Organization、仓库成员、CODEOWNERS、分支保护和 Private 可见性需要在目标仓库创建后配置。

## 下一步

确认公司允许使用外网 GitHub 托管并在公司 Organization 创建 Private 仓库；配置成员权限、
CODEOWNERS、分支保护、Secret scanning 和 Push protection 后再推送。首次 CI 与 Secret Scan
通过后发布 `v0.1.0-internal.1` 供部门内部验证。
