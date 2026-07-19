"""Local observability dashboard API.

Mounted at /api/plugins/local-observability/ by the Hermes dashboard.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
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


def _range_start(range_name: str = "24h") -> Optional[str]:
    value = (range_name or "24h").strip().lower()
    now = datetime.now(timezone.utc)
    if value in {"all", "全部"}:
        return None
    if value in {"1h", "hour", "最近1小时"}:
        return (now - timedelta(hours=1)).isoformat()
    if value in {"24h", "day", "1d", "最近24小时"}:
        return (now - timedelta(hours=24)).isoformat()
    if value in {"7d", "week", "最近7天"}:
        return (now - timedelta(days=7)).isoformat()
    return (now - timedelta(hours=24)).isoformat()


def _where_range(range_name: str, prefix: str = "WHERE") -> tuple[str, list[Any]]:
    start = _range_start(range_name)
    if not start:
        return "", []
    return f"{prefix} created_at >= ?", [start]


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


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(v) for v in value)
    return str(value)


def classify_failure(row: Dict[str, Any]) -> Dict[str, str]:
    text = " ".join(
        str(part or "")
        for part in (
            row.get("event_type"),
            row.get("name"),
            row.get("status"),
            _flatten_text(row.get("payload")),
        )
    ).lower()
    rules = [
        ("permission_error", "权限问题", ("permission denied", "eacces", "operation not permitted", "forbidden")),
        ("auth_error", "认证/API Key 问题", ("unauthorized", "401", "api key", "invalid token", "authentication")),
        ("rate_limit", "限流", ("rate limit", "429", "too many requests", "quota")),
        ("timeout", "超时", ("timeout", "timed out", "deadline exceeded")),
        ("network_error", "网络问题", ("network", "connection refused", "connection reset", "dns", "ssl", "tls")),
        ("not_found", "资源不存在", ("not found", "no such file", "enoent", "404")),
        ("agent_failed", "Agent 任务失败", ("task.failed", "task.interrupted", "agent_task")),
        ("tool_error", "工具执行错误", ("tool.completed", "tool.started")),
    ]
    for code, label, needles in rules:
        if any(needle in text for needle in needles):
            return {"code": code, "label": label}
    return {"code": "unknown", "label": "未分类"}


def _decorate_failure(row: Dict[str, Any]) -> Dict[str, Any]:
    row = _payload(row)
    row["failure_category"] = classify_failure(row)
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
        "failure_categories": [],
        "traces": [],
        "latency": [],
    }


def build_overview(range_name: str = "24h") -> Dict[str, Any]:
    conn = _connect()
    if conn is None:
        empty = _empty_overview()
        empty["range"] = range_name
        return empty
    try:
        where_sql, params = _where_range(range_name)
        totals_row = conn.execute(
            f"""
            SELECT COUNT(*) AS events,
                   COUNT(DISTINCT trace_id) AS traces,
                   SUM(CASE WHEN event_type = 'tool.completed' THEN 1 ELSE 0 END) AS tools,
                   SUM(CASE WHEN event_type = 'skill.used' THEN 1 ELSE 0 END) AS skills,
                   SUM(CASE WHEN status IN ('error', 'interrupted')
                             OR event_type IN ('task.failed', 'task.interrupted')
                            THEN 1 ELSE 0 END) AS failures,
                   ROUND(AVG(CASE WHEN event_type = 'llm.completed' THEN duration_ms END), 1) AS avg_llm_ms
            FROM events
            {where_sql}
            """,
            params,
        ).fetchone()
        totals = dict(totals_row) if totals_row else {}
        for key in ("events", "traces", "tools", "skills", "failures"):
            totals[key] = int(totals.get(key) or 0)

        event_types = _rows(
            conn,
            f"""
            SELECT event_type AS name, COUNT(*) AS count
            FROM events
            {where_sql}
            GROUP BY event_type
            ORDER BY count DESC, name ASC
            LIMIT 12
            """,
            params,
        )
        tools = _rows(
            conn,
            f"""
            SELECT name,
                   COUNT(*) AS count,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   ROUND(AVG(duration_ms), 1) AS avg_ms,
                   MAX(duration_ms) AS max_ms,
                   MAX(created_at) AS last_seen
            FROM events
            WHERE event_type = 'tool.completed'
            {"AND created_at >= ?" if params else ""}
            GROUP BY name
            ORDER BY count DESC, avg_ms DESC
            LIMIT 12
            """,
            params,
        )
        skills = _rows(
            conn,
            f"""
            SELECT name,
                   COUNT(*) AS count,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                   ROUND(AVG(duration_ms), 1) AS avg_ms,
                   MAX(created_at) AS last_seen
            FROM events
            WHERE event_type = 'skill.used'
            {"AND created_at >= ?" if params else ""}
            GROUP BY name
            ORDER BY count DESC, last_seen DESC
            LIMIT 12
            """,
            params,
        )
        failures = [
            _decorate_failure(row)
            for row in _rows(
                conn,
                f"""
                SELECT created_at, trace_id, task_id, session_id, event_type,
                       name, status, duration_ms, payload_json
                FROM events
                WHERE (
                    status IN ('error', 'interrupted')
                    OR event_type IN ('task.failed', 'task.interrupted')
                )
                {"AND created_at >= ?" if params else ""}
                ORDER BY created_at DESC
                LIMIT 10
                """,
                params,
            )
        ]
        failure_categories: Dict[str, Dict[str, Any]] = {}
        for failure in failures:
            category = failure.get("failure_category") or {"code": "unknown", "label": "未分类"}
            code = category["code"]
            row = failure_categories.setdefault(code, {"code": code, "label": category["label"], "count": 0})
            row["count"] += 1
        failure_category_rows = sorted(
            failure_categories.values(),
            key=lambda item: (-item["count"], item["label"]),
        )
        traces = _rows(
            conn,
            f"""
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
            {where_sql}
            GROUP BY trace_id
            ORDER BY last_seen DESC
            LIMIT 10
            """,
            params,
        )
        latency = _rows(
            conn,
            f"""
            SELECT substr(created_at, 1, 13) || ':00' AS bucket,
                   ROUND(AVG(CASE WHEN event_type = 'llm.completed' THEN duration_ms END), 1) AS llm_ms,
                   ROUND(AVG(CASE WHEN event_type = 'tool.completed' THEN duration_ms END), 1) AS tool_ms,
                   COUNT(*) AS events
            FROM events
            {where_sql}
            GROUP BY bucket
            ORDER BY bucket DESC
            LIMIT 24
            """,
            params,
        )
        latency.reverse()
        return {
            "range": range_name,
            "paths": {
                "jsonl": str(store.events_jsonl_path()),
                "sqlite": str(store.sqlite_path()),
            },
            "totals": totals,
            "event_types": event_types,
            "tools": tools,
            "skills": skills,
            "failures": failures,
            "failure_categories": failure_category_rows,
            "traces": traces,
            "latency": latency,
        }
    finally:
        conn.close()


@router.get("/overview")
async def overview(range: str = Query("24h")):
    return build_overview(range_name=range)


@router.get("/events")
async def events(limit: int = Query(100, ge=1, le=500), range: str = Query("24h")):
    conn = _connect()
    if conn is None:
        return {"events": []}
    try:
        where_sql, params = _where_range(range)
        rows = [
            _payload(row)
            for row in _rows(
                conn,
                f"""
                SELECT event_id, created_at, trace_id, task_id, session_id,
                       event_type, span_type, name, status, duration_ms,
                       model, provider, payload_json
                FROM events
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, _limit(limit, maximum=500)),
            )
        ]
        return {"events": rows}
    finally:
        conn.close()


