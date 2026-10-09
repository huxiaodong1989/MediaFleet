# GitHub 仓库与分支保护清单

MediaFleet 当前托管在个人 GitHub Public 仓库。本清单保留首次迁移时的安全要求，并用于
持续检查分支保护、协作权限、密钥扫描和发布边界。源码公开不代表镜像或 Release 已达到
稳定发布标准。

## 创建仓库

- 仓库所有者负责可见性、管理员权限和最终合并；以后迁入公司 Organization 时重新审计权限。
- 首次创建仓库时使用空仓库，不自动生成 README、License 或 `.gitignore`。
- 限制仓库管理员和可修改可见性的人员。
- 确认源代码、历史业务代码及第三方资产已经获得外部托管授权。

## 默认分支保护

为 `main` 配置规则：

- 只能通过 Pull Request 合并，至少需要一名 Reviewer；
- 要求所有讨论解决后才能合并；
- 要求 `CI / quality`、三个 Python 测试任务、依赖审计和 Secret Scan 通过；
- 禁止 force push 和删除分支；
- 管理员也遵守相同规则；
- 确定 Organization 用户或团队后添加 `.github/CODEOWNERS`。

Public 仓库应启用规则集。仓库内提供 `github-main-ruleset.json` 作为导入模板；required checks
名称必须与当前 Actions 工作流实际产生的检查名称一致。

## 安全设置

- 启用 Secret scanning、Push protection、Dependabot alerts 和安全更新；
- 安全问题通过 Private vulnerability reporting 处理，不进入普通 Issue；
- Actions 默认权限设为只读，只对确实需要写入的工作流单独授权；
- 环境密钥保存在 GitHub Environments 或部署平台中，不写入仓库变量、Issue 或日志；
- 首次推送后重新执行 Gitleaks，并检查 GitHub 的安全告警页面。

## 首次推送与候选版本

添加远程地址前确认目标仓库所有者和可见性符合当前发布决定：

```bash
git remote add origin <repository-url>
git push -u origin main
```

部门验证通过后可使用 `v0.1.0-rc.1` 这类预发布标签。完成容器安全、出站访问控制、许可证
闭环、真实基础设施集成测试和公开文档检查前，不创建稳定版 Release。
