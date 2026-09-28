# Roadmap

## Public baseline

- Remove environment-specific code and examples.
- Replace the historical migration chain with a clean schema baseline.
- Stabilize service names, configuration, and Docker images.
- Add automated secret, dependency, and license checks.

## Agent-facing interfaces

- Publish a typed Python SDK.
- Add the `mediafleet` CLI with JSON output and stable exit codes.
- Add a separately deployable MCP server backed by the SDK.
- Define scopes and audit rules for mutating CLI/MCP operations.

## Operations console

- Expand node, task, recording, and artifact visibility.
- Add drain and maintenance workflows.
- Introduce typed, versioned runtime configuration with rollout and rollback.
- Keep bootstrap secrets in the deployment platform.

## Reliability

- Complete recorder execution generations and long-running leases.
- Persist post-processing stage checkpoints and notification compensation.
- Add multi-control-plane failure injection and repeatable capacity tests.
