"""Local event store for Hermes observability.

The store is intentionally small and dependency-free: every event is appended
to JSONL for easy export, then inserted into SQLite for local summaries.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from hermes_constants import get_hermes_home

_LOCK = threading.RLock()
_SCHEMA_READY_FOR: set[str] = set()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def observability_dir() -> Path:
    override = _env("HERMES_OBSERVABILITY_DIR")
    if override:
        return Path(override).expanduser()
    return get_hermes_home() / "observability"


def events_jsonl_path() -> Path:
    return observability_dir() / "events.jsonl"


def sqlite_path() -> Path:
    return observability_dir() / "observability.sqlite"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def trace_id_for(task_id: str = "", session_id: str = "") -> str:
    seed = task_id or session_id or "default"
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:32]


def content_capture_enabled() -> bool:
    return _env_bool("HERMES_OBSERVABILITY_CAPTURE_CONTENT", False)


def max_chars() -> int:
    try:
        return max(128, int(_env("HERMES_OBSERVABILITY_MAX_CHARS", "2000") or "2000"))
    except ValueError:
        return 2000


def _redact_text(value: str) -> str:
    try:
        from agent.redact import redact_sensitive_text
        return redact_sensitive_text(value)
    except Exception:
        return value


def _truncate(value: str) -> str:
    limit = max_chars()
    value = _redact_text(value)
    if len(value) <= limit:
        return value
    return value[:limit] + f"... [truncated {len(value) - limit} chars]"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def text_summary(value: Any) -> Dict[str, Any]:
    text = "" if value is None else str(value)
    summary: Dict[str, Any] = {
        "chars": len(text),
        "sha256": _hash_text(text),
    }
    if content_capture_enabled() and text:
        summary["preview"] = _truncate(text)
    return summary


def _parse_json_string(value: str) -> Any:
    stripped = value.strip()
    if len(stripped) < 2 or stripped[0] not in "{[":
        return value
    try:
        parsed = json.loads(stripped)
    except Exception:
        return value
    return parsed if isinstance(parsed, (dict, list)) else value


def safe_value(value: Any, *, depth: int = 0, parse_json_strings: bool = False) -> Any:
    if depth > 4:
        return "<max-depth>"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "bytes": len(value)}
    if isinstance(value, str):
        if parse_json_strings:
            parsed = _parse_json_string(value)
            if parsed is not value:
                return safe_value(parsed, depth=depth, parse_json_strings=True)
        return _truncate(value) if content_capture_enabled() else text_summary(value)
    if isinstance(value, dict):
        return {
            str(k): safe_value(v, depth=depth + 1, parse_json_strings=parse_json_strings)
            for k, v in list(value.items())[:80]
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [
            safe_value(item, depth=depth + 1, parse_json_strings=parse_json_strings)
            for item in items[:80]
        ]
        if len(items) > 80:
            result.append({"omitted_items": len(items) - 80})
        return result
    return text_summary(repr(value))


def tool_result_status(result: Any) -> str:
    parsed = _parse_json_string(result) if isinstance(result, str) else result
    if isinstance(parsed, dict):
        if parsed.get("success") is False or parsed.get("error"):
            return "error"
    if isinstance(result, str) and result.lower().startswith(("error", "[tool_error]")):
        return "error"
    return "success"


def result_summary(result: Any) -> Dict[str, Any]:
    parsed = _parse_json_string(result) if isinstance(result, str) else result
    summary: Dict[str, Any] = {"status": tool_result_status(result)}
    if isinstance(result, str):
        summary["result"] = safe_value(result, parse_json_strings=True)
    else:
        summary["result"] = safe_value(parsed, parse_json_strings=True)
    return summary


def _json_default(value: Any) -> Any:
    return text_summary(repr(value))


def normalize_event(
    *,
    event_type: str,
    task_id: str = "",
    session_id: str = "",
    span_type: str = "",
    name: str = "",
    status: str = "",
    duration_ms: Optional[int] = None,
    model: str = "",
    provider: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trace_id = trace_id_for(task_id, session_id)
    return {
        "event_id": str(uuid.uuid4()),
        "created_at": now_iso(),
        "trace_id": trace_id,
        "task_id": task_id or "",
        "session_id": session_id or "",
        "event_type": event_type,
        "span_type": span_type or "",
        "name": name or "",
        "status": status or "",
        "duration_ms": duration_ms,
        "model": model or "",
        "provider": provider or "",
        "payload": payload or {},
    }


def _ensure_schema(conn: sqlite3.Connection) -> None:
    db_key = str(sqlite_path())
    if db_key in _SCHEMA_READY_FOR:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            trace_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            span_type TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_trace_id ON events(trace_id);
        CREATE INDEX IF NOT EXISTS idx_events_type_name ON events(event_type, name);
        CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
        """
    )
    _SCHEMA_READY_FOR.add(db_key)


