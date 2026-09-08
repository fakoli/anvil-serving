import {
  el,
  list,
  badge,
  empty,
  number,
  table,
  formatTime,
  safeLink,
} from "./common.js";
import { request, query } from "./api.js";
const colors = [
  "#9abbff",
  "#70ddb2",
  "#f5c477",
  "#ff94a3",
  "#c3acff",
  "#9fdce4",
  "#ddd2b7",
  "#c6d0dd",
];
const cache = new Map();
window.addEventListener("observatory-session-expired", () => cache.clear());
window.addEventListener("observatory-session-changed", () => cache.clear());
const dashes = ["", "6 4", "2 3", "10 3 2 3", "12 5", "3 2", "8 2 2 2", "1 3"];
export function chartPanel(id, ctx) {
  const panel = el(
    "article",
    { class: "panel chart", "aria-label": `${id} chart` },
    el("p", { class: "loading", text: "Loading historical chart…" }),
  );
  ctx.jobs.push(async () => {
    try {
      const key = JSON.stringify([
        ctx.session.identity,
        id,
        ctx.host,
        ctx.serve,
        ctx.range,
      ]);
      const cached = cache.get(key);
      const data =
        cached && Date.now() - cached.at < 15000
          ? cached.data
          : await request(
              query("metrics", {
                chart: id,
                host: ctx.host,
                serve: ctx.serve,
                range: ctx.range,
              }),
              { signal: ctx.signal },
            );
      if (!cached || data !== cached.data) {
        if (cache.size > 64) cache.clear();
        cache.set(key, { at: Date.now(), data });
      }
      if (ctx.signal.aborted) return;
      renderChart(panel, data, ctx.zone);
    } catch (error) {
      if (error.name !== "AbortError")
        panel.replaceChildren(
          el("h2", { text: id.replaceAll("_", " ") }),
          empty(error.message),
        );
    }
  });
  return panel;
}
export function renderChart(panel, data, zone) {
  const percent = data.unit === "ratio";
  const displayUnit = percent ? "% (source ratio)" : data.unit;
  const header = el(
    "div",
    { class: "card-head" },
    el(
      "div",
      {},
      el("h2", { text: data.title || "Historical metric" }),
      el("p", {
        class: "chart-subtitle",
        text: `${displayUnit || "Unit not reported"} · ${data.source || "Source not reported"} · ${data.window || "Window not reported"}`,
      }),
    ),
    safeLink(data.grafana_url, "Open in Grafana ↗"),
  );
  panel.replaceChildren(header, badge(data.status));
  const series = list(data.series)
    .slice(0, 8)
    .map((s) => ({
      ...s,
      points: list(s.points)
        .slice(0, 1000)
        .filter(
          (p) =>
            Array.isArray(p) &&
            p.length === 2 &&
            Number.isFinite(p[0]) &&
            (p[1] === null || Number.isFinite(p[1])),
        )
        .map(([at, value]) => [
          at,
          value === null ? null : percent ? value * 100 : value,
        ]),
    }));
  const measured = series.flatMap((s) => s.points).filter((p) => p[1] !== null);
  if (!measured.length) {
    panel.append(
      empty(
        data.reason || "No samples reported for this scope and time window.",
      ),
    );
    return;
  }
  if (data.reason) panel.append(el("p", { class: "meta", text: data.reason }));
  const allPoints = series.flatMap((s) => s.points);
  const start = Math.min(...allPoints.map((p) => p[0])),
    end = Math.max(...allPoints.map((p) => p[0]));
  const low = Math.min(0, ...measured.map((p) => p[1])),
    high = Math.max(...measured.map((p) => p[1]));
  const x = (v) => 40 + ((v - start) / (end - start || 1)) * 590,
    y = (v) => 148 - ((v - low) / (high - low || 1)) * 130;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 640 166");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    `${data.title}. ${series.length} series; gaps indicate missing samples. Data table follows.`,
  );
  for (let i = 0; i < 3; i++) {
    const yAxis = 18 + i * 65;
    const line = document.createElementNS(svg.namespaceURI, "line");
    for (const [k, v] of Object.entries({
      x1: 40,
      x2: 630,
      y1: yAxis,
      y2: yAxis,
      stroke: "#273140",
    }))
      line.setAttribute(k, v);
    svg.append(line);
    const label = document.createElementNS(svg.namespaceURI, "text");
    label.setAttribute("x", "0");
    label.setAttribute("y", String(yAxis + 4));
    label.setAttribute("fill", "#a7b4c5");
    label.setAttribute("font-size", "10");
    label.textContent = new Intl.NumberFormat("en-US", {
      notation: "compact",
      maximumSignificantDigits: 3,
    }).format(high - ((high - low) * i) / 2);
    svg.append(label);
  }
  const legend = el("div", { class: "chart-legend" });
  series.forEach((s, index) => {
    let pen = false;
    let path = "";
    for (const [at, value] of s.points) {
      if (value === null) {
        pen = false;
        continue;
      }
      path += `${pen ? "L" : "M"}${x(at).toFixed(2)},${y(value).toFixed(2)} `;
      pen = true;
    }
    const curve = document.createElementNS(svg.namespaceURI, "path");
    for (const [k, v] of Object.entries({
      d: path,
      fill: "none",
      stroke: colors[index],
      "stroke-width": 2,
      "stroke-dasharray": dashes[index],
      "data-series": String(index),
    }))
      curve.setAttribute(k, v);
    svg.append(curve);
    const key = el(
      "span",
      { class: `legend-key series-${index}` },
      el("span", { class: "legend-line", "aria-hidden": "true" }),
      `${index + 1}. ${s.label || s.id}`,
    );
    legend.append(key);
  });
  panel.append(
    el(
      "div",
      { class: "chart-plot" },
      svg,
      el(
        "div",
        { class: "chart-axis" },
        el("span", { text: formatTime(start, zone) }),
        el("span", { text: formatTime(end, zone) }),
      ),
    ),
    legend,
  );
  const rows = series.flatMap((s) =>
    s.points.map(([at, value]) => [
      s.label || s.id,
      formatTime(at, zone),
      value === null ? "Gap — no sample" : number(value),
    ]),
  );
  panel.append(
    el(
      "details",
      {},
      el("summary", { text: `View data table (${rows.length} samples)` }),
      table(
        ["Series", "Time", displayUnit || "Value"],
        rows,
        "All returned samples. A gap is distinct from zero.",
      ),
    ),
  );
}
