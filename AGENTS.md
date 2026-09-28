# MediaFleet agent instructions

## Project boundaries

MediaFleet is a distributed media control and processing platform composed of
independently deployable services.

- MySQL is the source of truth for nodes, bindings, tasks, dispatch state, and
  media artifacts.
- RabbitMQ provides at-least-once delivery. Producers and consumers must be
  idempotent and use publisher confirms, manual acknowledgements, bounded
  retries, and dead-letter queues.
- A recording command is always routed to the recorder node that owns the
  corresponding ZLMediaKit instance and recording files.
- Generic media tasks are consumed competitively from a shared durable queue.
- Offline ASR model loading and inference remain inside `media-worker`.
- The control plane must not run FFmpeg, read recorder-local directories, or
  load media/AI models.
- In-memory state and Redis may optimize runtime behavior but must not become
  the only source of distributed correctness.

## Before changing code

1. Run `git status --short --branch` and preserve existing work.
2. Read `docs/architecture/overview.md`.
3. Read `docs/project-status.md` and continue from the first unfinished item.
4. Inspect every producer and consumer before changing a message or API
   contract.
5. Keep changes within one coherent service boundary whenever possible.

## Security and open-source hygiene

- Never commit `.env` files, credentials, tokens, private keys, signed URLs,
  credential-bearing RTSP URLs, or internal network addresses.
- Logs must redact URL user information and sensitive query parameters.
- Do not add binary model weights or fonts without a documented redistributable
  license.
- Configuration examples must use empty values or unmistakable placeholders.
- Runtime configuration APIs must be typed and allowlisted; never expose an
  arbitrary environment-variable mutation endpoint.

## Interfaces

REST, CLI, MCP, and the operations console must call the same application
services. CLI and MCP adapters should normally access the control-plane API via
the shared SDK rather than bypassing authorization through direct database or
RabbitMQ access.

## Completion

Run tests and static checks proportional to the change. Update
`docs/project-status.md` with completed scope, validation, remaining risks, and
one concrete next action. Update architecture documentation when a durable
decision changes.
