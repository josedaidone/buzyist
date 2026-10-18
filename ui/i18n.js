"use strict";

// The app language in the pages: the same contract as buzyist/i18n.py
// (tests/i18n_cases.json runs against both). t(key, values) looks the key up
// in the active catalog, then in English, then gives the key itself; it fills
// named placeholders ("{n} min") and picks a plural entry's form
// ({"one": ..., "other": ...}) from values.n with Intl.PluralRules.
// Python sends the catalogs as the files are (api.i18n, at load); a language
// change reloads the page. Static markup is translated by applyI18n():
//   data-i18n="key"             the element's text (with child elements, like
//                               an icon, only its last text is replaced)
//   data-i18n-<attr>="key"      that attribute: data-i18n-title,
//                               data-i18n-placeholder, data-i18n-aria-label, ...
//   data-i18n-rich="key"        the element's content, from a text that may hold
//                               <b>, <i>, <code>, <kbd> and <br> (setRich)
// The English in the markup stays as written until then.

const I18N = { lang: "en", active: {}, english: {} };
const PLURAL_CATEGORIES = ["zero", "one", "two", "few", "many", "other"];
const pluralRules = {};

function isPluralEntry(value) {
  return !!value && typeof value === "object" && typeof value.other === "string"
    && Object.entries(value).every(([k, v]) => PLURAL_CATEGORIES.includes(k) && typeof v === "string");
}

// A catalog file's nested groups as {"area.key": text or plural entry};
// keys starting with "_" are notes.
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
  if (!pluralRules[lang]) {
    try { pluralRules[lang] = new Intl.PluralRules(lang); } catch (err) { pluralRules[lang] = new Intl.PluralRules("en"); }
  }
  return pluralRules[lang].select(n);
}

function pickText(entry, lang, values) {
  if (typeof entry === "string") return entry;
  if (!isPluralEntry(entry)) return null;
  const n = values.n;
  const category = typeof n === "number" && isFinite(n) ? pluralCategory(lang, n) : "other";
  return entry[category] || entry.other;
}

function t(key, values) {
  values = values || {};
  let text = pickText(I18N.active[key], I18N.lang, values);
  if (text === null) text = pickText(I18N.english[key], "en", values);
  if (text === null) return key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in values ? String(values[name]) : match));
}

function applyI18n(root) {
  for (const node of (root || document).querySelectorAll("*")) {
    for (const [name, key] of Object.entries(node.dataset)) {
      if (name === "i18n") setText(node, t(key));
      else if (name === "i18nRich") setRich(node, t(key));
      else if (name.startsWith("i18n")) {
        // dataset turns data-i18n-aria-label into i18nAriaLabel
        const attr = name.slice(4).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase()).replace(/^-/, "");
        node.setAttribute(attr, t(key));
      }
    }
  }
}

function setText(node, text) {
  if (!node.children.length) { node.textContent = text; return; }
  const texts = [...node.childNodes].filter((c) => c.nodeType === 3 && c.textContent.trim());
  if (texts.length) texts[texts.length - 1].textContent = text;
  else node.append(text);
}

// A text with a few inline tags (help texts: <b>Close</b>, <code>*.google.com</code>)
// as the node's content. Only those tags become elements, without attributes;
// anything else stays text, so a catalog can't put markup of its own in a page.
const RICH_TAGS = /<(b|i|code|kbd)>([^<]*)<\/\1>|<br>/g;
function setRich(node, text) {
  node.replaceChildren();
  let at = 0;
  for (const m of text.matchAll(RICH_TAGS)) {
    if (m.index > at) node.append(text.slice(at, m.index));
    node.append(m[1] ? Object.assign(document.createElement(m[1]), { textContent: m[2] }) : document.createElement("br"));
    at = m.index + m[0].length;
  }
  if (at < text.length) node.append(text.slice(at));
}

// Ask Python for the catalogs and translate the page; English if that fails.
async function loadI18n(api) {
  try {
    setI18n(await api.i18n());
  } catch (err) {
    console.error("i18n", err);
  }
  document.documentElement.lang = I18N.lang;
  applyI18n(document);
}

// Dates and numbers in the app language (never a hard-coded "en-US").
const formatDate = (date, options) => date.toLocaleDateString(I18N.lang, options);
const formatTime = (date, options) => date.toLocaleTimeString(I18N.lang, options);
const formatNumber = (n, options) => Number(n).toLocaleString(I18N.lang, options);
