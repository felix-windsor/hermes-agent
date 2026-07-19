"""Tests for the bundled observability/local plugin."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "observability" / "local"
DASHBOARD_API = PLUGIN_DIR / "dashboard" / "plugin_api.py"


def fresh_plugin(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for mod_name in (
        "plugins.observability.local",
        "plugins.observability.local.store",
    ):
        sys.modules.pop(mod_name, None)
    return importlib.import_module("plugins.observability.local")


class TestManifest:
    def test_plugin_directory_exists(self):
        assert PLUGIN_DIR.is_dir()
        assert (PLUGIN_DIR / "plugin.yaml").exists()
        assert (PLUGIN_DIR / "__init__.py").exists()
        assert (PLUGIN_DIR / "store.py").exists()

    def test_manifest_fields(self):
        data = yaml.safe_load((PLUGIN_DIR / "plugin.yaml").read_text())
        assert data["name"] == "local-observability"
        assert set(data["hooks"]) == {
            "pre_api_request",
            "post_api_request",
            "pre_tool_call",
            "post_tool_call",
            "post_llm_call",
            "on_session_end",
            "skill_used",
        }

    def test_dashboard_manifest_fields(self):
        data = yaml.safe_load((PLUGIN_DIR / "dashboard" / "manifest.json").read_text())
        assert data["name"] == "local-observability"
        assert data["tab"]["path"] == "/observability"
        assert data["entry"] == "dist/index.js"
        assert data["css"] == "dist/style.css"
        assert data["api"] == "plugin_api.py"


class TestDiscovery:
    def test_plugin_is_discovered_as_standalone_opt_in(self, tmp_path, monkeypatch):
        from hermes_cli import plugins as plugins_mod

        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        manager = plugins_mod.PluginManager()
        manager.discover_and_load()

        loaded = manager._plugins.get("observability/local")
        assert loaded is not None
        assert loaded.enabled is False
        assert "not enabled" in (loaded.error or "").lower()


class TestEventStore:
    def test_hooks_write_jsonl_and_sqlite(self, tmp_path, monkeypatch):
        mod = fresh_plugin(monkeypatch, tmp_path)

        mod.on_pre_api_request(
            task_id="task-1",
            session_id="session-1",
            user_message="hello secret sk-test1234567890",
            model="m",
            provider="p",
            api_call_count=1,
            message_count=2,
        )
        mod.on_post_tool_call(
            tool_name="terminal",
            args={"command": "echo hi"},
            result='{"success": true, "output": "hi"}',
            task_id="task-1",
            session_id="session-1",
            duration_ms=42,
        )
        mod.on_session_end(
            task_id="task-1",
            session_id="session-1",
            completed=True,
            interrupted=False,
            failed=False,
            model="m",
            api_call_count=1,
        )

        jsonl = mod.store.events_jsonl_path()
        assert jsonl.exists()
        events = [json.loads(line) for line in jsonl.read_text().splitlines()]
        assert [event["event_type"] for event in events] == [
            "llm.requested",
            "tool.completed",
            "task.completed",
        ]
        assert events[0]["payload"]["user_message"]["chars"] > 0
        assert "preview" not in events[0]["payload"]["user_message"]

        conn = sqlite3.connect(mod.store.sqlite_path())
        try:
            count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            assert count == 3
        finally:
            conn.close()

    def test_skill_view_records_skill_used(self, tmp_path, monkeypatch):
        mod = fresh_plugin(monkeypatch, tmp_path)

        mod.on_post_tool_call(
            tool_name="skill_view",
            args={"name": "writer"},
            result='{"success": true, "name": "writer"}',
            task_id="task-2",
            session_id="session-2",
            duration_ms=7,
        )

        events = [
            json.loads(line)
            for line in mod.store.events_jsonl_path().read_text().splitlines()
        ]
        assert [event["event_type"] for event in events] == [
            "tool.completed",
            "skill.used",
        ]
        assert events[1]["name"] == "writer"
        assert events[1]["payload"]["source"] == "skill_view"

    def test_summary_reports_failures_and_skills(self, tmp_path, monkeypatch):
        mod = fresh_plugin(monkeypatch, tmp_path)

        mod.on_post_tool_call(
            tool_name="web_search",
            args={"query": "x"},
            result='{"error": "boom"}',
            task_id="task-3",
            duration_ms=10,
        )
        mod.on_skill_used(skill_name="researcher", source="preload", task_id="task-3")

        summary = mod.store.format_summary()
        assert "tool.completed" in summary
        assert "web_search: 1" in summary
        assert "researcher: 1" in summary

        failures = mod.store.format_failures()
        assert "Recent failures" in failures
        assert "web_search" in failures

        skills = mod.store.format_skills()
        assert "Skill usage" in skills
        assert "researcher: n=1" in skills

        traces = mod.store.format_traces()
        assert "Recent traces" in traces
        assert "errors=1" in traces

    def test_observe_command_export_writes_json(self, tmp_path, monkeypatch):
        mod = fresh_plugin(monkeypatch, tmp_path)

        mod.on_session_end(task_id="task-4", session_id="session-4", completed=True)
        result = mod._handle_observe("export 10")

        assert "Exported local observability events" in result
        path = Path(result.rsplit(" ", 1)[-1])
        assert path.exists()
        exported = json.loads(path.read_text())
        assert exported[0]["event_type"] == "task.completed"

    def test_observe_command_help_lists_analysis_commands(self, tmp_path, monkeypatch):
        mod = fresh_plugin(monkeypatch, tmp_path)

        help_text = mod._handle_observe("help")

        assert "/observe failures" in help_text
        assert "/observe skills" in help_text
        assert "/observe traces" in help_text
        assert "/observe export" in help_text


class TestDashboardApi:
    def _fresh_dashboard_api(self, monkeypatch, tmp_path):
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        for mod_name in (
            "plugins.observability.local",
            "plugins.observability.local.store",
            "local_observability_dashboard_api_test",
        ):
            sys.modules.pop(mod_name, None)
        spec = importlib.util.spec_from_file_location(
            "local_observability_dashboard_api_test",
            DASHBOARD_API,
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_dashboard_overview_empty(self, tmp_path, monkeypatch):
        api = self._fresh_dashboard_api(monkeypatch, tmp_path)

        overview = api.build_overview()

        assert overview["totals"]["events"] == 0
        assert overview["paths"]["sqlite"].endswith("observability.sqlite")

    def test_dashboard_overview_aggregates_local_events(self, tmp_path, monkeypatch):
        api = self._fresh_dashboard_api(monkeypatch, tmp_path)
        store = importlib.import_module("plugins.observability.local.store")

        store.record_event(
            event_type="tool.completed",
            task_id="dash-task",
            span_type="tool",
            name="terminal",
            status="success",
            duration_ms=40,
            payload={"ok": True},
        )
        store.record_event(
            event_type="skill.used",
            task_id="dash-task",
            span_type="skill",
            name="writer",
            status="success",
            duration_ms=5,
            payload={"source": "test"},
        )
        store.record_event(
            event_type="tool.completed",
            task_id="dash-task",
            span_type="tool",
            name="web_search",
            status="error",
            duration_ms=90,
            payload={"error": "boom"},
        )

        overview = api.build_overview()

        assert overview["totals"]["events"] == 3
        assert overview["totals"]["tools"] == 2
        assert overview["totals"]["skills"] == 1
        assert overview["totals"]["failures"] == 1
        assert any(row["name"] == "terminal" for row in overview["tools"])
        assert overview["skills"][0]["name"] == "writer"
        assert overview["failures"][0]["name"] == "web_search"
