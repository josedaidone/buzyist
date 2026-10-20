// The Stats tab's charts: the gridlines (niceTicks) and the dates under the
// bars (axisMarks) in ui/main.js, on the cases in tests/stats_cases.json.
// The bars are built here the way buzyist/stats_common.py does (one per day,
// or per ISO week for a year; tests/test_stats_common.py checks those).

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadUiFunction, readJson } from "./load.mjs";

const CASES = readJson("tests", "stats_cases.json");
const niceTicks = loadUiFunction("niceTicks");
const axisMarks = loadUiFunction("axisMarks");

// JSON.parse turns the vm's arrays into this realm's, for deepEqual.
const plain = (value) => JSON.parse(JSON.stringify(value));

function bars(today, span, weekly) {
  const end = Date.parse(today + "T00:00:00Z");
  const out = [];
  for (let i = span - 1; i >= 0; i--) {
    const day = new Date(end - i * 86_400_000).toISOString().slice(0, 10);
    const monday = new Date(day + "T12:00:00Z").getUTCDay() === 1;
    if (!out.length || !weekly || monday) out.push({ day, last: day, days: 1 });
    else Object.assign(out.at(-1), { last: day, days: out.at(-1).days + 1 });
  }
  return out;
}

test("niceTicks: even steps from 0 to at least the max", () => {
  for (const { max, count, minutes, out } of CASES.ticks) {
    const ticks = plain(niceTicks(max, count, minutes));
    assert.deepEqual(ticks, out, JSON.stringify({ max, minutes }));
    assert.ok(ticks.at(-1) >= max && ticks.at(-2) < Math.max(max, 1));  // the top is the first step past the max
  }
});

test("axisMarks: the dates under the bars", () => {
  for (const { today, span, weekly, out } of CASES.marks) {
    const got = plain(axisMarks(bars(today, span, weekly), span)).map((m) => [m.i, m.kind]);
    assert.deepEqual(got, out, `${span} days`);
  }
});

test("axisMarks: no bars, one bar", () => {
  assert.deepEqual(plain(axisMarks([], 7)), []);
  assert.deepEqual(plain(axisMarks(bars("2026-10-14", 1, false), 1)), [{ i: 0, kind: "last" }]);
});
