// The toolbar popup: what buZyist is doing, and where each website limit
// stands. Everything comes from the background worker (popupData), which also
// works while buZyist is closed, from the last answer it got.

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Texts, dates and times in the app language (i18n.js).
const clock = formatClock;
const sameDay = (a, b) => new Date(a).toDateString() === new Date(b).toDateString();
// "17:00", or "Thu 08:00" when it isn't today.
const when = (ms) => (sameDay(ms, Date.now()) ? "" : formatWeekday(ms) + " ") + clock(ms);

function duration(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  if (s < 60) return t("time.seconds", { n: s });
  const m = Math.ceil(s / 60);
  if (m < 60) return t("time.minutes", { n: m });
  const h = Math.floor(m / 60);
  return m % 60 ? t("time.hours_minutes", { h, m: m % 60 }) : t("time.hours", { h });
}

const names = (patterns) => patterns.join(", ");

// Replaces a box's content only when it actually changed, so a re-render
// every 2 s doesn't make anything flicker.
function swap(box, ...nodes) {
  const next = document.createElement("div");
  next.append(...nodes);
  if (box.innerHTML !== next.innerHTML) box.replaceChildren(...next.childNodes);
}
function setClass(node, cls) {
  if (node.className !== cls) node.className = cls;
}
function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

// A bar showing how much of a budget is left.
function bar(left, budget) {
  const wrap = el("div", "bar" + (left <= 0 ? " full" : left <= 120 ? " warn" : ""));
  const fill = el("i");
  fill.style.width = Math.max(0, Math.min(100, (100 * left) / budget)) + "%";
  wrap.append(fill);
  return wrap;
}

function renderFocus(data) {
  const out = [];
  const box = { append: (node) => out.push(node) };
  const f = data.focus;
  if (f.active) {
    const mins = Math.max(0, Math.ceil((f.ends_at_ms - Date.now()) / 60000));
    // No end time: a call, which lasts as long as it does.
    box.append(el("div", "big bad", f.ends_at_ms ? t("popup.minutes_left", { n: mins }) : t("popup.in_focus")));
    box.append(el("div", "small", f.task ? t("popup.focusing_on", { task: f.task }) : t("popup.work_running")));
    const rules = f.mode === "allow" ? t("popup.allow_mode")
      : f.sites ? t("popup.block_mode", { n: f.sites }) : t("popup.nothing_blocked");
    // An activity's own blocking rules (the session's name already carries it).
    box.append(el("div", "small muted", f.activity ? t("popup.during_activity", { activity: f.activity, rules }) : rules));
  } else if (data.offline && !data.known) {
    box.append(el("div", "small muted", t("popup.unknown_yet")));
  } else if (data.offline) {
    box.append(el("div", "small muted", t("popup.unknown_closed")));
  } else {
    box.append(el("div", "small", t("popup.no_work")));
    box.append(el("div", "small muted", t("popup.start_one")));
  }
  swap($("focus"), ...out);
}

function ruleRow(rule) {
  const row = el("div", "rule");
  const head = el("div", "name");
  head.append(names(rule.patterns));
  if (rule.here) head.append(el("span", "tag", t("popup.this_tab")));
  row.append(head);

  if (rule.state) {
    row.append(el("div", "line blocked", rule.state.reason === "window"
      ? t("popup.blocked_until", { when: when(rule.state.until) })
      : t("popup.times_up_until", { when: when(rule.state.until) })));
  } else if (rule.left_s != null) {
    const used = Math.max(0, rule.budget_s - rule.left_s);
    row.append(el("div", "line", t("popup.left_of", { left: duration(rule.left_s), label: limitLabel(rule.label) })));
    row.append(bar(rule.left_s, rule.budget_s));
    row.append(el("div", "line", t("popup.used_of", {
      used: Math.floor(used / 60), budget: Math.round(rule.budget_s / 60), when: when(rule.resets_ms),
    })));
  } else if (!rule.next_window) {
    row.append(el("div", "line", t("popup.no_limit_now")));
  }
  if (rule.next_window && !(rule.state && rule.state.reason === "window")) {
    const [start, end] = rule.next_window;
    row.append(el("div", "line", t("popup.blocked_span", { start: when(start), end: clock(end) })));
  }
  if (rule.pass_until_ms > Date.now()) {
    row.append(el("div", "line", t("popup.extra_until", { time: clock(rule.pass_until_ms) })));
  }
  return row;
}

