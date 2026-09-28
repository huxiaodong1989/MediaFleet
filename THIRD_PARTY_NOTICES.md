# Third-party notices

MediaFleet source code is distributed under the MIT License. Dependencies,
external runtimes, downloaded models, and user-supplied assets retain their own
licenses.

Important examples include:

- FFmpeg: license depends on the concrete build and enabled codecs.
- ZLMediaKit: deployed as an external runtime under its own license.
- Ultralytics: current open-source releases are AGPL-3.0. Object-detection
  deployments must comply with that license or use a commercial license.
- FunASR, ModelScope, Transformers, PyTorch, and individual ASR/model artifacts:
  library and model licenses must both be reviewed before redistribution.
- OpenCV and other Python dependencies retain their published licenses.

This repository intentionally excludes model weights, proprietary fonts, and
assets whose redistribution rights have not been established.

See [the dependency license review](docs/security/dependency-licenses.md) for
the reviewed base environment and optional-component decisions.
