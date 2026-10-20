"use strict";

// Export report (the Stats drawer's button): the period on show, filter
// included, as one HTML page that opens in any browser. It has no script and
// loads nothing: the styles are in it, the logo is inline and the charts are
// SVG, with each bar's text as its tooltip (<title>). The text comes from the
// same helpers as the drawer (ui/main.js: focusTiles, focusLine, statTiles,
// axisMarks, ...), so it reads the same, in the app language. Every list is
// whole (the drawer shows 8 apps until "Show all"). Api.report_assets gives
// the logo and the version; Api.save_report asks where to save it.

const REPORT_CSS = `
:root { --tomate: #D9361B; --limit: #C98A12; --ink: #3A1410; --muted: #7A4A42; --faint: #A07A72;
  --bg: #FFF7F3; --surface: #FFEDE6; --line: #F7D5C9; color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 400 14px/1.45 "Figtree", "Segoe UI", system-ui, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 28px 40px; }
header { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; border-bottom: 2px solid var(--line); padding-bottom: 18px; margin-bottom: 8px; }
header .logo { height: 34px; width: auto; display: block; }
header .logo svg { height: 34px; width: auto; display: block; }
h1 { margin: 0; font: 600 30px/1.1 "Fredoka", "Arial Rounded MT Bold", sans-serif; }
.period { font-size: 15px; font-weight: 700; color: var(--muted); }
.head-text { display: grid; gap: 2px; }
section { padding-top: 22px; break-inside: avoid-page; }
h2 { margin: 0 0 12px; font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); }
h3 { margin: 18px 0 8px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
.note { font-size: 13px; color: var(--muted); margin: 0 0 10px; }
.chip { display: inline-block; padding: 3px 12px; border-radius: 999px; background: var(--surface); font-size: 13px; font-weight: 700; margin-bottom: 10px; }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
.tiles.two { grid-template-columns: repeat(2, 1fr); }
.tile { background: var(--surface); border-radius: 18px; padding: 10px 14px; }
.tile .label { font-size: 12px; font-weight: 700; color: var(--muted); }
.tile .num { font: 600 28px/1.2 "Fredoka", "Arial Rounded MT Bold", sans-serif; white-space: nowrap; }
.tile .line { font-size: 13px; color: var(--muted); line-height: 1.35; margin-top: 2px; }
.streak { font-size: 13.5px; color: var(--muted); margin: -4px 0 12px; }
.streak b { color: var(--ink); }
.legend { display: flex; gap: 14px; font-size: 12.5px; color: var(--muted); margin: 4px 0 2px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.sw.f { background: var(--tomate); } .sw.l { background: var(--limit); }
svg.chart { width: 100%; height: auto; display: block; margin: 6px 0 4px; overflow: visible; }
svg.chart text { font: 11px "Figtree", "Segoe UI", system-ui, sans-serif; fill: var(--faint); }
svg.chart .bar:hover .hit { fill: var(--surface); }
table { width: 100%; border-collapse: separate; border-spacing: 0 4px; }
td { background: var(--surface); padding: 8px 12px; vertical-align: baseline; }
td:first-child { border-radius: 18px 0 0 18px; font-weight: 600; }
td:last-child { border-radius: 0 18px 18px 0; text-align: right; font-weight: 700; white-space: nowrap; }
td.sub { color: var(--muted); font-size: 13px; text-align: right; white-space: nowrap; }
td .icon { width: 16px; height: 16px; display: inline-block; vertical-align: -3px; margin-right: 8px; color: var(--muted); }
.empty { font-size: 13px; color: var(--muted); margin: 0 0 8px; }
footer { margin-top: 28px; padding-top: 14px; border-top: 2px solid var(--line); font-size: 12.5px; color: var(--faint); }
@media print { body { background: #fff; } main { padding: 0; } td, .tile, .chip { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
@media (max-width: 560px) { .tiles { grid-template-columns: 1fr; } td.sub { display: none; } }
`;

