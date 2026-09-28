# Architecture overview

MediaFleet separates control-plane coordination from node-local media work.

## Components

### Control center

The control center exposes APIs, stores durable task and binding facts, selects
eligible nodes, and reliably publishes work. Multiple instances can serve any
request without creator-instance affinity.

It does not execute FFmpeg, inspect recorder-local files, or load inference
models.

### Recorder node

A recorder node is paired with one ZLMediaKit instance and its recording
storage. It consumes commands addressed to its stable node identity, processes
local fragments, uploads artifacts, and writes results back to MySQL.

Historical file ownership never changes merely because a stream is rebound.

### Media worker

Media workers compete on one shared durable queue. Every worker attached to
that queue must provide equivalent enabled capabilities. Offline ASR remains a
worker capability and scales by adding suitable CPU or GPU instances.

### Optional analysis services

Content and image analysis are independent compute pools with their own shared
queues and recovery checkpoints. They do not become part of a recording unit.

## Consistency model

- MySQL is authoritative for nodes, server intent, stream bindings, tasks,
  dispatch state, leases, and artifacts.
- RabbitMQ provides reliable transport using at-least-once delivery.
- Consumers use stable message identifiers, conditional state transitions,
  execution generations, and leases to reject duplicates and stale writers.
- Redis and process memory are optional caches or coordination aids only.

## Stream ownership

Clients keep a technical `stream_id` distinct from a display name. The control
plane persists sticky bindings and returns the selected media-node endpoint.
Camera pull-proxy creation and shutdown remain the responsibility of the RTC or
client integration after it receives the binding.

Recording commands always follow the current binding to the recorder node that
owns the stream and local files.

## Dependency direction

```text
services/*
    -> media_platform/application
    -> media_platform/domain + media_platform/contracts
    -> media_platform/infrastructure adapters
```

Service-private orchestration stays inside its service. Shared packages expose
domain concepts, process contracts, and table-level infrastructure rather than
service-specific business verbs.
