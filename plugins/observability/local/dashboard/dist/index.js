(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React, fetchJSON } = SDK;
  const h = React.createElement;
  const { useEffect, useMemo, useState } = SDK.hooks;
  const API = "/api/plugins/local-observability";
  const RANGES = [
    { key: "1h", label: "1 小时" },
    { key: "24h", label: "24 小时" },
    { key: "7d", label: "7 天" },
    { key: "all", label: "全部" },
  ];

  const EVENT_LABELS = {
    "llm.requested": "LLM 请求",
    "llm.completed": "LLM 完成",
    "tool.started": "工具开始",
    "tool.completed": "工具完成",
    "skill.used": "Skill 使用",
    "turn.completed": "回合完成",
    "task.completed": "任务完成",
    "task.failed": "任务失败",
    "task.interrupted": "任务中断",
  };

  function fmtNumber(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? n.toLocaleString() : "0";
  }

  function fmtMs(value) {
    if (value === null || value === undefined) return "n/a";
    const n = Number(value);
    if (!Number.isFinite(n)) return "n/a";
    if (n >= 1000) return (n / 1000).toFixed(2) + "s";
    return Math.round(n) + "ms";
  }

  function shortId(value) {
    return String(value || "").slice(0, 12);
  }

  function dateShort(value) {
    if (!value) return "";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString();
  }

  function eventLabel(value) {
    return EVENT_LABELS[value] || value || "-";
  }

  function statusLabel(value) {
    if (value === "success") return "成功";
    if (value === "error") return "错误";
    if (value === "interrupted") return "中断";
    return value || "-";
  }

  function payloadPreview(payload) {
    if (!payload || typeof payload !== "object") return "";
    try {
      return JSON.stringify(payload).slice(0, 260);
    } catch (_err) {
      return "";
    }
  }

  function Kpi({ label, value, hint }) {
    return h("div", { className: "hermes-observe-kpi" },
      h("span", null, label),
      h("strong", null, value),
      hint ? h("div", { className: "hermes-observe-muted" }, hint) : null,
    );
  }

  function Panel({ title, actions, children }) {
    return h("section", { className: "hermes-observe-panel" },
      h("div", { className: "hermes-observe-panel-head" },
        h("h2", null, title),
        actions || null,
      ),
      children,
    );
  }

  function Bars({ rows, labelKey = "name", valueKey = "count", valueFormat = fmtNumber }) {
    const max = useMemo(function () {
      return Math.max(1, ...rows.map((row) => Number(row[valueKey] || 0)));
    }, [rows, valueKey]);
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无数据。");
    return h("div", { className: "hermes-observe-bars" },
      rows.map((row) => {
        const value = Number(row[valueKey] || 0);
        const width = Math.max(4, Math.round((value / max) * 100));
        const label = row.label || row[labelKey];
        return h("div", { className: "hermes-observe-bar-row", key: row[labelKey] || label },
          h("div", { className: "hermes-observe-bar-label", title: label }, label),
          h("div", { className: "hermes-observe-bar-track" },
            h("div", { className: "hermes-observe-bar-fill", style: { width: width + "%" } }),
          ),
          h("div", { className: "hermes-observe-code" }, valueFormat(value)),
        );
      }),
    );
  }

  function RangePicker({ value, onChange }) {
    return h("div", { className: "hermes-observe-range", role: "group", "aria-label": "时间范围" },
      RANGES.map((item) => h("button", {
        key: item.key,
        className: value === item.key ? "hermes-observe-range-button active" : "hermes-observe-range-button",
        onClick: () => onChange(item.key),
      }, item.label)),
    );
  }

  function ToolTable({ rows }) {
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无工具调用记录。");
    return h("table", { className: "hermes-observe-table" },
      h("thead", null, h("tr", null,
        h("th", null, "工具"),
        h("th", null, "调用次数"),
        h("th", null, "错误"),
        h("th", null, "平均耗时"),
        h("th", null, "最长耗时"),
      )),
      h("tbody", null, rows.map((row) => h("tr", { key: row.name },
        h("td", { className: "hermes-observe-code" }, row.name),
        h("td", null, fmtNumber(row.count)),
        h("td", { className: row.errors ? "hermes-observe-status-error" : "" }, fmtNumber(row.errors)),
        h("td", null, fmtMs(row.avg_ms)),
        h("td", null, fmtMs(row.max_ms)),
      ))),
    );
  }

  function SkillTable({ rows }) {
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无 Skill 使用记录。");
    return h("table", { className: "hermes-observe-table" },
      h("thead", null, h("tr", null,
        h("th", null, "Skill"),
        h("th", null, "使用次数"),
        h("th", null, "错误"),
        h("th", null, "最近出现"),
      )),
      h("tbody", null, rows.map((row) => h("tr", { key: row.name },
        h("td", { className: "hermes-observe-code" }, row.name),
        h("td", null, fmtNumber(row.count)),
        h("td", { className: row.errors ? "hermes-observe-status-error" : "" }, fmtNumber(row.errors)),
        h("td", null, dateShort(row.last_seen)),
      ))),
    );
  }

  function TraceTable({ rows, selectedTraceId, onSelect }) {
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无 trace 记录。");
    return h("table", { className: "hermes-observe-table hermes-observe-clickable-table" },
      h("thead", null, h("tr", null,
        h("th", null, "Trace"),
        h("th", null, "事件数"),
        h("th", null, "工具"),
        h("th", null, "Skills"),
        h("th", null, "错误"),
        h("th", null, "最近出现"),
      )),
      h("tbody", null, rows.map((row) => h("tr", {
        key: row.trace_id,
        className: row.trace_id === selectedTraceId ? "selected" : "",
        onClick: () => onSelect(row.trace_id),
      },
        h("td", { className: "hermes-observe-code", title: row.trace_id }, shortId(row.trace_id)),
        h("td", null, fmtNumber(row.events)),
        h("td", null, fmtNumber(row.tools)),
        h("td", null, fmtNumber(row.skills)),
        h("td", { className: row.errors ? "hermes-observe-status-error" : "" }, fmtNumber(row.errors)),
        h("td", null, dateShort(row.last_seen)),
      ))),
    );
  }

  function FailureList({ rows, onSelectTrace }) {
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无失败记录。");
    return h("table", { className: "hermes-observe-table hermes-observe-clickable-table" },
      h("thead", null, h("tr", null,
        h("th", null, "时间"),
        h("th", null, "分类"),
        h("th", null, "事件"),
        h("th", null, "名称"),
        h("th", null, "Trace"),
      )),
      h("tbody", null, rows.map((row, idx) => h("tr", {
        key: row.created_at + idx,
        onClick: () => onSelectTrace(row.trace_id),
      },
        h("td", null, dateShort(row.created_at)),
        h("td", { className: "hermes-observe-status-error" }, row.failure_category ? row.failure_category.label : "未分类"),
        h("td", null, eventLabel(row.event_type)),
        h("td", { className: "hermes-observe-code" }, row.name || "agent_task"),
        h("td", { className: "hermes-observe-code", title: row.trace_id }, shortId(row.trace_id)),
      ))),
    );
  }

  function TraceDetail({ detail, loading, error }) {
    if (loading) return h("div", { className: "hermes-observe-empty" }, "Trace 加载中...");
    if (error) return h("div", { className: "hermes-observe-status-error" }, error);
    if (!detail || !detail.events || !detail.events.length) {
      return h("div", { className: "hermes-observe-empty" }, "选择一条 trace 查看完整链路。");
    }
    const summary = detail.summary || {};
    return h("div", { className: "hermes-observe-trace-detail" },
      h("div", { className: "hermes-observe-trace-summary" },
        h("div", null, h("span", null, "Trace"), h("strong", { className: "hermes-observe-code" }, shortId(summary.trace_id))),
        h("div", null, h("span", null, "事件"), h("strong", null, fmtNumber(summary.events))),
        h("div", null, h("span", null, "工具"), h("strong", null, fmtNumber(summary.tools))),
        h("div", null, h("span", null, "错误"), h("strong", { className: summary.errors ? "hermes-observe-status-error" : "" }, fmtNumber(summary.errors))),
        h("div", null, h("span", null, "观测耗时"), h("strong", null, fmtMs(summary.observed_duration_ms))),
      ),
      h("div", { className: "hermes-observe-timeline" },
        detail.events.map((row) => h("div", {
          className: row.status === "error" ? "hermes-observe-timeline-item error" : "hermes-observe-timeline-item",
          key: row.event_id,
        },
          h("div", { className: "hermes-observe-timeline-dot" }),
          h("div", { className: "hermes-observe-timeline-card" },
            h("div", { className: "hermes-observe-timeline-head" },
              h("strong", null, eventLabel(row.event_type)),
              h("span", null, dateShort(row.created_at)),
            ),
            h("div", { className: "hermes-observe-timeline-meta" },
              h("span", { className: "hermes-observe-code" }, row.name || row.span_type || "-"),
              h("span", { className: row.status === "error" ? "hermes-observe-status-error" : "hermes-observe-status-success" }, statusLabel(row.status)),
              row.duration_ms === null || row.duration_ms === undefined ? null : h("span", null, fmtMs(row.duration_ms)),
            ),
            payloadPreview(row.payload) ? h("pre", null, payloadPreview(row.payload)) : null,
          ),
        )),
      ),
    );
  }

  function ObservabilityDashboard() {
    const [range, setRange] = useState("24h");
    const [data, setData] = useState(null);
    const [events, setEvents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [exportPath, setExportPath] = useState("");
    const [selectedTraceId, setSelectedTraceId] = useState("");
    const [traceDetail, setTraceDetail] = useState(null);
    const [traceLoading, setTraceLoading] = useState(false);
    const [traceError, setTraceError] = useState("");

    async function load(nextRange) {
      const activeRange = nextRange || range;
      setLoading(true);
      setError("");
      try {
        const [overview, recent] = await Promise.all([
          fetchJSON(API + "/overview?range=" + encodeURIComponent(activeRange)),
          fetchJSON(API + "/events?limit=30&range=" + encodeURIComponent(activeRange)),
        ]);
        setData(overview);
        setEvents(recent.events || []);
        if (!selectedTraceId && overview.traces && overview.traces.length) {
          setSelectedTraceId(overview.traces[0].trace_id);
        }
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }

    async function loadTrace(traceId) {
      if (!traceId) {
        setTraceDetail(null);
        return;
      }
      setTraceLoading(true);
      setTraceError("");
      try {
        const result = await fetchJSON(API + "/traces/" + encodeURIComponent(traceId));
        setTraceDetail(result);
      } catch (err) {
        setTraceError(err && err.message ? err.message : String(err));
      } finally {
        setTraceLoading(false);
      }
    }

    async function exportEvents() {
      setError("");
      try {
        const result = await fetchJSON(API + "/export?limit=1000&range=" + encodeURIComponent(range));
        setExportPath(result.path || "");
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      }
    }

    function changeRange(nextRange) {
      setRange(nextRange);
      setSelectedTraceId("");
      setTraceDetail(null);
      load(nextRange);
    }

    useEffect(function () { load(range); }, []);
    useEffect(function () { loadTrace(selectedTraceId); }, [selectedTraceId]);

    const totals = (data && data.totals) || {};
    return h("div", { className: "hermes-observe" },
      h("div", { className: "hermes-observe-shell" },
        h("header", { className: "hermes-observe-header" },
          h("div", { className: "hermes-observe-title" },
            h("h1", null, "观测看板"),
            h("p", null, "查看 Hermes 本地运行事件、trace、工具调用、Skill 使用和任务结果。"),
          ),
          h("div", { className: "hermes-observe-actions" },
            h(RangePicker, { value: range, onChange: changeRange }),
            h("button", { className: "hermes-observe-button", onClick: () => load(range), disabled: loading }, loading ? "刷新中" : "刷新"),
            h("button", { className: "hermes-observe-button", onClick: exportEvents }, "导出 JSON"),
          ),
        ),
        error ? h("div", { className: "hermes-observe-status-error" }, error) : null,
        exportPath ? h("div", { className: "hermes-observe-muted" }, "已导出到 " + exportPath) : null,
        h("section", { className: "hermes-observe-kpis" },
          h(Kpi, { label: "事件总数", value: fmtNumber(totals.events) }),
          h(Kpi, { label: "Trace 数", value: fmtNumber(totals.traces) }),
          h(Kpi, { label: "工具调用", value: fmtNumber(totals.tools) }),
          h(Kpi, { label: "Skill 使用", value: fmtNumber(totals.skills) }),
          h(Kpi, { label: "失败次数", value: fmtNumber(totals.failures) }),
          h(Kpi, { label: "LLM 平均耗时", value: fmtMs(totals.avg_llm_ms) }),
        ),
        h("section", { className: "hermes-observe-grid" },
          h(Panel, { title: "事件类型分布" }, h(Bars, { rows: (data && data.event_types) || [] })),
          h(Panel, { title: "失败原因分类" }, h(Bars, { rows: (data && data.failure_categories) || [], labelKey: "code" })),
        ),
        h("section", { className: "hermes-observe-grid hermes-observe-grid-traces" },
          h(Panel, { title: "最近 Trace" },
            h(TraceTable, {
              rows: (data && data.traces) || [],
              selectedTraceId,
              onSelect: setSelectedTraceId,
            }),
          ),
          h(Panel, { title: "Trace 详情" }, h(TraceDetail, { detail: traceDetail, loading: traceLoading, error: traceError })),
        ),
        h("section", { className: "hermes-observe-grid" },
          h(Panel, { title: "工具性能" }, h(ToolTable, { rows: (data && data.tools) || [] })),
          h(Panel, { title: "Skill 使用" }, h(SkillTable, { rows: (data && data.skills) || [] })),
        ),
        h("section", { className: "hermes-observe-grid" },
          h(Panel, { title: "失败记录" }, h(FailureList, { rows: (data && data.failures) || [], onSelectTrace: setSelectedTraceId })),
          h(Panel, { title: "最近事件" },
            events.length ? h("table", { className: "hermes-observe-table" },
              h("thead", null, h("tr", null,
                h("th", null, "时间"),
                h("th", null, "类型"),
                h("th", null, "名称"),
                h("th", null, "状态"),
              )),
              h("tbody", null, events.slice(0, 12).map((row) => h("tr", { key: row.event_id },
                h("td", null, dateShort(row.created_at)),
                h("td", null, eventLabel(row.event_type)),
                h("td", { className: "hermes-observe-code" }, row.name),
                h("td", { className: row.status === "error" ? "hermes-observe-status-error" : "hermes-observe-status-success" }, statusLabel(row.status)),
              ))),
            ) : h("div", { className: "hermes-observe-empty" }, "暂无事件。"),
          ),
        ),
        data && data.paths ? h("footer", { className: "hermes-observe-muted hermes-observe-code" },
          "SQLite: " + data.paths.sqlite,
        ) : null,
      ),
    );
  }

  window.__HERMES_PLUGINS__.register("local-observability", ObservabilityDashboard);
})();