def record_event(**kwargs: Any) -> Dict[str, Any]:
    event = normalize_event(**kwargs)
    base = observability_dir()
    base.mkdir(parents=True, exist_ok=True)
    json_line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=_json_default)
    payload_json = json.dumps(
        event["payload"],
        ensure_ascii=False,
        sort_keys=True,
        default=_json_default,
    )

    with _LOCK:
        with events_jsonl_path().open("a", encoding="utf-8") as fh:
            fh.write(json_line + "\n")
        with sqlite3.connect(sqlite_path(), timeout=10) as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO events (
                    event_id, created_at, trace_id, task_id, session_id,
                    event_type, span_type, name, status, duration_ms,
                    model, provider, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["created_at"],
                    event["trace_id"],
                    event["task_id"],
                    event["session_id"],
                    event["event_type"],
                    event["span_type"],
                    event["name"],
                    event["status"],
                    event["duration_ms"],
                    event["model"],
                    event["provider"],
                    payload_json,
                ),
            )
    return event


def _connect_readonly() -> Optional[sqlite3.Connection]:
    db = sqlite_path()
    if not db.exists():
        return None
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, tuple(params)))


def _clamp_limit(limit: int = 8) -> int:
    try:
        return min(200, max(1, int(limit)))
    except (TypeError, ValueError):
        return 8


def format_summary(limit: int = 8) -> str:
    limit = _clamp_limit(limit)
    conn = _connect_readonly()
    if conn is None:
        return "No local observability events yet."
    try:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        traces = conn.execute("SELECT COUNT(DISTINCT trace_id) FROM events").fetchone()[0]
        lines = [
            f"Local observability: {total} event(s), {traces} trace(s)",
            f"JSONL: {events_jsonl_path()}",
            f"SQLite: {sqlite_path()}",
            "",
            "Top event types:",
        ]
        for row in _rows(
            conn,
            "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type ORDER BY n DESC LIMIT ?",
            (limit,),
        ):
            lines.append(f"  {row['event_type']}: {row['n']}")

        lines.append("")
        lines.append("Slowest tools:")
        for row in _rows(
            conn,
            """
            SELECT name, COUNT(*) AS n, ROUND(AVG(duration_ms), 1) AS avg_ms,
                   MAX(duration_ms) AS max_ms
            FROM events
            WHERE event_type = 'tool.completed' AND duration_ms IS NOT NULL
            GROUP BY name
            ORDER BY avg_ms DESC
            LIMIT ?
            """,
            (limit,),
        ):
            lines.append(f"  {row['name']}: avg {row['avg_ms']} ms, max {row['max_ms']} ms, n={row['n']}")

        lines.append("")
        lines.append("Tool failures:")
        for row in _rows(
            conn,
            """
            SELECT name, COUNT(*) AS n
            FROM events
            WHERE event_type = 'tool.completed' AND status = 'error'
            GROUP BY name
            ORDER BY n DESC
            LIMIT ?
            """,
            (limit,),
        ):
            lines.append(f"  {row['name']}: {row['n']}")

        lines.append("")
        lines.append("Skills:")
        for row in _rows(
            conn,
            """
            SELECT name, COUNT(*) AS n
            FROM events
            WHERE event_type = 'skill.used'
            GROUP BY name
            ORDER BY n DESC
            LIMIT ?
            """,
            (limit,),
        ):
            lines.append(f"  {row['name']}: {row['n']}")
        return "\n".join(lines)
    finally:
        conn.close()


