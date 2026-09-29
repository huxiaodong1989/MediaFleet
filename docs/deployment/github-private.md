# GitHub Private 仓库上线清单

本清单用于把 MediaFleet 托管到外网 GitHub，同时保持公司内部可见。完成公开发布检查前，
仓库可见性必须保持 `Private`，镜像和 Release 产物也不得发布到公共仓库。

## 创建仓库

- 优先使用公司 GitHub Organization，不使用个人账号承载正式仓库。
- 创建空仓库，不自动生成 README、License 或 `.gitignore`。
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

## 安全设置

- 启用 Secret scanning、Push protection、Dependabot alerts 和安全更新；
- 安全问题通过 Private vulnerability reporting 处理，不进入普通 Issue；
- Actions 默认权限设为只读，只对确实需要写入的工作流单独授权；
- 环境密钥保存在 GitHub Environments 或部署平台中，不写入仓库变量、Issue 或日志；
- 首次推送后重新执行 Gitleaks，并检查 GitHub 的安全告警页面。

## 首次推送与内部版本

添加远程地址前先确认目标仓库确实为 Private：

```bash
git remote add origin <private-repository-url>
git push -u origin main
```

内部验证通过后使用 `v0.1.0-internal.1` 这类预发布标签。完成容器安全、出站访问控制、
许可证闭环、真实基础设施集成测试和公开文档检查前，不创建稳定版 Release，也不改为 Public。