def build_trace_detail(trace_id: str) -> Dict[str, Any]:
    conn = _connect()
    if conn is None:
        return {"trace_id": trace_id, "summary": {}, "events": []}
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
                WHERE trace_id = ?
                ORDER BY created_at ASC
                """,
                (trace_id,),
            )
        ]
        if not rows:
            return {"trace_id": trace_id, "summary": {}, "events": []}
        started_at = rows[0]["created_at"]
        ended_at = rows[-1]["created_at"]
        total_duration = sum(
            int(row["duration_ms"])
            for row in rows
            if row.get("duration_ms") is not None
        )
        failures = [
            row
            for row in rows
            if row.get("status") in {"error", "interrupted"}
            or row.get("event_type") in {"task.failed", "task.interrupted"}
        ]
        for failure in failures:
            failure["failure_category"] = classify_failure(failure)
        summary = {
            "trace_id": trace_id,
            "task_id": rows[-1].get("task_id") or rows[0].get("task_id") or "",
            "session_id": rows[-1].get("session_id") or rows[0].get("session_id") or "",
            "events": len(rows),
            "tools": sum(1 for row in rows if row.get("event_type") == "tool.completed"),
            "skills": sum(1 for row in rows if row.get("event_type") == "skill.used"),
            "errors": len(failures),
            "started_at": started_at,
            "ended_at": ended_at,
            "observed_duration_ms": total_duration,
        }
        return {"trace_id": trace_id, "summary": summary, "events": rows, "failures": failures}
    finally:
        conn.close()


@router.get("/traces/{trace_id}")
async def trace_detail(trace_id: str):
    return build_trace_detail(trace_id)


@router.get("/export")
async def export(limit: int = Query(1000, ge=1, le=5000), range: str = Query("24h")):
    path = store.export_events(
        limit=_limit(limit, default=1000, maximum=5000),
        started_after=_range_start(range),
    )
    return {"path": str(path)}
