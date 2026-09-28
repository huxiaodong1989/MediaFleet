# REST, CLI, and MCP interfaces

MediaFleet uses one application layer and multiple transport adapters.

```text
Application services
    |-- REST/OpenAPI
    |-- Python SDK
    |-- MediaFleet CLI
    |-- MediaFleet MCP server
    `-- Operations console
```

REST remains the stable remote contract. The SDK provides typed clients for
REST operations. CLI, MCP, and the operations console should use the SDK so
authentication, authorization, auditing, idempotency, and error semantics stay
consistent.

## CLI requirements

The planned `mediafleet` command should provide deterministic exit codes,
machine-readable `--json` output, non-interactive operation, and commands for
health checks, nodes, bindings, tasks, recordings, artifacts, and allowlisted
runtime configuration.

Credentials must come from environment variables, protected configuration
files, or an operating-system credential store, never required command-line
arguments that can appear in process lists.

## MCP requirements

The MCP server should expose small, explicit tools backed by the SDK. Read-only
tools are enabled by default. Mutating tools require scoped authorization,
stable idempotency keys, and audit records.

MCP tools must not provide arbitrary SQL, shell execution, filesystem access,
RabbitMQ publishing, or environment-variable mutation.
