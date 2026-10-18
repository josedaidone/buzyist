"use strict";

// The extension's text in the app language. buZyist names the language in
// every answer (`language`, see App.site_focus); the worker keeps it with its
// last answer (lastGood, in chrome.storage.local), so the popup, the block
// page and the badge stay in that language while buZyist is closed, and are
// English until buZyist has answered once. Chrome's _locales would follow the
// browser's language instead, so the extension has catalogs of its own:
// i18n/<code>.json, the same format as buZyist's ui/i18n/*.json (nested keys,
// "_" notes, plural entries of CLDR categories picked by values.n with
// Intl.PluralRules), and t() is the same contract as ui/i18n.js (the cases in
// tests/i18n_cases.json run against both).
//
// Loaded by popup.html and blocked.html (a <script> before theirs) and by the
// worker (importScripts). Names here are shared with those scripts' globals.

const I18N = { lang: "en", active: {}, english: {}, loaded: "" };
const I18N_PLURALS = ["zero", "one", "two", "few", "many", "other"];
const I18N_CODE = /^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/; // what may name a catalog file
const i18nRules = {};
const i18nFiles = {}; // lang -> promise of its catalog file ({} when there is none)

function isPluralEntry(value) {
  return !!value && typeof value === "object" && typeof value.other === "string"
    && Object.entries(value).every(([k, v]) => I18N_PLURALS.includes(k) && typeof v === "string");
}

// A catalog file's nested groups as {"area.key": text or plural entry}.
function flattenCatalog(tree, prefix = "", out = {}) {
  for (const [key, value] of Object.entries(tree || {})) {
    if (key.startsWith("_")) continue;
    if (value && typeof value === "object" && !isPluralEntry(value)) flattenCatalog(value, prefix + key + ".", out);
    else out[prefix + key] = value;
  }
  return out;
}

// {lang, catalog, english}: the active language's file and English's.
function setI18n(data) {
  I18N.lang = (data && data.lang) || "en";
  I18N.active = flattenCatalog(data && data.catalog);
  I18N.english = flattenCatalog(data && data.english);
}

function pluralCategory(lang, n) {
  if (!i18nRules[lang]) {
    try { i18nRules[lang] = new Intl.PluralRules(lang); } catch (err) { i18nRules[lang] = new Intl.PluralRules("en"); }
  }
  return i18nRules[lang].select(n);
}

function pickText(entry, lang, values) {
  if (typeof entry === "string") return entry;
  if (!isPluralEntry(entry)) return null;
  const n = values.n;
  const category = typeof n === "number" && isFinite(n) ? pluralCategory(lang, n) : "other";
  return entry[category] || entry.other;
}

// The text of `key` before its placeholders are filled: the active language,
// else English, else the key.
function i18nTemplate(key, values) {
  let text = pickText(I18N.active[key], I18N.lang, values);
  if (text === null) text = pickText(I18N.english[key], "en", values);
  return text === null ? key : text;
}

function t(key, values) {
  values = values || {};
  return i18nTemplate(key, values).replace(/\{(\w+)\}/g, (match, name) => (name in values ? String(values[name]) : match));
}

// t() as nodes, each filled placeholder in bold: "Focus until <b>17:00</b>".
function tBold(key, values) {
  values = values || {};
  return i18nTemplate(key, values).split(/(\{\w+\})/).filter(Boolean).map((part) => {
    const name = /^\{(\w+)\}$/.exec(part);
    if (!name || !(name[1] in values)) return part;
    const b = document.createElement("b");
    b.textContent = String(values[name[1]]);
    return b;
  });
}

// A language code as buZyist sends it, or English for anything else.
const i18nLanguage = (lang) => (typeof lang === "string" && I18N_CODE.test(lang) ? lang : "en");

function i18nFile(lang) {
  if (!i18nFiles[lang]) {
    i18nFiles[lang] = fetch(chrome.runtime.getURL("i18n/" + lang + ".json"))
      .then((res) => (res.ok ? res.json() : {}))
      .catch(() => ({}));
  }
  return i18nFiles[lang];
}

// Switch to `lang` (English when unset or unknown); true when it changed.
async function useLanguage(lang) {
  lang = i18nLanguage(lang);
  if (I18N.loaded === lang) return false;
  const [english, catalog] = await Promise.all([i18nFile("en"), lang === "en" ? null : i18nFile(lang)]);
  setI18n({ lang, catalog: catalog || english, english });
  I18N.loaded = lang;
  return true;
}

// A page's static text: data-i18n="key" (its text, the <title> too) and
// data-i18n-<attr>="key". The English in the markup shows until then.
function applyI18n(root) {
  document.documentElement.lang = I18N.lang;
  for (const node of (root || document).querySelectorAll("*")) {
    for (const [name, key] of Object.entries(node.dataset)) {
      if (name === "i18n") node.textContent = t(key);
      else if (name.startsWith("i18n")) {
        const attr = name.slice(4).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase()).replace(/^-/, "");
        node.setAttribute(attr, t(key));
      }
    }
  }
}

// Dates and times in the app language.
const formatClock = (ms) => new Date(ms).toLocaleTimeString(I18N.lang, { hour: "2-digit", minute: "2-digit" });
const formatWeekday = (ms) => new Date(ms).toLocaleDateString(I18N.lang, { weekday: "short" });

// buZyist's budget label ("5 min per hour", "10 min per 2 days", made by
// site_rules.period_label) in the app language; anything else as it came.
function limitLabel(label) {
  const m = /^(\d+) min per (?:(\d+) )?(minute|hour|day|week)s?$/.exec(label || "");
  return m ? t("limit." + m[3], { minutes: Number(m[1]), n: Number(m[2] || 1) }) : label || "";
}
