# Docker Compose deployment

The Compose files under `deploy/compose/` are development and evaluation
examples. Copy `.env.example` to an untracked `.env`, replace every placeholder,
and review network exposure before starting services.

```bash
cp .env.example .env
docker compose --env-file .env -f deploy/compose/docker-compose.yml config
docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d
```

Production guidance:

- run database migrations as a separate release step;
- do not let every control-plane replica execute DDL concurrently;
- use unique stable identities for recorder nodes;
- use one shared queue name for equivalent media workers;
- mount one recording directory into ZLMediaKit and its paired recorder;
- inject secrets through the deployment platform;
- pin image versions and back up MySQL before migrations;
- validate disk, network, CPU/GPU, and post-processing capacity.