const REPORT_COLORS = { f: "#D9361B", l: "#C98A12" };

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function reportTiles(tiles) {
  return `<div class="tiles${tiles.length === 2 ? " two" : ""}">` + tiles.map((x) =>
    `<div class="tile"><div class="label">${esc(x.label)}</div><div class="num">${esc(x.number)}</div>`
    + x.lines.map((line) => `<div class="line">${esc(line)}</div>`).join("") + "</div>").join("") + "</div>";
}

function reportLegend(items) {
  return `<div class="legend">` + items.map(([cls, key]) => `<span><i class="sw ${cls}"></i>${esc(t(key))}</span>`).join("") + "</div>";
}

// A stacked bar chart as SVG, like barChart: `series` is [key, "f"|"l"]
// (the first at the bottom), gridlines from niceTicks, dates from axisMarks.
// A single day has no chart, as in the drawer.
function reportChart(s, series, label, line, minutes) {
  const bars = s.bars || [];
  if (s.span === 1 || !bars.length) return "";
  const W = 800, H = 150, gutter = 52, axis = 22;
  const plot = W - gutter, n = bars.length;
  const gap = n > 60 ? 1 : 3, slot = plot / n, width = Math.max(1, slot - gap);
  const total = (d) => series.reduce((sum, [k]) => sum + d[k], 0);
  const ticks = niceTicks(Math.max(0, ...bars.map(total)), 3, minutes);
  const top = ticks[ticks.length - 1];
  const y = (v) => H - (H * v) / top;
  const value = (v) => minutes ? duration(v) : formatNumber(v);
  const out = [`<svg class="chart" viewBox="0 0 ${W} ${H + axis}" role="img" aria-label="${esc(label)}">`];
  for (const v of ticks.slice(1)) {
    out.push(`<line x1="${gutter}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="#F7D5C9" stroke-dasharray="3 3"/>`,
             `<text x="${gutter - 6}" y="${y(v) + 4}" text-anchor="end">${esc(value(v))}</text>`);
  }
  bars.forEach((d, i) => {
    const x = gutter + i * slot + gap / 2;
    const head = barDay(d, !s.offset && i === n - 1);
    out.push(`<g class="bar"><title>${esc(head + ": " + line(d))}</title>`,
             `<rect class="hit" x="${x}" y="0" width="${width}" height="${H}" fill="transparent"/>`);
    let base = 0;
    series.forEach(([key, cls], j) => {
      if (!d[key]) return;
      const h = Math.max(2, (H * d[key]) / top);
      const last = !series.slice(j + 1).some(([k]) => d[k]);
      const r = last ? Math.min(4, width / 2) : 0;
      // A segment with rounded top corners only when it is the bar's top one.
      const yTop = H - base - h;
      out.push(r
        ? `<path d="M${x} ${yTop + h}V${yTop + r}q0 -${r} ${r} -${r}h${width - 2 * r}q${r} 0 ${r} ${r}V${yTop + h}z" fill="${REPORT_COLORS[cls]}"/>`
        : `<rect x="${x}" y="${yTop}" width="${width}" height="${h}" fill="${REPORT_COLORS[cls]}"/>`);
      base += h;
    });
    out.push("</g>");
  });
  out.push(`<line x1="${gutter}" x2="${W}" y1="${H + 1}" y2="${H + 1}" stroke="#F7D5C9" stroke-width="2"/>`);
  // The dates, first and last kept first, dropping any that would touch (about 6.5px a letter).
  const marks = axisMarks(bars, s.span).map((m) => {
    const text = axisText(m, bars[m.i], s);
    const w = text.length * 6.5;
    const x = m.kind === "first" ? gutter : m.kind === "last" ? W : gutter + (m.i + 0.5) * slot;
    const left = m.kind === "first" ? x : m.kind === "last" ? x - w : x - w / 2;
    return { ...m, text, x, left, right: left + w };
  });
  const kept = [];
  for (const m of [...marks.filter((m) => m.kind === "last" || m.kind === "first"), ...marks.filter((m) => m.kind !== "last" && m.kind !== "first")]) {
    if (!kept.some((k) => m.left < k.right + 8 && m.right > k.left - 8)) kept.push(m);
  }
  for (const m of kept) {
    const anchor = m.kind === "first" ? "start" : m.kind === "last" ? "end" : "middle";
    out.push(`<text x="${m.x}" y="${H + 16}" text-anchor="${anchor}">${esc(m.text)}</text>`);
  }
  out.push("</svg>");
  return out.join("");
}

