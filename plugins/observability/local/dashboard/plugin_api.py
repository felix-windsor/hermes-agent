"""Local observability dashboard API.

Mounted at /api/plugins/local-observability/ by the Hermes dashboard.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from fastapi import APIRouter, Query
except Exception:  # Allows unit tests without dashboard extras.
    class APIRouter:  # type: ignore
        def get(self, *_args, **_kwargs):
            return lambda fn: fn
    def Query(default=None, **_kwargs):  # type: ignore
        return default

from plugins.observability.local import store

router = APIRouter()


def _limit(value: int, default: int = 50, maximum: int = 500) -> int:
    try:
        return min(maximum, max(1, int(value)))
    except (TypeError, ValueError):
        return default


def _connect() -> Optional[sqlite3.Connection]:
    db = store.sqlite_path()
    if not db.exists():
        return None
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params))]


def _payload(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.pop("payload_json", "{}")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {}
    row["payload"] = parsed if isinstance(parsed, dict) else {"value": parsed}
    return row


def _empty_overview() -> Dict[str, Any]:
    return {
        "paths": {
            "jsonl": str(store.events_jsonl_path()),
            "sqlite": str(store.sqlite_path()),
        },
        "totals": {
            "events": 0,
            "traces": 0,
            "tools": 0,
            "skills": 0,
            "failures": 0,
            "avg_llm_ms": None,
        },
        "event_types": [],
        "tools": [],
        "skills": [],
        "failures": [],
        "traces": [],
        "latency": [],
    }


def build_overview() -> Dict[str, Any]:
    conn = _connect()
    if conn is None:
        return _empty_overview()
    try:
        totals_row = conn.execute(
            """
            SELECT COUNT(*) AS events,
                   COUNT(DISTINCT trace_id) AS traces,
                   SUM(CASE WHEN event_type = 'tool.completed' THEN 1 ELSE 0 END) AS tools,
                   SUM(CASE WHEN event_type = 'skill.used' THEN 1 ELSE 0 END) AS skills,
                   SUM(CASE WHEN status IN ('error', 'interrupted')
                             OR event_type IN ('task.failed', 'task.interrupted')
                            THEN 1 ELSE 0 END) AS failures,
                   ROUND(AVG(CASE WHEN event_type = 'llm.completed' THEN duration_ms END), 1) AS avg_llm_ms
            FROM events
            """
        ).fetchone()
        totals = dict(totals_row) if totals_row else {}
        for key in ("events", "traces", "tools", "skills", "failures"):
            totals[key] = int(totals.get(key) or 0)

        event_types = _rows(
            conn,
            """
            SELECT event_type AS name, COUNT(*) AS count
            FROM events
            GROUP BY event_type
            ORDER BY count DESC, name ASC
            LIMIT 12
            """,
        )
        tools = _rows(
            conn,
            """
            SELECT name,
                   COUNT(*) AS count,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   ROUND(AVG(duration_ms), 1) AS avg_ms,
                   MAX(duration_ms) AS max_ms,
                   MAX(created_at) AS last_seen
            FROM events
            WHERE event_type = 'tool.completed'
            GROUP BY name
            ORDER BY count DESC, avg_ms DESC
            LIMIT 12
            """,
        )
        skills = _rows(
            conn,
            """
            SELECT name,
                   COUNT(*) AS count,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   ROUND(AVG(duration_ms), 1) AS avg_ms,
                   MAX(created_at) AS last_seen
            FROM events
            WHERE event_type = 'skill.used'
            GROUP BY name
            ORDER BY count DESC, last_seen DESC
            LIMIT 12
            """,
        )
        failures = [
            _payload(row)
            for row in _rows(
                conn,
                """
                SELECT created_at, trace_id, task_id, session_id, event_type,
                       name, status, duration_ms, payload_json
                FROM events
                WHERE status IN ('error', 'interrupted')
                   OR event_type IN ('task.failed', 'task.interrupted')
                ORDER BY created_at DESC
                LIMIT 10
                """,
            )
        ]
        traces = _rows(
            conn,
            """
            SELECT trace_id,
                   MIN(created_at) AS started_at,
                   MAX(created_at) AS last_seen,
                   COUNT(*) AS events,
                   SUM(CASE WHEN event_type = 'tool.completed' THEN 1 ELSE 0 END) AS tools,
                   SUM(CASE WHEN event_type = 'skill.used' THEN 1 ELSE 0 END) AS skills,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   MAX(task_id) AS task_id,
                   MAX(session_id) AS session_id
            FROM events
            GROUP BY trace_id
            ORDER BY last_seen DESC
            LIMIT 10
            """,
        )
        latency = _rows(
            conn,
            """
            SELECT substr(created_at, 1, 13) || ':00' AS bucket,
                   ROUND(AVG(CASE WHEN event_type = 'llm.completed' THEN duration_ms END), 1) AS llm_ms,
                   ROUND(AVG(CASE WHEN event_type = 'tool.completed' THEN duration_ms END), 1) AS tool_ms,
                   COUNT(*) AS events
            FROM events
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT 24
            """,
        )
        latency.reverse()
        return {
            "paths": {
                "jsonl": str(store.events_jsonl_path()),
                "sqlite": str(store.sqlite_path()),
            },
            "totals": totals,
            "event_types": event_types,
            "tools": tools,
            "skills": skills,
            "failures": failures,
            "traces": traces,
            "latency": latency,
        }
    finally:
        conn.close()


@router.get("/overview")
async def overview():
    return build_overview()


@router.get("/events")
async def events(limit: int = Query(100, ge=1, le=500)):
    conn = _connect()
    if conn is None:
        return {"events": []}
    try:
        rows = [
            _payload(row)
            for row in _rows(
                conn,
                """
                SELECT event_id, created_at, trace_id, task_id, session_id,
                       event_type, span_type, name, status, duration_ms,
                       model, provider, payload_json
                FROM events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (_limit(limit, maximum=500),),
            )
        ]
        return {"events": rows}
    finally:
        conn.close()


@router.get("/export")
async def export(limit: int = Query(1000, ge=1, le=5000)):
    path = store.export_events(limit=_limit(limit, default=1000, maximum=5000))
    return {"path": str(path)}
