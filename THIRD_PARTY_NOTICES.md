# 第三方组件说明

MediaFleet 源代码采用 MIT License。依赖、外部运行时、下载的模型和用户提供的资产保留
各自许可证。

需要特别注意：

- FFmpeg：许可证取决于具体构建、启用的编解码器和链接选项。
- ZLMediaKit：作为外部运行时部署，遵循其自身许可证。
- Ultralytics：当前开源版本采用 AGPL-3.0。对象检测部署必须满足该许可证或取得商业许可。
- FunASR、ModelScope、Transformers、PyTorch 和各个 ASR/AI 模型：库许可证与模型许可证
  都需要在再分发前分别审查。
- OpenCV 和其他 Python 依赖保留其发布许可证。

仓库有意排除模型权重、专有字体和无法确认再分发权利的资产。基础环境和可选组件的审查
结果见[依赖许可证审查](docs/security/dependency-licenses.md)。