// An activity's icon, copied from the page's symbol so the file needs nothing else.
function reportIcon(name) {
  const symbol = document.getElementById("a-" + (ACTIVITY_ICONS.includes(name) ? name : "dot"));
  if (!symbol) return "";
  const attrs = ["viewBox", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin"]
    .filter((a) => symbol.getAttribute(a)).map((a) => `${a}="${esc(symbol.getAttribute(a))}"`).join(" ");
  return `<svg class="icon" ${attrs} aria-hidden="true">${symbol.innerHTML}</svg>`;
}

// Rows [{name, sub, value, icon}] as a table, or `empty` when there are none.
function reportRows(rows, empty) {
  if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
  return "<table>" + rows.map((r) => `<tr><td>${r.icon || ""}${esc(r.name)}</td><td class="sub">${esc(r.sub)}</td><td>${esc(r.value)}</td></tr>`).join("") + "</table>";
}

function reportFocus(s) {
  const pc = focusPc(s);
  const parts = [`<section><h2>${esc(t("stats.focus.title"))}</h2>`];
  const f = s.filter;
  if (f) {
    const name = f.kind === "activity" ? focusGroupName(f) : f.name;
    parts.push(`<div class="chip">${esc(t(f.kind === "activity" ? "stats.filter.activity" : "stats.filter.task", { name }))}</div>`);
  }
  parts.push(reportTiles(focusTiles(s)));
  const [before, current, after] = focusStreak(s);
  parts.push(`<div class="streak">${esc(before)}<b>${esc(current)}</b>${esc(after)}</div>`);
  if (pc && s.span !== 1) parts.push(reportLegend([["f", "stats.focus.legend_focus"], ["l", "stats.focus.legend_outside"]]));
  parts.push(reportChart(s, pc ? [["minutes", "f"], ["outside", "l"]] : [["minutes", "f"]],
    t("stats.focus.chart", { ...rangeDays(s), duration: duration(s.totals.range.minutes) }), focusLine(s), true));

  const groups = s.activities || [];
  if (groups.some((g) => !g.id.startsWith("_")) || f) {
    parts.push(`<h3>${esc(t("stats.focus.by_activity"))}</h3>`, reportRows(groups.map((g) => ({
      icon: g.icon ? reportIcon(g.icon) : "",
      name: focusGroupName(g),
      sub: t("stats.focus.pomodoros", { n: g.pomodoros }) + " · " + percent(s.totals.range.minutes ? g.minutes / s.totals.range.minutes : 0),
      value: duration(g.minutes),
    })), t("stats.focus.activities_empty")));
  }
  parts.push(`<h3>${esc(t("stats.focus.top_tasks"))}</h3>`, reportRows(s.tasks.map((task) => ({
    name: task.content, sub: focusCounts(task), value: duration(task.minutes),
  })), t("stats.focus.tasks_empty")));
  parts.push("</section>");
  return parts.join("");
}

// Left out when it was off and counted nothing in the period.
function reportApps(s) {
  const counted = s.totals.range.focus + s.totals.range.other > 0 || s.apps.length || (s.sites || []).length;
  if (!s.on && !counted) return "";
  const rows = (list) => list.map((a) => ({
    name: a.name,
    sub: a.today == null ? t("stats.apps.row_past", { share: percent(a.range ? a.focus / a.range : 0) })
      : t("stats.apps.row", { today: duration(a.today), share: percent(a.range ? a.focus / a.range : 0) }),
    value: duration(a.range),
  }));
  return [
    `<section><h2>${esc(t("stats.apps.title"))}</h2>`,
    s.on ? "" : `<p class="note">${esc(t("stats.report.apps_off"))}</p>`,
    reportTiles(appTimeTiles(s)),
    s.span === 1 ? "" : reportLegend([["f", "stats.apps.legend_focus"], ["l", "stats.apps.legend_other"]]),
    reportChart(s, [["focus", "f"], ["other", "l"]],
      t("stats.apps.chart", { ...rangeDays(s), duration: duration(s.totals.range.focus + s.totals.range.other) }), appTimeLine, true),
    `<h3>${esc(t("stats.apps.most_apps"))}</h3>`, reportRows(rows(s.apps), t("stats.apps.apps_empty")),
    `<h3>${esc(t("stats.apps.most_sites"))}</h3>`, reportRows(rows(s.sites || []), t("stats.apps.sites_empty")),
    "</section>",
  ].join("");
}

function reportBlocks(s) {
  return [
    `<section><h2>${esc(t("stats.blocks.title"))}</h2>`,
    reportTiles(blocksTiles(s)),
    s.span === 1 ? "" : reportLegend([["f", "stats.blocks.legend_focus"], ["l", "stats.blocks.legend_limit"]]),
    reportChart(s, [["focus", "f"], ["limit", "l"]],
      t("stats.blocks.chart", { ...rangeDays(s), focus: formatNumber(s.totals.range.focus), limit: formatNumber(s.totals.range.limit) }), blocksLine, false),
    `<h3>${esc(t("stats.blocks.most"))}</h3>`,
    reportRows(s.sites.map((site) => ({ name: site.host, sub: blocksSplit(site), value: t("stats.blocks.count", { n: site.focus + site.limit }) })),
      t("stats.blocks.empty")),
    "</section>",
  ].join("");
}

// The whole page for an Api.stats answer; `assets` is {logo, version}.
function statsReport(s, assets) {
  const period = rangeLabel(s.range, true);
  const now = new Date();
  const made = t("stats.report.made", {
    version: assets.version || "",
    date: formatDate(now, { day: "numeric", month: "long", year: "numeric" }),
    time: formatTime(now, { hour: "2-digit", minute: "2-digit" }),
  });
  return `<!doctype html>
<html lang="${esc(I18N.lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="buZyist ${esc(assets.version || "")}">
<title>${esc(t("stats.report.doc_title", { range: period }))}</title>
<style>${REPORT_CSS}</style>
</head>
<body>
<main>
<header>${assets.logo ? `<div class="logo">${assets.logo}</div>` : ""}<div class="head-text"><h1>${esc(t("stats.report.heading"))}</h1><div class="period">${esc(period)}</div></div></header>
${reportFocus(s.focus)}
${reportApps(s.apps)}
${reportBlocks(s.blocks)}
<footer>${esc(made)}</footer>
</main>
</body>
</html>
`;
}

// "buzyist-stats-2026-10-08_2026-10-14.html" (one day: just the day).
function reportFileName(r) {
  return "buzyist-stats-" + (r.first === r.last ? r.last : r.first + "_" + r.last) + ".html";
}

$("exportStats").onclick = async () => {
  if (!lastStats) return toast(t("errors.report.empty"), true);
  const button = $("exportStats");
  button.disabled = true;
  button.textContent = t("stats.report.exporting");
  try {
    const assets = await api.report_assets();
    const html = statsReport(lastStats, assets.ok ? assets : {});
    const result = await api.save_report(html, reportFileName(lastStats.range));
    if (!result.ok) return toast(result.error, true);
    if (result.path) toast(t("stats.report.saved", { path: result.path }));
  } catch (err) {
    toast(String(err), true);
  } finally {
    button.disabled = false;
    button.textContent = t("stats.report.export");
  }
};