function render(data) {
  const status = $("status");
  setText(status, data.offline ? t("popup.not_running") : t("popup.connected"));
  setClass(status, "status " + (data.offline ? "bad" : "ok"));

  // Help: what to do when something is missing. The extension can't open the
  // app, so it says where to find it. Each piece is a whole sentence.
  let helpClass = "notice hidden";
  const helpParts = [];
  const tip = (info, lead, ...lines) => {
    helpClass = "notice" + (info ? " info" : "");
    helpParts.push(el("b", "", t(lead) + " "), ...lines.map((key) => t(key) + " "));
  };
  if (data.offline) {
    tip(false, "popup.help_offline",
      data.known ? "popup.help_offline_known" : "popup.help_offline_unknown", "popup.help_offline_start");
  } else if (!data.limits_on && !data.limits.length) {
    tip(true, "popup.help_limits_off", "popup.help_limits_off_how");
  } else if (!data.limits_on) {
    tip(true, "popup.help_limits_switched_off", "popup.help_limits_switched_off_how");
  } else if (!data.limits.length) {
    tip(true, "popup.help_no_limits", "popup.help_no_limits_how");
  }

  setClass($("help"), helpClass);
  swap($("help"), ...helpParts);

  renderFocus(data);

  const here = data.limits.filter((r) => r.here);
  $("hereCard").classList.toggle("hidden", !here.length);
  swap($("here"), ...here.map((rule) => {
    const box = el("div");
    if (rule.state) box.append(el("div", "big bad", t(rule.state.reason === "window" ? "popup.blocked" : "popup.times_up")));
    else if (rule.left_s != null) {
      const level = rule.left_s <= 30 ? "bad" : rule.left_s <= 120 ? "warn" : "ok";
      box.append(el("div", "big " + level, t("popup.left", { left: duration(rule.left_s) })));
      box.append(bar(rule.left_s, rule.budget_s));
      box.append(el("div", "small muted", limitLabel(rule.label)));
    } else {
      box.append(el("div", "small", t("popup.no_time_limit")));
    }
    return box;
  }));

  $("limitsCard").classList.toggle("hidden", !data.limits.length);
  swap($("limits"), ...data.limits.map(ruleRow));

  // How often a website was blocked today, on this PC (buZyist's Stats tab has the rest).
  const b = data.blocks_today || { focus: 0, limit: 0 };
  $("blocksCard").classList.toggle("hidden", !data.known);
  swap($("blocks"), ...(b.focus + b.limit
    ? [el("div", "big", t("popup.times", { n: b.focus + b.limit })),
       el("div", "small muted", t("popup.blocks_split", { focus: b.focus, limit: b.limit }))]
    : [el("div", "small muted", t("popup.no_blocks"))]));

  swap($("footer"), ...tBold("popup.footer", { where: t("popup.footer_where") }), " ",
    el("span", "", t("popup.version", { version: data.version })));
}

let shown = false;
async function load() {
  let data = null;
  try {
    data = await chrome.runtime.sendMessage({ type: "popup" });
  } catch (e) { /* the worker is waking up; try again shortly */ }
  // buZyist's language, as the worker last heard it (English until then).
  if (data && (await useLanguage(data.language))) applyI18n();
  if (data) { render(data); shown = true; }
  // Once something is shown, a missed answer keeps it instead of flashing "Starting...".
  else if (!shown) setText($("status"), t("popup.starting"));
}

load();
setInterval(load, 2000);
