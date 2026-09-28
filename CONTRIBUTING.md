# 贡献指南

感谢参与 MediaFleet 开发。本项目默认使用中文编写 Issue、Pull Request 和 Markdown 文档；
代码标识、协议名称和配置项保持英文。

## 开发流程

1. 从默认分支创建职责单一的功能分支。
2. 开始前阅读[文档导航](docs/README.md)、[总体架构](docs/architecture/overview.md)和
   [扩展开发指南](docs/development/extending-mediafleet.md)。
3. 服务私有行为留在对应 `services/<name>`，共享领域规则与契约放入 `media_platform`。
4. 消息契约、幂等、状态迁移、路由和数据库变更需要添加或更新测试。
5. 运行相关测试、静态检查和 `git diff --check`。
6. 公开行为或架构发生变化时同步更新文档和 `docs/project-status.md`。

## 提交与 Pull Request

提交信息建议使用 `类型: 中文说明`，例如 `docs: 补充媒体任务扩展指南`。一次提交应围绕一个
清晰目的，不要把大规模目录移动和无关行为修改混在一起。

Pull Request 至少说明：

- 解决的问题和可观察到的行为变化；
- 选择的设计及其服务边界；
- 执行过的验证；
- 对部署、数据库迁移、消息兼容、性能和许可证的影响；
- 已知风险与后续工作。

## 测试要求

变更应选择能证明关键行为的测试。常用命令：

```bash
uv run python -m pytest -q
uv run python -m compileall media_platform services alembic
git diff --check
```

修改 Compose 时还要对受影响文件执行 `docker compose ... config --quiet`。修改消息契约时
应覆盖生产者、消费者、重试、DLQ 和幂等行为；修改数据库时应检查 Alembic 升降级路径。

## 安全与许可证

不得提交凭据、私有部署数据、带认证信息的 URL、专有资产或没有明确再分发许可的模型
权重。新增依赖、模型、字体、FFmpeg 构建或外部运行时时，应说明许可证影响。
