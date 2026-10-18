// The UI's t() (ui/i18n.js) against the same cases as buzyist/i18n.py
// (tests/i18n_cases.json, also run by tests/test_i18n.py).

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadUiScript, readJson } from "./load.mjs";

const { catalogs, cases, shipped } = readJson("tests", "i18n_cases.json");
const i18n = loadUiScript("ui/i18n.js");

function check(files, list) {
  for (const { lang, key, values, out } of list) {
    i18n.setI18n({ lang, catalog: files[lang] || {}, english: files.en });
    assert.equal(i18n.t(key, values), out, JSON.stringify({ lang, key, values }));
  }
}

test("t() agrees with buzyist/i18n.py on the test catalogs", () => check(catalogs, cases));

test("t() agrees with buzyist/i18n.py on the shipped catalogs", () => {
  const langs = [...new Set(["en", ...shipped.map((c) => c.lang)])];
  check(Object.fromEntries(langs.map((lang) => [lang, readJson("ui", "i18n", `${lang}.json`)])), shipped);
});

test("before the catalogs arrive t() gives the key, never an error", () => {
  i18n.setI18n(null);
  assert.equal(i18n.t("summary.close", { n: 2 }), "summary.close");
});