def format_failures(limit: int = 20) -> str:
    limit = _clamp_limit(limit)
    conn = _connect_readonly()
    if conn is None:
        return "No local observability events yet."
    try:
        rows = _rows(
            conn,
            """
            SELECT created_at, trace_id, task_id, session_id, event_type, name,
                   status, duration_ms, payload_json
            FROM events
            WHERE status IN ('error', 'interrupted')
               OR event_type IN ('task.failed', 'task.interrupted')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        if not rows:
            return "No failures recorded."

        lines = [f"Recent failures ({len(rows)}):"]
        for row in rows:
            detail = ""
            try:
                payload = json.loads(row["payload_json"])
                result = payload.get("result") if isinstance(payload, dict) else None
                if isinstance(result, dict):
                    error = result.get("error")
                    if isinstance(error, dict):
                        detail = error.get("sha256") or ""
                    elif error:
                        detail = str(error)
                elif isinstance(payload, dict) and payload.get("failed"):
                    detail = "task marked failed"
            except Exception:
                detail = ""
            suffix = f" - {detail}" if detail else ""
            lines.append(
                f"  {row['created_at']} {row['event_type']} {row['name']} "
                f"status={row['status']} trace={row['trace_id'][:8]}{suffix}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


def format_skills(limit: int = 20) -> str:
    limit = _clamp_limit(limit)
    conn = _connect_readonly()
    if conn is None:
        return "No local observability events yet."
    try:
        rows = _rows(
            conn,
            """
            SELECT name,
                   COUNT(*) AS n,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   MIN(created_at) AS first_seen,
                   MAX(created_at) AS last_seen,
                   ROUND(AVG(duration_ms), 1) AS avg_ms
            FROM events
            WHERE event_type = 'skill.used'
            GROUP BY name
            ORDER BY n DESC, last_seen DESC
            LIMIT ?
            """,
            (limit,),
        )
        if not rows:
            return "No skill usage events recorded."

        lines = [f"Skill usage ({len(rows)}):"]
        for row in rows:
            avg = row["avg_ms"]
            avg_part = f", avg {avg} ms" if avg is not None else ""
            lines.append(
                f"  {row['name']}: n={row['n']}, errors={row['errors'] or 0}"
                f"{avg_part}, last={row['last_seen']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


def format_traces(limit: int = 12) -> str:
    limit = _clamp_limit(limit)
    conn = _connect_readonly()
    if conn is None:
        return "No local observability events yet."
    try:
        rows = _rows(
            conn,
            """
            SELECT trace_id,
                   MIN(created_at) AS started_at,
                   MAX(created_at) AS last_seen,
                   COUNT(*) AS events,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   SUM(CASE WHEN event_type = 'tool.completed' THEN 1 ELSE 0 END) AS tools,
                   SUM(CASE WHEN event_type = 'skill.used' THEN 1 ELSE 0 END) AS skills,
                   MAX(task_id) AS task_id,
                   MAX(session_id) AS session_id
            FROM events
            GROUP BY trace_id
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (limit,),
        )
        if not rows:
            return "No traces recorded."

        lines = [f"Recent traces ({len(rows)}):"]
        for row in rows:
            ident = row["task_id"] or row["session_id"] or row["trace_id"][:12]
            lines.append(
                f"  {row['last_seen']} trace={row['trace_id'][:12]} id={ident} "
                f"events={row['events']} tools={row['tools'] or 0} "
                f"skills={row['skills'] or 0} errors={row['errors'] or 0}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


def export_events(limit: int = 1000) -> Path:
    limit = _clamp_limit(limit)
    conn = _connect_readonly()
    if conn is None:
        raise FileNotFoundError("No local observability events yet.")
    try:
        rows = _rows(
            conn,
            """
            SELECT event_id, created_at, trace_id, task_id, session_id,
                   event_type, span_type, name, status, duration_ms,
                   model, provider, payload_json
            FROM events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        events = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                payload = {}
            events.append({
                "event_id": row["event_id"],
                "created_at": row["created_at"],
                "trace_id": row["trace_id"],
                "task_id": row["task_id"],
                "session_id": row["session_id"],
                "event_type": row["event_type"],
                "span_type": row["span_type"],
                "name": row["name"],
                "status": row["status"],
                "duration_ms": row["duration_ms"],
                "model": row["model"],
                "provider": row["provider"],
                "payload": payload,
            })
    finally:
        conn.close()

    out = observability_dir() / f"export-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return out
