// Export report (ui/report.js): the page statsReport builds from an
// Api.stats answer, with the helpers it shares with the Stats drawer pulled
// out of ui/main.js (which needs a whole page to run) and the English catalog.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import vm from "node:vm";

import { functionSource, readJson, ROOT } from "./load.mjs";

const FUNCTIONS = ["duration", "splitAround", "statTiles", "bigDuration", "changeLine", "barDay", "niceTicks", "axisMarks",
  "axisText", "keepCounts", "focusTiles", "focusStreak", "focusLine", "appTimeTiles", "blocksTiles", "rangeLabel"];
const CONSTS = ["ACTIVITY_ICONS", "percent", "statDay", "rangeDays", "focusPc", "focusCounts", "focusGroupName",
  "appTimeLine", "blocksLine", "blocksSplit"];

function loadReport() {
  const context = vm.createContext({ console, document: { getElementById: () => null } });
  const read = (path) => readFileSync(join(ROOT, path), "utf8");
  vm.runInContext(read("ui/i18n.js"), context);
  const english = readJson("ui", "i18n", "en.json");
  context.__catalogs = { lang: "en", catalog: english, english };
  vm.runInContext("setI18n(__catalogs)", context);
  const main = read("ui/main.js").split(/\r?\n/);
  const consts = CONSTS.map((name) => {
    const line = main.find((l) => l.startsWith(`const ${name} =`));
    if (!line) throw new Error(`const ${name} not found in ui/main.js`);
    return line;
  });
  const report = read("ui/report.js");
  const pure = report.slice(0, report.indexOf('$("exportStats")'));  // the button's handler needs the page
  vm.runInContext([...consts, ...FUNCTIONS.map((f) => functionSource("ui/main.js", f)), pure,
    "globalThis.statsReport = statsReport; globalThis.reportFileName = reportFileName;"].join("\n"), context);
  return context;
}

function days(last, n) {
  const end = Date.parse(last + "T00:00:00Z");
  return Array.from({ length: n }, (_, i) => new Date(end - (n - 1 - i) * 86_400_000).toISOString().slice(0, 10));
}

// An answer shaped like App.stats for `span` days ending 2026-10-14.
function answer(span) {
  const list = days("2026-10-14", span);
  const info = { span, offset: 0, by: "day", first: list[0], last: list.at(-1) };
  const bars = (make) => list.map((day, i) => ({ day, last: day, days: 1, ...make(i) }));
  const tot = (o) => ({ range: o, previous: o, average: o, today: o });
  return {
    range: { ...info, has_older: false },
    focus: {
      ...info, pc_on: false, filter: null, change: 0.25,
      bars: bars((i) => ({ minutes: i * 25, pomodoros: i, outside: 0 })),
      totals: tot({ minutes: 75, pomodoros: 3, sessions: 2, tasks: 1, completed: 1, outside: 0 }),
      streak: { current: 3, best: 5 },
      tasks: [{ key: "t1", content: "Fix <b>it</b> & ship", minutes: 75, pomodoros: 3, sessions: 2 }],
      activities: [{ id: "_task", name: "", icon: "", minutes: 75, pomodoros: 3, sessions: 2 }],
    },
    apps: {
      ...info, on: true, extension: true, change: null,
      bars: bars(() => ({ focus: 10, other: 20 })),
      totals: tot({ focus: 10, other: 20 }),
      apps: [{ key: "code", name: "VS Code", range: 30, focus: 10, today: 30 }],
      sites: [],
    },
    blocks: { ...info, change: null, bars: bars(() => ({ focus: 1, limit: 0 })), totals: tot({ focus: 1, limit: 0 }), sites: [{ host: "youtube.com", focus: 1, limit: 0 }] },
  };
}

test("statsReport: a whole page with the sections, escaped, and no script", () => {
  const { statsReport } = loadReport();
  const html = statsReport(answer(7), { logo: "<svg id=logo></svg>", version: "1.2.3" });
  assert.match(html, /^<!doctype html>/);
  assert.match(html, /<html lang="en">/);
  assert.ok(html.includes("<svg id=logo></svg>"));
  for (const text of ["Focus", "Time in apps", "Websites blocked", "Most focused tasks", "VS Code", "youtube.com", "Made with buZyist 1.2.3"]) {
    assert.ok(html.includes(text), text);
  }
  assert.ok(html.includes("Fix &lt;b&gt;it&lt;/b&gt; &amp; ship"));
  assert.ok(!/<script/i.test(html));
  assert.equal((html.match(/<svg class="chart"/g) || []).length, 3);
  assert.ok(html.includes("+25% on the previous 7 days"));
});

test("statsReport: a single day has tiles and lists but no charts", () => {
  const { statsReport } = loadReport();
  const html = statsReport(answer(1), { logo: "", version: "" });
  assert.ok(!html.includes('<svg class="chart"'));
  assert.ok(html.includes('class="tiles two"'));
});

test("statsReport: time in apps is left out when it was off and counted nothing", () => {
  const { statsReport } = loadReport();
  const s = answer(7);
  s.apps = { ...s.apps, on: false, apps: [], bars: s.apps.bars.map((b) => ({ ...b, focus: 0, other: 0 })),
    totals: { range: { focus: 0, other: 0 }, previous: { focus: 0, other: 0 }, average: { focus: 0, other: 0 }, today: { focus: 0, other: 0 } } };
  assert.ok(!statsReport(s, {}).includes("Time in apps"));
});

test("reportFileName: the period's days", () => {
  const { reportFileName } = loadReport();
  assert.equal(reportFileName({ first: "2026-10-08", last: "2026-10-14" }), "buzyist-stats-2026-10-08_2026-10-14.html");
  assert.equal(reportFileName({ first: "2026-10-14", last: "2026-10-14" }), "buzyist-stats-2026-10-14.html");
});
