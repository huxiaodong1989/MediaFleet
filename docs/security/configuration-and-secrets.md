# Configuration and secrets

MediaFleet separates bootstrap configuration from runtime configuration.

## Bootstrap configuration

Database, RabbitMQ, object-storage, ZLMediaKit, and encryption credentials are
supplied by the deployment platform or a secret manager. They are not editable
through the operations console and generally require a restart to change.

## Runtime configuration

Concurrency limits, capacity thresholds, retry budgets, scheduling weights,
and drain state may later be managed through a typed configuration registry.

Each definition should include a stable key, owner, scope, data type, default,
validation constraints, sensitivity, reload behavior, version, author, and
audit history.

The API exposes only allowlisted definitions. It never provides a generic
endpoint that changes arbitrary environment variables. Sensitive settings are
stored as encrypted values or secret references and are never returned after
creation. Changes should support validation, preview, staged rollout, rollback,
and per-node acknowledgement.

## Logging

Logs must redact URL user information and sensitive query parameters such as
`token`, `signature`, `secret`, `key`, and temporary storage signatures.
Credential-bearing RTSP URLs must never be logged.
