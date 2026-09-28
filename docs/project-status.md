# 项目状态

## 当前阶段

MediaFleet `0.1.0` 公开基线已经具备，可创建新的 GitHub 仓库。仓库中文档以中文为默认
语言，核心架构与扩展开发规则已形成可供共同开发使用的基线。

## 已完成

- 从原 `develop` 源码快照导出代码，不携带原 Git 历史，并在 `main` 初始化独立仓库。
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

## 验证

- 公开基线验证：`uv sync --frozen` 通过。
- 公开基线验证：`python -m compileall media_platform services alembic` 通过。
- 最新全量验证：`uv run python -m pytest -q`，396 项通过。
- 内容分析、评课查询和共享表结构专项验证：33 项通过。
- `python -m compileall media_platform services tests/content_analysis` 通过。
- 15 份 Docker Compose 定义使用公开示例环境执行 `config --quiet` 通过。
- Gitleaks 暂存树扫描未发现泄漏；`pip-audit` 未发现已知漏洞。
- 本次文档改造已检查 Markdown 链接目标、敏感路径与 `git diff --check`。

## 剩余风险

- 兼容 Schema 仍会产生 Pydantic V2 弃用警告，应在 Pydantic V3 前完成现代化。
- 最新 Starlette 测试客户端提示 `httpx` 集成将弃用并迁移到 `httpx2`。
- 可选模型、FFmpeg 构建、Ultralytics 部署和厂商数据库驱动需要按部署环境单独审查许可证。
- 真实 MySQL、RabbitMQ、ZLMediaKit 和对象存储集成测试仍依赖外部环境。
- 部分旧兼容 API 和模型尚未迁移到统一的新应用服务。

## 下一步

创建目标 GitHub 仓库、添加远程地址并推送 `main`；随后从类型化 Python SDK 和 CLI 基础
开始，再实现独立 MCP 适配器。
