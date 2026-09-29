# MediaFleet 文档导航

本目录用于记录当前可执行的架构约束、开发约定和运维方法。旧项目文档中的历史方案
只用于追溯背景；实现发生变化时，应同步修改这里的对应文档，避免代码与说明分离。

## 新成员阅读顺序

1. [项目背景与演进](project-background.md)：了解项目来源、改造目标和当前阶段。
2. [总体架构](architecture/overview.md)：了解组件、主链路、部署拓扑和扩展方式。
3. [服务边界与依赖规则](architecture/service-boundaries.md)：明确代码应该放在哪里。
4. [数据一致性与消息可靠性](architecture/data-and-messaging.md)：理解 MySQL、RabbitMQ、幂等、重试和恢复规则。
5. [扩展开发指南](development/extending-mediafleet.md)：按场景新增 API、任务、处理器或服务。
6. [贡献指南](../CONTRIBUTING.md)：了解分支、提交、测试和 Pull Request 要求。

## 架构文档

- [总体架构](architecture/overview.md)
- [服务边界与依赖规则](architecture/service-boundaries.md)
- [数据一致性与消息可靠性](architecture/data-and-messaging.md)
- [REST、SDK、CLI 与 MCP 接口架构](architecture/interfaces.md)
- [架构决策记录](decisions/README.md)

## 开发与运维

- [扩展开发指南](development/extending-mediafleet.md)
- [Docker Compose 部署](deployment/docker-compose.md)
- [GitHub Private 仓库上线清单](deployment/github-private.md)
- [配置与密钥管理](security/configuration-and-secrets.md)
- [依赖许可证审查](security/dependency-licenses.md)

## 项目管理

- [当前项目状态](project-status.md)
- [路线图](roadmap.md)
- [变更记录](../CHANGELOG.md)

## 文档维护约定

- Markdown 以中文为默认语言，代码标识、协议名和配置项保留原始英文。
- 文档描述当前行为；未实现的内容必须明确标注“规划中”。
- 修改服务边界、持久化模型、消息拓扑或公开接口时，必须更新相关架构文档。
- 图使用 Mermaid，命令应能在仓库根目录直接执行，示例不得包含真实凭据和内网地址。
- 一次功能变更结束后，在 `docs/project-status.md` 记录完成范围、验证、风险和下一步。
