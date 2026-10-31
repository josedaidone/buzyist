// The Integrations tab's status lines (integrationStatus in ui/main.js): which
// catalog key each integration shows for what the page knows.

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadUiFunction, readJson } from "./load.mjs";

const integrationStatus = loadUiFunction("integrationStatus");
const en = readJson("ui", "i18n", "en.json");
const has = (key) => key.split(".").reduce((o, k) => (o == null ? o : o[k]), en) !== undefined;
const status = (id, x) => JSON.parse(JSON.stringify(integrationStatus(id, x)));

test("Todoist", () => {
  assert.equal(status("todoist", { on: false }).key, "settings.todoist.no_todoist_help");
  assert.deepEqual(status("todoist", { on: true, saved: true, token: false }), { key: "settings.integrations.todoist.needs_token", values: {}, tone: "warn" });
  assert.equal(status("todoist", { on: true, saved: false, token: true }).key, "settings.integrations.unsaved");
  assert.equal(status("todoist", { on: true, saved: true, token: true }).key, "settings.integrations.todoist.connected");
});

test("BUSY Bar", () => {
  assert.equal(status("busybar", { on: false, bar: { local: true } }).key, "settings.bar.no_bar_help");
  assert.equal(status("busybar", { on: true, bar: { local: true } }).key, "settings.integrations.unsaved");
  assert.equal(status("busybar", { on: true, bar: null }).key, "settings.integrations.unsaved");
  assert.equal(status("busybar", { on: true, bar: { offline: true, ok: true, via: "Wi-Fi" } }).key, "settings.integrations.busybar.offline");
  assert.deepEqual(status("busybar", { on: true, bar: { ok: true, via: "USB" } }), { key: "settings.integrations.busybar.via", values: { via: "USB" }, tone: "ok" });
  assert.equal(status("busybar", { on: true, bar: { ok: false } }).key, "settings.integrations.busybar.unreachable");
  assert.equal(status("busybar", { on: true, bar: {} }).key, "settings.integrations.busybar.on");
});

test("every status key is in the catalog", () => {
  const xs = [{ on: false }, { on: true }, { on: true, token: true }, { on: true, saved: true, token: true },
    { on: true, bar: { offline: true } }, { on: true, bar: { ok: true, via: "x" } }, { on: true, bar: { ok: false } }, { on: true, bar: {} }];
  for (const id of ["todoist", "busybar"]) for (const x of xs) assert.ok(has(status(id, x).key), status(id, x).key);
  assert.equal(integrationStatus("other", {}), null);
});
