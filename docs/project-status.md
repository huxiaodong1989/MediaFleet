# Project status

## Current phase

MediaFleet 0.1.0 public baseline is ready for a new GitHub repository.

## Completed

- Exported the original `develop` source snapshot without its Git history and
  initialized an independent repository on `main`.
- Renamed the project, services, images, database defaults, and physical
  tables to MediaFleet-neutral identifiers.
- Added MIT licensing, public contribution/security documents, architecture,
  deployment guidance, roadmap, dependency-license notes, and bilingual
  English/Chinese project landing pages.
- Removed populated environment files, private pipelines, internal logs and
  documents, paused private integrations, proprietary binary assets, model
  weights, and unused legacy implementations.
- Replaced the historical upgrade chain with the single clean Alembic baseline
  `20260928_0001` for empty public deployments.
- Removed embedded credentials and environment-specific endpoints. Logs now
  strip URL user information, query strings, and fragments before output.
- Documented REST as the stable remote contract and reserved SDK-backed CLI
  and MCP adapters without granting them direct database or RabbitMQ access.
- Upgraded the dependency lock to versions with no vulnerabilities reported by
  `pip-audit` on 2026-09-28.

## Validation

- `uv sync --frozen`: passed.
- `python -m compileall media_platform services alembic`: passed.
- `python -m pytest -q`: 384 passed.
- All 15 Docker Compose definitions: `config --quiet` passed using the public
  example environment.
- Gitleaks staged-tree scan: no leaks found.
- `pip-audit`: no known vulnerabilities found.
- Base dependency licenses reviewed; AGPL and model/runtime caveats are
  documented separately.

## Remaining risks

- Compatibility schemas still emit Pydantic V2 deprecation warnings and should
  be modernized before Pydantic V3.
- The latest Starlette test client warns that its `httpx` integration is
  deprecated in favor of `httpx2`.
- Optional model artifacts, FFmpeg builds, Ultralytics deployment, and vendor
  database drivers require deployment-specific license review.
- Real MySQL, RabbitMQ, ZLMediaKit, and object-storage integration tests remain
  environment-dependent and were not run during repository extraction.

## Next action

Create the target GitHub repository, add its remote, and push `main`; then begin
the typed SDK and CLI foundation before implementing the separate MCP adapter.
