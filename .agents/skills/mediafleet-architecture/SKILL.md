---
name: mediafleet-architecture
description: Maintain MediaFleet's control plane, recorder nodes, workers, messaging, persistence, CLI, MCP, and operations interfaces.
---

# MediaFleet architecture skill

Before making architecture or implementation changes:

1. Read `AGENTS.md` completely.
2. Read `docs/architecture/overview.md`.
3. Read `docs/project-status.md`.
4. Run `git status --short --branch`.
5. Inspect every producer and consumer before changing a process contract.

Preserve these boundaries:

- MySQL is the durable source of truth.
- RabbitMQ delivery is at least once; operations are idempotent.
- Recording commands are routed to the node that owns the files.
- Generic workers compete on shared durable queues.
- Offline ASR remains inside media-worker.
- The control plane does not execute media algorithms.
- REST, SDK, CLI, MCP, and the console share application services and security.
- Runtime configuration is typed and allowlisted; bootstrap secrets stay in the
  deployment platform.

After material work, update `docs/project-status.md` and affected architecture
documentation.
