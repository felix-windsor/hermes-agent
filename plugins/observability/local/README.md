# Local Observability Plugin

This plugin turns Hermes Agent runs into a local observability dataset and a
dashboard that can be used to explain, debug, and improve agent behavior.

This bundled opt-in plugin records Hermes runtime events locally for scenario
analysis and optimization loops.

## What It Shows

The dashboard answers four practical questions:

- Did the agent run successfully?
- Which tools and skills did it rely on?
- Where did latency or failures happen?
- Can a single trace be replayed as a timeline?

The current dashboard includes:

- Time range filters: 1 hour, 24 hours, 7 days, all time.
- KPI cards for events, traces, tool calls, skill usage, failures, and LLM
  latency.
- Event type and failure reason distributions.
- Tool performance and skill usage tables.
- Clickable trace detail with a chronological timeline.
- Recent failure and event tables.
- JSON export for the selected time range.

## Enable

```bash
hermes plugins enable observability/local
```

Events are written to:

```text
~/.hermes/observability/events.jsonl
~/.hermes/observability/observability.sqlite
```

Use `/observe summary` inside a Hermes session to see local aggregates.

## Data Model

Each event is stored once as JSONL for easy export and once in SQLite for
dashboard queries.

Core fields:

```text
event_id
created_at
trace_id
task_id
session_id
event_type
span_type
name
status
duration_ms
model
provider
payload
```

Tracked event types:

```text
llm.requested
llm.completed
tool.started
tool.completed
skill.used
turn.completed
task.completed
task.failed
task.interrupted
```

## Analysis Loop

This plugin is intentionally small, but it supports a complete optimization
loop:

```text
collect runtime events
-> inspect aggregate health
-> open a failed or slow trace
-> identify tool, skill, prompt, or permission issue
-> change the agent behavior
-> compare the next run with the same dashboard
```

Failure categories are rule-based in the first version:

```text
permission_error
auth_error
rate_limit
timeout
network_error
not_found
agent_failed
tool_error
unknown
```

This keeps the analysis deterministic and easy to explain before adding
LLM-based clustering or eval scoring.

## Privacy

By default, text content is summarized as length + SHA-256 hash. To include
redacted/truncated previews for local debugging:

```bash
HERMES_OBSERVABILITY_CAPTURE_CONTENT=true
HERMES_OBSERVABILITY_MAX_CHARS=2000
```

You can override the storage directory with:

```bash
HERMES_OBSERVABILITY_DIR=/path/to/observability
```

## Dashboard API

The dashboard plugin exposes these local API routes:

```text
GET /api/plugins/local-observability/overview?range=24h
GET /api/plugins/local-observability/events?range=24h&limit=30
GET /api/plugins/local-observability/traces/{trace_id}
GET /api/plugins/local-observability/export?range=24h&limit=1000
```

Supported ranges are `1h`, `24h`, `7d`, and `all`.
