# 独立 AI 评课服务

该服务只负责 `content.class_evaluation` 工作流，不包含旧 agents 项目的其他 Agent、
聊天或知识库功能。调用中心把公共任务写入 `media_task`，再投递到独立
`content.analysis.task` Exchange；所有评课实例竞争消费同一个
`content-analysis.tasks` 持久化队列。

提示词首次启动时从 `bootstrap_prompts/` 写入数据库，之后运行任务只读取
`prompt_version` 中已发布版本。调用中心管理后台的“AI评课提示词”页面直接管理该共享
数据库表，创建草稿、修改、发布和回滚都不依赖某个内容分析实例，也不需要重启服务。

启动日志会输出经过脱敏的大模型地址、模型和 API Key 摘要，并明确提示本次是从
`bootstrap_prompts/` 写入第一个发布版本，还是复用数据库中已有的发布版本。

本地启动：

```powershell
Copy-Item services/content_analysis/.env.example services/content_analysis/.env
uv sync --extra content-analysis
uv run python -m services.content_analysis.main
```

健康检查：`http://127.0.0.1:8012/health`。
