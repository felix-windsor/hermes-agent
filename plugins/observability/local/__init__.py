"""local-observability — local Hermes trace/event collection.

This plugin complements hosted tracing backends such as Langfuse. It records
privacy-conscious local events for scenario analysis and optimization loops.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from . import store

logger = logging.getLogger(__name__)


def _record(**kwargs: Any) -> None:
    try:
        store.record_event(**kwargs)
    except Exception as exc:
        logger.debug("local observability event dropped: %s", exc, exc_info=True)


def _coerce_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return value
    return parsed


def _skill_name_from_view(tool_name: str, args: Any, result: Any) -> Optional[str]:
    if tool_name != "skill_view":
        return None
    if isinstance(result, str):
        parsed = _coerce_json(result)
    else:
        parsed = result
    if isinstance(parsed, dict) and parsed.get("success"):
        name = parsed.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    if isinstance(args, dict):
        name = args.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def on_pre_api_request(
    *,
    task_id: str = "",
    session_id: str = "",
    user_message: Any = None,
    platform: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_call_count: int = 0,
    message_count: int = 0,
    tool_count: int = 0,
    approx_input_tokens: int = 0,
    request_char_count: int = 0,
    max_tokens: Any = None,
    **_: Any,
) -> None:
    _record(
        event_type="llm.requested",
        task_id=task_id,
        session_id=session_id,
        span_type="llm",
        name=f"api_call_{api_call_count}",
        status="started",
        model=model,
        provider=provider,
        payload={
            "user_message": store.text_summary(user_message),
            "platform": platform,
            "base_url": base_url,
            "api_mode": api_mode,
            "api_call_count": api_call_count,
            "message_count": message_count,
            "tool_count": tool_count,
            "approx_input_tokens": approx_input_tokens,
            "request_char_count": request_char_count,
            "max_tokens": max_tokens,
        },
    )


def on_post_api_request(
    *,
    task_id: str = "",
    session_id: str = "",
    platform: str = "",
    model: str = "",
    provider: str = "",
    base_url: str = "",
    api_mode: str = "",
    api_call_count: int = 0,
    api_duration: float = 0.0,
    finish_reason: str = "",
    message_count: int = 0,
    response_model: str = "",
    usage: Any = None,
    assistant_content_chars: int = 0,
    assistant_tool_call_count: int = 0,
    **_: Any,
) -> None:
    _record(
        event_type="llm.completed",
        task_id=task_id,
        session_id=session_id,
        span_type="llm",
        name=f"api_call_{api_call_count}",
        status="success",
        duration_ms=int(api_duration * 1000) if api_duration else None,
        model=model,
        provider=provider,
        payload={
            "platform": platform,
            "base_url": base_url,
            "api_mode": api_mode,
            "api_call_count": api_call_count,
            "finish_reason": finish_reason,
            "message_count": message_count,
            "response_model": response_model,
            "usage": store.safe_value(usage),
            "assistant_content_chars": assistant_content_chars,
            "assistant_tool_call_count": assistant_tool_call_count,
        },
    )


def on_pre_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    **_: Any,
) -> None:
    _record(
        event_type="tool.started",
        task_id=task_id,
        session_id=session_id,
        span_type="tool",
        name=tool_name,
        status="started",
        payload={
            "tool_call_id": tool_call_id,
            "args": store.safe_value(args),
        },
    )


def on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int = 0,
    **_: Any,
) -> None:
    status = store.tool_result_status(result)
    _record(
        event_type="tool.completed",
        task_id=task_id,
        session_id=session_id,
        span_type="tool",
        name=tool_name,
        status=status,
        duration_ms=duration_ms,
        payload={
            "tool_call_id": tool_call_id,
            "args": store.safe_value(args),
            **store.result_summary(result),
        },
    )

    skill_name = _skill_name_from_view(tool_name, args, result)
    if skill_name:
        _record(
            event_type="skill.used",
            task_id=task_id,
            session_id=session_id,
            span_type="skill",
            name=skill_name,
            status=status,
            duration_ms=duration_ms,
            payload={
                "source": "skill_view",
                "file_path": args.get("file_path") if isinstance(args, dict) else None,
                "tool_call_id": tool_call_id,
            },
        )


def on_skill_used(
    *,
    skill_name: str = "",
    source: str = "",
    task_id: str = "",
    session_id: str = "",
    file_path: str = "",
    **_: Any,
) -> None:
    if not skill_name:
        return
    _record(
        event_type="skill.used",
        task_id=task_id,
        session_id=session_id,
        span_type="skill",
        name=skill_name,
        status="success",
        payload={
            "source": source or "unknown",
            "file_path": file_path or None,
        },
    )


def on_post_llm_call(
    *,
    task_id: str = "",
    session_id: str = "",
    user_message: Any = None,
    assistant_response: Any = None,
    model: str = "",
    platform: str = "",
    **_: Any,
) -> None:
    _record(
        event_type="turn.completed",
        task_id=task_id,
        session_id=session_id,
        span_type="turn",
        name="agent_turn",
        status="success",
        model=model,
        payload={
            "platform": platform,
            "user_message": store.text_summary(user_message),
            "assistant_response": store.text_summary(assistant_response),
        },
    )


def on_session_end(
    *,
    task_id: str = "",
    session_id: str = "",
    completed: bool = True,
    interrupted: bool = False,
    failed: bool = False,
    model: str = "",
    platform: str = "",
    final_response_chars: int = 0,
    api_call_count: int = 0,
    **_: Any,
) -> None:
    if interrupted:
        event_type = "task.interrupted"
        status = "interrupted"
    elif failed or not completed:
        event_type = "task.failed"
        status = "error"
    else:
        event_type = "task.completed"
        status = "success"
    _record(
        event_type=event_type,
        task_id=task_id,
        session_id=session_id,
        span_type="task",
        name="agent_task",
        status=status,
        model=model,
        payload={
            "platform": platform,
            "completed": completed,
            "interrupted": interrupted,
            "failed": failed,
            "final_response_chars": final_response_chars,
            "api_call_count": api_call_count,
        },
    )


def _handle_observe(raw_args: str) -> str:
    argv = raw_args.strip().split()
    sub = argv[0] if argv else "summary"
    limit = 20
    if len(argv) > 1:
        try:
            limit = int(argv[1])
        except ValueError:
            limit = 20
    if sub in {"summary", "status"}:
        return store.format_summary()
    if sub == "failures":
        return store.format_failures(limit=limit)
    if sub == "skills":
        return store.format_skills(limit=limit)
    if sub == "traces":
        return store.format_traces(limit=limit)
    if sub == "export":
        try:
            path = store.export_events(limit=limit if len(argv) > 1 else 1000)
        except FileNotFoundError as exc:
            return str(exc)
        return f"Exported local observability events to {path}"
    if sub == "path":
        return f"JSONL: {store.events_jsonl_path()}\nSQLite: {store.sqlite_path()}"
    if sub in {"help", "-h", "--help"}:
        return (
            "/observe summary  Show local observability aggregates\n"
            "/observe failures [limit]  Show recent failed/interrupted events\n"
            "/observe skills [limit]    Show skill usage aggregates\n"
            "/observe traces [limit]    Show recent trace aggregates\n"
            "/observe export [limit]    Export recent events to JSON\n"
            "/observe path              Show local event store paths"
        )
    return "Unknown /observe subcommand. Try /observe help."


def register(ctx) -> None:
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("post_llm_call", on_post_llm_call)
    ctx.register_hook("on_session_end", on_session_end)
    ctx.register_hook("skill_used", on_skill_used)
    ctx.register_command(
        "observe",
        handler=_handle_observe,
        description="Show local Hermes observability summaries.",
    )
