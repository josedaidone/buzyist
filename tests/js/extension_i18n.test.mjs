// The extension's text in the app language (extension/i18n.js, #28): its t()
// keeps the contract of buzyist/i18n.py and ui/i18n.js (tests/i18n_cases.json),
// it loads the catalog buZyist names and falls back to English, and the worker
// takes the language from buZyist's last answer.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import vm from "node:vm";

import { EXTENSION_ID, ROOT, loadBackground, readJson } from "./load.mjs";

const { catalogs, cases } = readJson("tests", "i18n_cases.json");
const files = { en: readJson("extension", "i18n", "en.json"), "pt-BR": readJson("extension", "i18n", "pt-BR.json") };

// extension/i18n.js in a fresh context whose fetch reads the extension folder,
// as chrome-extension://<id>/i18n/<code>.json would. `fetched` lists what it asked for.
function loadI18n() {
  const fetched = [];
  const prefix = `chrome-extension://${EXTENSION_ID}/`;
  const context = vm.createContext({
    console,
    chrome: { runtime: { getURL: (path) => prefix + path } },
    fetch: async (url) => {
      fetched.push(url.slice(prefix.length));
      try {
        const text = readFileSync(join(ROOT, "extension", url.slice(prefix.length)), "utf8");
        return { ok: true, json: async () => JSON.parse(text) };
      } catch (e) {
        return { ok: false, json: async () => ({}) };
      }
    },
  });
  const path = join(ROOT, "extension", "i18n.js");
  vm.runInContext(readFileSync(path, "utf8"), context, { filename: path });
  context.fetched = fetched;
  return context;
}

test("t() agrees with buzyist/i18n.py and ui/i18n.js on the shared cases", () => {
  const i18n = loadI18n();
  for (const { lang, key, values, out } of cases) {
    i18n.setI18n({ lang, catalog: catalogs[lang] || {}, english: catalogs.en });
    assert.equal(i18n.t(key, values), out, JSON.stringify({ lang, key, values }));
  }
});

test("a fresh extension speaks English, then the language buZyist names", async () => {
  const i18n = loadI18n();
  assert.equal(i18n.t("popup.connected"), "popup.connected"); // nothing loaded yet: the key, never an error
  assert.equal(await i18n.useLanguage(undefined), true); // no answer yet: English
  assert.equal(i18n.t("popup.connected"), "Connected");
  assert.equal(await i18n.useLanguage("en"), false); // already there
  assert.equal(await i18n.useLanguage("pt-BR"), true);
  assert.equal(i18n.t("popup.connected"), "Conectado");
  assert.equal(i18n.t("blocked.pass", { n: 2 }), "Permitir mais 2 minutos");
  assert.equal(i18n.t("blocked.pass", { n: 1 }), "Permitir mais 1 minuto");
  assert.equal(i18n.t("popup.minutes_left", { n: 0 }), "Falta 0 min"); // pt's "one" covers 0
  assert.equal(i18n.t("blocked.focus_activity", { activity: "Email" }), "Bloqueado durante Email");
  assert.equal(await i18n.useLanguage("en"), true);
  assert.equal(i18n.t("blocked.focus_activity", { activity: "Email" }), "Blocked during Email");
});

test("a language with no catalog, or a code that isn't one, shows English", async () => {
  const i18n = loadI18n();
  await i18n.useLanguage("fr");
  assert.equal(i18n.t("popup.times", { n: 3 }), "3 times");
  await i18n.useLanguage("../manifest");
  assert.ok(!i18n.fetched.some((path) => path.includes("manifest")));
  assert.equal(i18n.t("popup.times", { n: 1 }), "1 time");
});

test("buZyist's budget labels are translated, anything else is kept", () => {
  const i18n = loadI18n();
  i18n.setI18n({ lang: "pt-BR", catalog: files["pt-BR"], english: files.en });
  assert.equal(i18n.limitLabel("5 min per hour"), "5 min por hora");
  assert.equal(i18n.limitLabel("30 min per 2 days"), "30 min a cada 2 dias");
  assert.equal(i18n.limitLabel("10 min per 3 weeks"), "10 min a cada 3 semanas");
  assert.equal(i18n.limitLabel("5 min por hora"), "5 min por hora"); // already translated by buZyist
  assert.equal(i18n.limitLabel(undefined), "");
  i18n.setI18n({ lang: "en", catalog: files.en, english: files.en });
  assert.equal(i18n.limitLabel("1 min per minute"), "1 min per minute");
  assert.equal(i18n.limitLabel("15 min per 2 hours"), "15 min per 2 hours");
});

test("the worker keeps the language of buZyist's last answer, English without one", () => {
  const bg = loadBackground();
  assert.equal(bg.get("languageNow")(), "en");
  bg.set("lastGood", { active: false, language: "pt-BR" });
  assert.equal(bg.get("languageNow")(), "pt-BR");
  bg.set("lastGood", { active: false }); // a buZyist from before languages
  assert.equal(bg.get("languageNow")(), "en");
});

test("the badge's title and text are in the app language", () => {
  const bg = loadBackground();
  bg.run(`chrome.action.setTitle = (o) => { globalThis.__title = o.title; };
          chrome.action.setBadgeText = (o) => { globalThis.__text = o.text; };`);
  bg.get("setI18n")({ lang: "pt-BR", catalog: files["pt-BR"], english: files.en });
  const now = Date.now();
  bg.get("updateBadge")({ active: true, ends_at_ms: now + 5 * 60000, limits: [] }, now, "");
  assert.equal(bg.get("__title"), "buZyist: foco, faltam 5 min");
  assert.equal(bg.get("__text"), "5");
  const rule = { id: "r1", patterns: ["youtube.com"], remaining_s: 600, limit_end_ms: now + 3600000, limit_label: "20 min per hour" };
  bg.get("updateBadge")({ active: false, limits_on: true, limits: [rule] }, now, "https://youtube.com/");
  assert.equal(bg.get("__title"), "buZyist: 10m restantes em youtube.com (20 min por hora)");
  assert.equal(bg.get("__text"), "10m");
});
