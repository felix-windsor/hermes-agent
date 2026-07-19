# Local Observability Plugin

This bundled opt-in plugin records Hermes runtime events locally for scenario
analysis and optimization loops.

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
