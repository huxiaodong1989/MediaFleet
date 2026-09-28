# 依赖许可证审查

审查日期：2026-09-28

MediaFleet 源代码采用 MIT License。Python 包、外部运行时和下载的模型保留各自许可证。

## 基础与开发环境

通过 `uv sync --frozen` 生成的环境已使用 `pip-licenses` 审查。其直接和间接依赖主要采用
MIT、BSD、Apache-2.0、MPL-2.0 或 Python Software Foundation 许可证。

基础环境中需要注意的互惠许可证依赖是 `pycountry`，其报告为 LGPL。分发者应保留通知并
遵守对应库的再分发条款。准确解析版本记录在 `uv.lock`。

## 可选与外部组件

- `ultralytics` 提供 AGPL-3.0 或商业许可证。启用对象检测能力前必须完成部署许可证决策。
- FFmpeg 许可证取决于所选二进制、编解码器和构建参数。
- ZLMediaKit 是外部运行时，保留自身许可证。
- FunASR、ModelScope、Transformers、PyTorch 和每个下载模型都有独立的库或模型卡许可证；
  本仓库不包含模型权重。
- `dmPython` 和 `dmsqlalchemy` 是可选厂商集成，再分发前应审查厂商条款。

## 复现检查

```bash
uv sync --frozen
uvx pip-licenses --python .venv/bin/python --format=markdown --with-urls
uv run --with pip-audit pip-audit --progress-spinner off
```

Windows 上将 `pip-licenses` 命令中的 Python 路径替换为 `.venv/Scripts/python.exe`。
