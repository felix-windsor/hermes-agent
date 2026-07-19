(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const { React, fetchJSON } = SDK;
  const h = React.createElement;
  const { useEffect, useMemo, useState } = SDK.hooks;
  const API = "/api/plugins/local-observability";

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

  function Kpi({ label, value, hint }) {
    return h("div", { className: "hermes-observe-kpi" },
      h("span", null, label),
      h("strong", null, value),
      hint ? h("div", { className: "hermes-observe-muted" }, hint) : null,
    );
  }

  function Panel({ title, children }) {
    return h("section", { className: "hermes-observe-panel" },
      h("h2", null, title),
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
        return h("div", { className: "hermes-observe-bar-row", key: row[labelKey] },
          h("div", { className: "hermes-observe-bar-label", title: row[labelKey] }, row[labelKey]),
          h("div", { className: "hermes-observe-bar-track" },
            h("div", { className: "hermes-observe-bar-fill", style: { width: width + "%" } }),
          ),
          h("div", { className: "hermes-observe-code" }, valueFormat(value)),
        );
      }),
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

  function TraceTable({ rows }) {
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无 trace 记录。");
    return h("table", { className: "hermes-observe-table" },
      h("thead", null, h("tr", null,
        h("th", null, "Trace"),
        h("th", null, "事件数"),
        h("th", null, "工具"),
        h("th", null, "Skills"),
        h("th", null, "错误"),
        h("th", null, "最近出现"),
      )),
      h("tbody", null, rows.map((row) => h("tr", { key: row.trace_id },
        h("td", { className: "hermes-observe-code", title: row.trace_id }, shortId(row.trace_id)),
        h("td", null, fmtNumber(row.events)),
        h("td", null, fmtNumber(row.tools)),
        h("td", null, fmtNumber(row.skills)),
        h("td", { className: row.errors ? "hermes-observe-status-error" : "" }, fmtNumber(row.errors)),
        h("td", null, dateShort(row.last_seen)),
      ))),
    );
  }

  function FailureList({ rows }) {
    if (!rows.length) return h("div", { className: "hermes-observe-empty" }, "暂无失败记录。");
    return h("table", { className: "hermes-observe-table" },
      h("thead", null, h("tr", null,
        h("th", null, "时间"),
        h("th", null, "事件"),
        h("th", null, "名称"),
        h("th", null, "Trace"),
      )),
      h("tbody", null, rows.map((row, idx) => h("tr", { key: row.created_at + idx },
        h("td", null, dateShort(row.created_at)),
        h("td", { className: "hermes-observe-status-error" }, row.event_type),
        h("td", { className: "hermes-observe-code" }, row.name || "agent_task"),
        h("td", { className: "hermes-observe-code", title: row.trace_id }, shortId(row.trace_id)),
      ))),
    );
  }

  function ObservabilityDashboard() {
    const [data, setData] = useState(null);
    const [events, setEvents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [exportPath, setExportPath] = useState("");

    async function load() {
      setLoading(true);
      setError("");
      try {
        const [overview, recent] = await Promise.all([
          fetchJSON(API + "/overview"),
          fetchJSON(API + "/events?limit=30"),
        ]);
        setData(overview);
        setEvents(recent.events || []);
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }

    async function exportEvents() {
      setError("");
      try {
        const result = await fetchJSON(API + "/export?limit=1000");
        setExportPath(result.path || "");
      } catch (err) {
        setError(err && err.message ? err.message : String(err));
      }
    }

    useEffect(function () { load(); }, []);

    const totals = (data && data.totals) || {};
    return h("div", { className: "hermes-observe" },
      h("div", { className: "hermes-observe-shell" },
        h("header", { className: "hermes-observe-header" },
          h("div", { className: "hermes-observe-title" },
            h("h1", null, "观测看板"),
            h("p", null, "查看 Hermes 本地运行事件、trace、工具调用、Skill 使用和任务结果。"),
          ),
          h("div", { className: "hermes-observe-actions" },
            h("button", { className: "hermes-observe-button", onClick: load, disabled: loading }, loading ? "刷新中" : "刷新"),
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
          h(Panel, { title: "最近 Trace" }, h(TraceTable, { rows: (data && data.traces) || [] })),
        ),
        h("section", { className: "hermes-observe-grid" },
          h(Panel, { title: "工具性能" }, h(ToolTable, { rows: (data && data.tools) || [] })),
          h(Panel, { title: "Skill 使用" }, h(SkillTable, { rows: (data && data.skills) || [] })),
        ),
        h("section", { className: "hermes-observe-grid" },
          h(Panel, { title: "失败记录" }, h(FailureList, { rows: (data && data.failures) || [] })),
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
                h("td", null, row.event_type),
                h("td", { className: "hermes-observe-code" }, row.name),
                h("td", { className: row.status === "error" ? "hermes-observe-status-error" : "hermes-observe-status-success" }, row.status || "-"),
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
