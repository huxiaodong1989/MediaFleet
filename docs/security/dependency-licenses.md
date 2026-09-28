# Dependency license review

Review date: 2026-09-28

MediaFleet source code is licensed under MIT. Python packages, external
runtimes, and downloaded model artifacts keep their own licenses.

## Base and development environment

The environment produced by `uv sync --frozen` was reviewed with
`pip-licenses`. Its direct and transitive packages are predominantly MIT,
BSD, Apache-2.0, MPL-2.0, or Python Software Foundation licensed.

The notable reciprocal dependency in the base environment is `pycountry`,
which reports LGPL licensing. Distributors should retain its notices and
comply with the applicable library redistribution terms. Exact resolved
versions are recorded in `uv.lock`.

## Optional and external components

- `ultralytics` is offered under AGPL-3.0 or a commercial license. Enabling
  the object-detection extra requires an explicit deployment licensing
  decision.
- FFmpeg licensing depends on the selected binary, codecs, and build flags.
- ZLMediaKit is an external runtime and retains its own license.
- FunASR, ModelScope, Transformers, PyTorch, and every downloaded model have
  separate library and model-card licenses. Model weights are not included in
  this repository.
- `dmPython` and `dmsqlalchemy` are optional vendor integrations; review the
  vendor terms before redistribution.

## Reproduce the checks

```bash
uv sync --frozen
uvx pip-licenses --python .venv/bin/python --format=markdown --with-urls
uv run --with pip-audit pip-audit --progress-spinner off
```

On Windows, use `.venv/Scripts/python.exe` for the `pip-licenses` command.
