"use strict";

const $ = (id) => document.getElementById(id);
const RING = 2 * Math.PI * 120;  // the ring's r in ui/main.html
const PRESETS = [  // name: main.setup.presets.<name>
  { name: "classic", work_minutes: 25, rest_minutes: 5, cycles: 4 },
  { name: "short", work_minutes: 15, rest_minutes: 5, cycles: 4 },
  { name: "long", work_minutes: 50, rest_minutes: 10, cycles: 3 },
  { name: "deep", work_minutes: 90, rest_minutes: 15, cycles: 2 },
];

let api = null;
let state = null;          // last answer from get_state()
let tasks = [];
let shown = [];            // tasks after the search filter
let sel = 0;
let pomo = null;           // the pomodoro settings as edited here
let saveTimer = null;
let toastTimer = null;
let loadingTasks = false;
let lastTaskLoad = 0;
let filters = [];          // saved filters: [{name, query}]
let activeFilter = "";
let loadSeq = 0;           // newest task request; older answers are dropped
let editing = undefined;   // filter being edited: its name, null for a new one

// ------------------------------------------------------------- helpers

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

// [text before `part`, text after it] (all of it before when it isn't there),
// to set part of a translated text apart, e.g. a number in bold.
function splitAround(text, part) {
  const i = text.indexOf(part);
  return i < 0 ? [text, ""] : [text.slice(0, i), text.slice(i + part.length)];
}

function toast(message, bad) {
  const box = $("toast");
  box.textContent = message;
  box.className = "on" + (bad ? " bad" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.className = ""), bad ? 7000 : 3200);
}

function clock(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function duration(min) {
  const h = Math.floor(min / 60), m = min % 60;
  return t(h ? (m ? "common.duration.h_m" : "common.duration.h") : "common.duration.min", { h, m });
}

function isoDay(offset) {
  const d = new Date();
  d.setDate(d.getDate() + (offset || 0));
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

function niceDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return formatDate(new Date(y, m - 1, d), { month: "short", day: "numeric" });
}

function dueText(task) {
  if (!task.day) return "";
  if (task.overdue) return t("main.tasks.overdue", { day: niceDay(task.day) });
  if (task.day === isoDay(0)) return task.time || "";
  if (task.day === isoDay(1)) return task.time ? t("main.tasks.tomorrow_at", { time: task.time }) : t("main.tasks.tomorrow");
  return niceDay(task.day) + (task.time ? " " + task.time : "");
}

// Todoist's priorities (its API counts 4 as P1), with a word (main.tasks.priority.p1...)
// so the colour is never the only cue.
const prioClass = (p) => ({ 4: "p1", 3: "p2", 2: "p3" })[p] || "";

// One of the three tomatinhos: "done" (full), "now" (ring with a core), "plan" (outline).
function tomato(kind, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", "#t-" + kind);
  svg.setAttribute("aria-hidden", "true");
  if (cls) svg.setAttribute("class", cls);
  svg.append(use);
  return svg;
}

// ---------------------------------------------------------- timer view

function timeLeft() {
  const t = state && state.timer;
  if (!t) return null;
  return t.paused ? t.left_ms : t.left_ms - (Date.now() - state.now);
}

// A call (phase "call") has no timer: it counts up from when it began.
const callTime = (t) => t.elapsed_ms + (Date.now() - state.now);

// The phase chip's text: common.phase.<key>.
const PHASE_KEYS = { idle: "ready", paused: "paused", work: "focus", rest: "break", call: "call" };

function renderTimer() {
  if (!state) return;
  const tm = state.timer, s = state.session;
  const phase = !tm ? "idle" : tm.phase === "call" ? "call" : tm.paused ? "paused" : tm.phase === "work" ? "work" : "rest";
  document.body.dataset.phase = phase;
  const p = pomo || state.pomodoro;
  if (phase === "call") return renderCall(tm, s);

  // ring + time: the ring shows the time left and empties as it passes
  let left, total;
  if (tm) {
    left = Math.max(0, timeLeft());
    total = tm.total_ms;
  } else {
    left = total = p.work_minutes * 60000;
  }
  $("time").textContent = clock(left);
  $("ringProgress").style.strokeDasharray = RING;
  $("ringProgress").style.strokeDashoffset = tm ? RING * (1 - left / total) : 0;
  $("timeLabel").textContent = t(!tm ? "main.timer.per_pomodoro" : tm.paused ? "main.timer.paused" : "main.timer.left");
  $("timerBox").setAttribute("aria-label", t(tm ? "main.timer.aria_left" : "main.timer.aria_per_pomodoro", { time: clock(left) }));
  $("phaseTag").textContent = t("common.phase." + PHASE_KEYS[phase]);

  // this session's pomodoros: done, now, planned
  const rounds = tm ? tm.rounds : p.cycles;
  const done = !tm ? 0 : tm.round - (tm.phase === "rest" ? 0 : 1);
  const now = tm && tm.phase === "work" ? tm.round : 0;
  const key = `${rounds}/${done}/${now}`;
  const dots = $("rounds");
  if (dots.dataset.key !== key) {
    dots.dataset.key = key;
    dots.textContent = "";
    if (rounds <= 12) {
      for (let n = 1; n <= rounds; n++) dots.append(tomato(n <= done ? "done" : n === now ? "now" : "plan"));
    }
    dots.setAttribute("aria-label", tm ? t("main.timer.round", { round: Math.min(tm.round, rounds), rounds })
      : t("main.timer.rounds", { n: rounds }));
  }
  const restMin = Math.round((tm ? tm.rest_ms : p.rest_minutes * 60000) / 60000);
  let line;
  if (!tm) line = t("main.timer.plan", { n: p.cycles, work: p.work_minutes, rest: p.rest_minutes });
  else if (tm.phase === "rest") line = t("main.timer.break_next", { next: tm.round + 1, rounds: tm.rounds });
  else if (tm.round >= tm.rounds) line = t("main.timer.last", { round: tm.round, rounds: tm.rounds });
  else line = t("main.timer.break_after", { round: tm.round, rounds: tm.rounds, rest: restMin });
  $("roundsText").textContent = line;
  document.title = tm ? `${clock(left)} · ${s ? s.task.content : "buZyist"}` : "buZyist";
}

// A call shows the time so far on a full ring, and no pomodoros.
function renderCall(tm, s) {
  const so = clock(callTime(tm));
  $("time").textContent = so;
  $("ringProgress").style.strokeDasharray = RING;
  $("ringProgress").style.strokeDashoffset = 0;
  $("timeLabel").textContent = t("main.timer.call");
  $("timerBox").setAttribute("aria-label", t("main.timer.call_aria", { time: so }));
  $("phaseTag").textContent = t("common.phase.call");
  const dots = $("rounds");
  if (dots.dataset.key !== "call") {
    dots.dataset.key = "call";
    dots.textContent = "";
    dots.setAttribute("aria-label", t("main.timer.call_rounds"));
  }
  $("roundsText").textContent = t("main.timer.call_line");
  document.title = `${so} · ${s ? s.task.content : "buZyist"}`;
}

let wasRunning = null;
function renderCurrent() {
  const tm = state.timer, s = state.session;
  const box = $("current");
  const meta = $("curMeta");
  meta.textContent = "";
  box.classList.toggle("idle", !s);
  $("controls").classList.toggle("hidden", !tm);
  if (s) {
    const time = s.started_at;
    $("curEyebrow").textContent = tm && tm.phase === "call" ? t("main.current.call", { time })
      : tm
      ? t(tm.phase === "rest" ? "main.current.break" : tm.paused ? "main.current.paused" : "main.current.focus", { time })
      : t("main.current.wrapping_up");
    $("curTask").textContent = s.task.content;
    if (s.task.project) {
      const proj = el("span", "", s.task.project);
      meta.append(proj);
    }
    if (s.label_added && state.label) meta.append(el("span", "", "@" + state.label));
    if (s.task.url) {
      const open = el("button", "link", t("main.current.open_todoist"));
      open.onclick = () => api.open_url(s.task.url);
      meta.append(open);
    }
  } else if (tm && state.remote) {
    // Started on another PC, which shared its task through Chrome sync.
    const r = state.remote;
    $("curEyebrow").textContent = r.started_at ? t("main.current.remote_since", { by: r.by, time: r.started_at })
      : t("main.current.remote", { by: r.by });
    $("curTask").textContent = r.task.content;
    if (r.task.project) meta.append(el("span", "", r.task.project));
    if (r.task.url) {
      const open = el("button", "link", t("main.current.open_todoist"));
      open.onclick = () => api.open_url(r.task.url);
      meta.append(open);
    }
  } else if (tm) {
    $("curEyebrow").textContent = t(state.bar.local || state.bar.offline ? "main.current.no_task" : "main.current.bar_no_task");
    $("curTask").textContent = t("main.current.timer_running");
    meta.append(el("span", "", state.todoist ? t("main.current.pick_to_focus") : ""));
  } else {
    $("curEyebrow").textContent = t(state.todoist ? "main.current.idle" : "main.current.idle_solo");
    $("curTask").textContent = t(state.todoist ? "main.current.pick" : "main.current.ready");
  }
  $("freeBox").classList.toggle("hidden", !!tm || !!s);
  syncFreeActivity();
  if (tm || s) closeFreeList();
  // One Tomate button per screen: with a task list, its play buttons are the main action.
  $("startFree").classList.toggle("primary", !state.todoist);
  // A call has no pause or skip: it ends with the call, or with Stop.
  $("btnPause").classList.toggle("hidden", !!tm && tm.phase === "call");
  $("btnSkip").classList.toggle("hidden", !!tm && tm.phase === "call");
  if (tm) {
    $("pauseIcon").setAttribute("href", tm.paused ? "#i-play" : "#i-pause");
    $("btnPause").title = t(tm.paused ? "main.controls.resume" : "main.controls.pause");
    $("btnPause").setAttribute("aria-label", $("btnPause").title);
  }
  $("setupHint").textContent = t(tm ? "main.setup.hint_running" : "main.setup.hint_idle");
  // Settings stay open while idle and fold away once a timer runs.
  const running = !!tm;
  if (running !== wasRunning) $("setup").open = !running;
  wasRunning = running;
}

function renderGuard() {
  const b = state.blocks;
  if (!b) return;
  const parts = [];
  if (b.apps) parts.push(t("main.guard.apps", { n: b.apps }));
  if (b.sites_on) parts.push(b.site_mode === "allow" ? t("main.guard.allowlist") : t("main.guard.sites", { n: b.sites }));
  if (b.limits) parts.push(t("main.guard.limits", { n: b.limits }));
  const count = (b.apps || 0) + (b.limits || 0) + (!b.sites_on ? 0 : b.site_mode === "allow" ? 1 : b.sites);
  const guard = $("guard");
  let text;
  if (!parts.length) text = t("main.guard.off");
  else text = t(b.active ? "main.guard.on" : "main.guard.ready", { n: count });
  if ($("guardText").textContent !== text) $("guardText").textContent = text;
  guard.classList.toggle("on", parts.length > 0);
  guard.classList.toggle("active", !!b.active);
  $("guardIcon").setAttribute("href", b.active ? "#i-shield-check" : "#i-shield");
  guard.title = parts.length ? t("main.guard.title", { list: parts.join(", ") }) : t("main.guard.title_off");
  guard.dataset.tab = b.apps || !b.sites_on ? "apps" : "sites";
}

function renderToday() {
  const today = state.today;
  const n = today.pomodoros;
  const date = formatDate(new Date(), { weekday: "long", month: "long", day: "numeric" });
  if ($("todayDate").textContent !== date) $("todayDate").textContent = date;
  $("sumTitle").textContent = n ? t("main.today.count", { n }) : t("main.today.none");
  const side = [];
  if (today.minutes) side.push(t("main.today.focus", { duration: duration(today.minutes) }));
  if (today.completed) side.push(t("main.today.completed", { n: today.completed }));
  $("sumSide").textContent = side.join(" · ");
  const box = $("sumTomatoes");
  const key = String(n);
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.textContent = "";
    if (!n) box.append(el("span", "none", t("main.today.empty")));
    for (let i = 0; i < Math.min(n, 16); i++) box.append(tomato("done"));
    if (n > 16) box.append(el("span", "more", `+${n - 16}`));
    box.setAttribute("aria-label", t("main.today.finished", { n }));
  }
  // Without the task list (no Todoist) the header chip carries the count, in bold.
  const chip = $("today");
  chip.textContent = "";
  const [before, after] = splitAround(t("main.today.count", { n }), String(n));
  chip.append(tomato("done", "t"), before, el("b", "", String(n)), after);
  if (today.minutes) chip.append(" · " + duration(today.minutes));
}

function renderChrome() {
  renderToday();
  renderGuard();

  const bar = state.bar;
  $("barChip").className = "chip" + (bar.offline ? "" : bar.ok === true ? " ok" : bar.ok === false ? " bad" : "");
  $("barText").textContent = bar.local ? t("main.bar.local")
    : bar.offline ? t("main.bar.offline")
    : bar.ok === true ? t("main.bar.via", { via: bar.via }) : bar.ok === false ? t("main.bar.unreachable") : t("main.bar.looking");
  $("barChip").title = bar.offline ? t("main.bar.offline_title") : bar.ok === false ? bar.error : "";
  $("hotkeyHint").textContent = state.hotkey ? state.hotkey.replace(/\b\w/g, (c) => c.toUpperCase()) : "";

  const u = state.update;
  $("updateChip").classList.toggle("hidden", !u);
  if (u) $("updateChip").textContent = u.busy ? t("main.header.updating") : t("main.header.update", { version: u.version });
  $("extChip").classList.toggle("hidden", !state.extension_outdated);
  if ($("drawer").classList.contains("on")) {
    renderUpdate();
    renderExtension(!!state.extension_connected, !!state.extension_outdated);
  }
}

function renderUpdate() {
  const u = state && state.update;
  const btn = $("installUpdate");
  btn.classList.toggle("hidden", !u);
  $("releaseNotes").classList.toggle("hidden", !u);
  if (!u) return;
  btn.textContent = u.busy ? t("settings.about.installing")
    : t(u.installable ? "settings.about.install" : "settings.about.download", { version: u.version });
  btn.disabled = u.busy;
  const out = $("updateResult");
  out.className = "test-result " + (u.error ? "bad" : "ok");
  out.textContent = u.error || (u.busy ? t("settings.about.downloading") : t("settings.about.out", { version: u.version }));
}

function renderEnded() {
  const e = state.ended;
  $("endedWrap").classList.toggle("on", !!e);
  $("scrim").classList.toggle("on", !!e || $("drawer").classList.contains("on") || editing !== undefined);
  if (!e) return;
  const finished = e.reason === "finished";
  $("endedChip").textContent = t(finished ? "summary.chip_done" : "summary.chip_stopped");
  $("endedChip").className = "state-chip " + (finished ? "done-chip" : "stopped-chip");
  const box = $("endedTomatoes");
  box.textContent = "";
  for (let i = 0; i < Math.min(e.done, 8); i++) box.append(tomato("done"));
  if (e.done > 8) box.append(el("span", "more", `+${e.done - 8}`));
  box.classList.toggle("hidden", !e.done);
  box.setAttribute("aria-label", t("summary.pomodoros", { n: e.done }));
  $("endedTitle").textContent = t(finished ? "summary.title_done" : "summary.title_stopped");
  $("endedTask").textContent = e.task.content;
  $("endedComplete").classList.toggle("hidden", !e.task.id || !state.todoist);
  $("endedSummary").textContent = e.done
    ? t("summary.result", { n: e.done, duration: duration(e.minutes) })
    : t("summary.none");
  $("endedProblems").textContent = (e.problems || []).join(" ");
}

async function poll() {
  let fresh;
  try {
    fresh = await api.get_state();
  } catch (err) {
    return;
  }
  applyState(fresh);
}

function applyState(fresh) {
  // A poll sent before the window was shown may answer after the state
  // onShown brought: never go back to an older one.
  if (state && fresh.now < state.now) return;
  state = fresh;
  if (!pomo) loadPomodoro(state.pomodoro);
  renderTimer();
  renderCurrent();
  renderChrome();
  renderEnded();
  renderTasksEnabled();
  markCurrentTask();
}

// The task list needs Todoist: grey it out without a token or when it's off.
let tasksWereOff = false;
let wasSolo = null;
function renderTasksEnabled() {
  // Todoist switched off: drop the task list and narrow the window.
  const solo = !state.use_todoist;
  document.body.classList.toggle("solo", solo);
  if (wasSolo !== null && solo !== wasSolo) api.set_solo(solo);
  wasSolo = solo;
  const off = !state.todoist;
  $("tasksCard").classList.toggle("off", off);
  for (const id of ["search", "refresh", "editFilter", "addFilter"]) $(id).disabled = off;
  if (off && !tasksWereOff) {
    tasks = [];
    $("filterTabs").textContent = "";
    $("filterText").textContent = "";
    showEmpty(t("main.tasks.off"), t(state.needs_setup ? "main.tasks.needs_token" : "main.tasks.turn_on"));
  }
  if (!off && tasksWereOff) loadTasks();
  tasksWereOff = off;
}

// --------------------------------------------------------------- tasks

function skeleton() {
  const list = $("list");
  list.textContent = "";
  for (let i = 0; i < 6; i++) list.append(el("li", "skeleton"));
}

async function loadTasks(quiet, filterName) {
  if (state && !state.todoist) return;
  if (loadingTasks && filterName === undefined) return;  // a refresh is already on its way
  const seq = ++loadSeq;
  loadingTasks = true;
  $("refresh").classList.add("spin");
  if (!quiet || !tasks.length) skeleton();
  const result = await api.get_tasks(filterName === undefined ? null : filterName);
  if (seq !== loadSeq) return;  // the user picked another filter meanwhile
  applyTaskResult(result);
}

// A task with a time still ahead waits at the end, earliest first; once its time comes it sorts
// by priority like the rest. Mirrors task_rank in buzyist/todoist.py.
function taskRank(t, now) {
  if (t.time && `${t.day} ${t.time}` > now) return [1, t.day, t.time, t.order];
  return [0, -t.priority, t.overdue ? 0 : 1, t.time || "99", t.order];
}

function compareRanks(a, b) {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

// Once a minute: move up the tasks whose time has come, without asking Todoist again.
function resortTasks() {
  if (!tasks.length || $("taskMenu")) return;
  const d = new Date();
  const now = isoDay(0) + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  const sorted = [...tasks].sort((a, b) => compareRanks(taskRank(a, now), taskRank(b, now)));
  if (sorted.every((t, i) => t === tasks[i])) return;
  const picked = shown[sel] && shown[sel].id;
  tasks = sorted;
  renderTasks();
  const at = shown.findIndex((t) => t.id === picked);
  if (at >= 0 && at !== sel) { sel = at; markSelection(false); }
}

function applyTaskResult(result) {
  loadingTasks = false;
  lastTaskLoad = Date.now();
  $("refresh").classList.remove("spin");
  if (!result.ok) {
    tasks = [];
    renderFilters();
    showEmpty(t("main.tasks.load_failed"), result.error, /token/i.test(result.error));
    return;
  }
  tasks = result.tasks;
  filters = result.filters;
  activeFilter = result.active;
  renderFilters();
  renderTasks();
}

// ------------------------------------------------------------- filters

function renderFilters(loadingName) {
  const box = $("filterTabs");
  box.textContent = "";
  filters.forEach((f, i) => {
    const b = el("button", "ftab" + (f.name === activeFilter ? " on" : "") + (f.name === loadingName ? " loading" : ""));
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", f.name === activeFilter);
    b.title = f.query + (i < 9 ? `  (Ctrl+${i + 1})` : "");
    b.append(f.name);
    if (i < 9) b.append(el("span", "n", String(i + 1)));
    b.onclick = () => selectFilter(f.name);
    b.ondblclick = () => openFilterEditor(f.name);
    box.append(b);
    if (f.name === activeFilter) setTimeout(() => b.scrollIntoView({ block: "nearest", inline: "nearest" }));
  });
  const active = filters.find((f) => f.name === activeFilter);
  $("filterText").textContent = active ? active.query : "";
  $("filterText").title = active ? active.query : "";
  $("editFilter").disabled = !active;
}

function selectFilter(name) {
  if (name === activeFilter && !loadingTasks) return;
  activeFilter = name;
  sel = 0;
  renderFilters(name);
  loadTasks(false, name);
}

const EXAMPLES = ["today | overdue", "7 days", "p1 | p2", "#Inbox", "@waiting", "no date", "assigned to: me", "!@waiting"];

function openFilterEditor(name) {
  const f = name ? filters.find((x) => x.name === name) : null;
  editing = f ? f.name : null;
  $("filterTitle").textContent = t(f ? "main.filters.edit_title" : "main.filters.new");
  $("f_name").value = f ? f.name : "";
  $("f_query").value = f ? f.query : "";
  $("filterTest").textContent = "";
  $("filterTest").className = "test-result";
  $("filterDelete").classList.toggle("hidden", !f || filters.length <= 1);
  $("filterLeft").classList.toggle("hidden", !f || filters.length <= 1);
  $("filterRight").classList.toggle("hidden", !f || filters.length <= 1);
  const ex = $("examples");
  ex.textContent = "";
  for (const q of EXAMPLES) {
    const b = el("button", "", q);
    b.type = "button";
    b.title = t("main.filters.add_to_query");
    b.onclick = () => {
      const area = $("f_query");
      const cur = area.value.trim();
      area.value = cur ? `(${cur}) & ${q}` : q;
      area.focus();
    };
    ex.append(b);
  }
  $("filterWrap").classList.add("on");
  $("scrim").classList.add("on");
  setTimeout(() => (f ? $("f_query") : $("f_name")).focus(), 50);
}

function closeFilterEditor() {
  editing = undefined;
  $("filterWrap").classList.remove("on");
  if (!(state && state.ended) && !$("drawer").classList.contains("on")) $("scrim").classList.remove("on");
}

function filterBusy(on) {
  for (const id of ["filterSave", "filterPreview", "filterDelete"]) $(id).disabled = on;
}

function filterMessage(text, ok) {
  $("filterTest").className = "test-result " + (ok ? "ok" : "bad");
  $("filterTest").textContent = text;
}

$("addFilter").onclick = () => openFilterEditor(null);
$("editFilter").onclick = () => openFilterEditor(activeFilter);
$("filterCancel").onclick = closeFilterEditor;
$("filterPreview").onclick = async () => {
  filterBusy(true);
  filterMessage(t("main.filters.asking"), true);
  const r = await api.preview_filter($("f_query").value);
  filterBusy(false);
  if (r.ok) filterMessage(t("main.filters.matches", { n: r.count }), true);
  else filterMessage(r.error, false);
};
$("filterForm").onsubmit = async (e) => {
  e.preventDefault();
  filterBusy(true);
  filterMessage(t("main.filters.checking"), true);
  const r = await api.save_filter(editing, $("f_name").value, $("f_query").value);
  filterBusy(false);
  if (!r.ok) return filterMessage(r.error, false);
  ++loadSeq;  // this answer already carries the new list
  closeFilterEditor();
  sel = 0;
  applyTaskResult(r);
  toast(t("main.filters.saved", { name: r.active }));
};
$("filterDelete").onclick = async () => {
  if (!confirm(t("main.filters.delete_confirm", { name: editing }))) return;
  filterBusy(true);
  const r = await api.delete_filter(editing);
  filterBusy(false);
  if (!r.ok) return filterMessage(r.error, false);
  ++loadSeq;
  closeFilterEditor();
  applyTaskResult(r);
  toast(t("main.filters.deleted"));
};
async function moveFilter(delta) {
  const r = await api.move_filter(editing, delta);
  if (!r.ok) return filterMessage(r.error, false);
  filters = r.filters;
  renderFilters();
}
$("filterLeft").onclick = () => moveFilter(-1);
$("filterRight").onclick = () => moveFilter(1);
$("filterTabs").addEventListener("wheel", (e) => {
  // a vertical wheel scrolls the tabs sideways
  if (e.deltaY) { e.preventDefault(); $("filterTabs").scrollLeft += e.deltaY; }
}, { passive: false });

function showEmpty(title, text, withSettings) {
  const list = $("list");
  list.textContent = "";
  const li = el("li", "empty");
  li.append(el("b", "", title), el("span", "", text));
  if (withSettings) {
    const b = el("button", "btn", t("main.tasks.open_settings"));
    b.style.marginTop = "14px";
    b.onclick = () => openSettings("todoist");
    li.append(el("br"), b);
  }
  list.append(li);
  $("count").textContent = "";
}

function renderTasks() {
  const q = $("search").value.trim().toLowerCase();
  shown = !q ? tasks : tasks.filter((task) =>
    [task.content, task.parent, task.project, ...task.labels].join(" ").toLowerCase().includes(q));
  sel = Math.min(sel, Math.max(0, shown.length - 1));
  // The filter's Todoist durations added up (tasks without one count nothing).
  const planned = tasks.reduce((sum, task) => sum + (task.minutes || 0), 0);
  $("count").textContent = planned
    ? t("main.tasks.count_planned", { shown: shown.length, total: tasks.length, duration: duration(planned) })
    : t("main.tasks.count", { shown: shown.length, total: tasks.length });
  $("count").title = planned ? t("main.tasks.count_title", { duration: duration(planned) }) : "";
  const list = $("list");
  list.textContent = "";
  if (!tasks.length) return showEmpty(t("main.tasks.empty"), t("main.tasks.empty_text"));
  if (!shown.length) return showEmpty(t("main.tasks.no_match"), t("main.tasks.no_match_text", { text: $("search").value }));

  shown.forEach((task, i) => {
    const li = el("li", "task");
    li.dataset.id = task.id;
    const body = el("div", "t-body");
    const title = el("div", "t-title");
    // A subtask carries its parent's name in front: "parent › subtask".
    if (task.parent) title.append(el("span", "t-parent", task.parent + " › "));
    title.append(task.content);
    title.title = task.parent ? `${task.parent} › ${task.content}` : task.content;
    body.append(title);
    if (task.description) body.append(taskDescription(task));
    const meta = el("div", "t-meta");
    if (task.project && !task.inbox) {
      const proj = el("span", "proj");
      const sw = el("i");
      sw.style.background = task.color;
      proj.append(sw, task.project);
      meta.append(proj);
    }
    const due = dueText(task);
    if (due) meta.append(el("span", task.overdue ? "late" : "", due));
    if (task.plan) {
      // Its Todoist duration sets the pomodoros (buzyist/plan.py).
      const [mins, rounds] = task.plan;
      const chip = el("span", "plan");
      chip.title = t("main.tasks.plan_title", { minutes: task.minutes, n: rounds, work: mins });
      chip.append(icon("recent"), t("main.tasks.plan", { n: rounds, work: mins }));
      meta.append(chip);
    }
    for (const l of task.labels) meta.append(el("span", "lbl" + (l === state?.label ? " mine" : ""), "@" + l));
    if (task.subtasks && task.subtasks.length) {
      // Play asks which one to start on (openSubtaskPicker).
      const chip = el("span", "subs", t("main.tasks.subtasks", { n: task.subtasks.length }));
      chip.title = t("main.tasks.subtasks_title");
      meta.append(chip);
      li.dataset.subs = task.subtasks.map((s) => s.id).join(" ");
    }
    const dots = el("span", "t-dots");
    meta.prepend(dots);
    // Priority: a pill (filled flag, P1-P3 and a word) first in the meta line,
    // and the checkbox takes the same colour, like in Todoist.
    const prio = prioClass(task.priority);
    if (prio) {
      li.classList.add(prio);
      const pill = el("span", "prio-pill " + prio);
      pill.title = t("main.tasks.priority_title", { p: prio.slice(1) });
      pill.append(icon("flag-fill"), el("b", "", prio.toUpperCase()), t("main.tasks.priority." + prio));
      meta.prepend(pill);
    }
    body.append(meta);
    const play = el("button", "play");
    play.append(icon("play"));
    play.title = t("main.tasks.start");
    play.setAttribute("aria-label", t("main.tasks.start_on", { task: task.content }));
    play.tabIndex = -1;
    const check = el("button", "prio");
    check.title = t("main.tasks.done");
    check.setAttribute("aria-label", prio ? t("main.tasks.done_on_priority", { task: task.content, p: prio.slice(1) })
      : t("main.tasks.done_on", { task: task.content }));
    check.tabIndex = -1;
    const box = el("span", "box");
    box.append(icon("check"));
    check.append(box);
    check.onclick = (e) => { e.stopPropagation(); completeTask(task, li); };
    li.append(check, body, play);
    play.onclick = (e) => { e.stopPropagation(); startTask(task, li, e.shiftKey); };
    li.onclick = (e) => { sel = i; markSelection(false); openTaskMenu(task, li, e.clientX, e.clientY); };
    li.oncontextmenu = (e) => {
      e.preventDefault();
      sel = i;
      markSelection(false);
      openTaskMenu(task, li, e.clientX, e.clientY);
    };
    li.onmousemove = () => { if (sel !== i) { sel = i; markSelection(false); } };
    list.append(li);
  });
  markSelection(false);
  markCurrentTask();
}

// The description under the title, cut to two lines; "more" opens it (the row's
// click still opens the task menu, so the toggle stops it).
const openDescriptions = new Set();
function taskDescription(task) {
  const wrap = el("div", "t-desc");
  const text = el("div", "t-desc-text", task.description);
  const more = el("button", "t-more");
  more.type = "button";
  more.tabIndex = -1;
  const set = (open) => {
    wrap.classList.toggle("open", open);
    more.textContent = t(open ? "main.tasks.less" : "main.tasks.more");
    more.setAttribute("aria-expanded", open);
  };
  more.onclick = (e) => {
    e.stopPropagation();
    const open = !wrap.classList.contains("open");
    if (open) openDescriptions.add(task.id); else openDescriptions.delete(task.id);
    set(open);
  };
  set(openDescriptions.has(task.id));
  wrap.append(text, more);
  // Only long texts get the toggle: measured once the row is on the page.
  requestAnimationFrame(() => {
    more.hidden = !wrap.classList.contains("open") && text.scrollHeight <= text.clientHeight + 1;
  });
  return wrap;
}

function markSelection(scroll) {
  [...$("list").querySelectorAll(".task")].forEach((li, i) => {
    li.classList.toggle("sel", i === sel);
    if (i === sel && scroll) li.scrollIntoView({ block: "nearest" });
  });
}

// The task in focus gets a Tomate border and a state badge; every row shows
// one small tomato per pomodoro done on it.
function markCurrentTask() {
  const id = state && state.session && state.session.task.id;
  const tm = state && state.timer;
  const word = t("main.tasks.badge." + (!tm ? "wrapping_up" : tm.phase === "call" ? "call" : tm.paused ? "paused"
    : tm.phase === "rest" ? "break" : "focus"));
  const counts = (state && state.task_tomatoes) || {};
  for (const li of $("list").querySelectorAll(".task")) {
    // a session on one of its subtasks marks the row too
    const on = !!id && (li.dataset.id === id || (li.dataset.subs || "").split(" ").includes(id));
    li.classList.toggle("in-focus", on);
    let badge = li.querySelector(".badge");
    if (on && !badge) {
      badge = el("span", "badge", word);
      li.insertBefore(badge, li.querySelector(".play"));
    } else if (!on && badge) {
      badge.remove();
    }
    if (badge && badge.textContent !== word) badge.textContent = word;
    const dots = li.querySelector(".t-dots");
    const n = counts[li.dataset.id] || 0;
    if (dots && dots.dataset.n !== String(n)) {
      dots.dataset.n = String(n);
      dots.textContent = "";
      for (let k = 0; k < Math.min(n, 8); k++) dots.append(el("i"));
      if (n > 8) dots.append(el("span", "", "+" + (n - 8)));
      dots.classList.toggle("hidden", !n);
      dots.setAttribute("role", "img");
      dots.setAttribute("aria-label", t("main.tasks.pomodoros_done", { n }));
    }
  }
}

// fixed: the usual pomodoro settings even when the task's duration would set them.
// A task with open subtasks first asks which one (direct: start this one, no asking;
// at: where to open the question, see openSubtaskPicker).
async function startTask(task, li, fixed, direct, at) {
  if (!direct && task.subtasks && task.subtasks.length) return openSubtaskPicker(task, li, fixed, at);
  if (state && state.session && state.session.task.id === task.id) {
    toast(t("main.start.already"));
    return;
  }
  if (state && state.session && !confirm(t("main.start.replace", { task: state.session.task.content }))) return;
  if (li) li.classList.add("busy");
  flushPomodoro();
  const result = await api.start(task.id, "", "", !!fixed);
  if (li) li.classList.remove("busy");
  if (!result.ok) return toast(result.error, true);
  const onBar = !(state && (state.bar.local || state.bar.offline));
  toast(result.planned
    ? t(onBar ? "main.start.go_planned_bar" : "main.start.go_planned", { n: result.rounds, work: result.work_minutes })
    : t(onBar ? "main.start.go_bar" : "main.start.go", { work: result.work_minutes }));
  $("search").value = "";
  renderTasks();
  poll();
}

async function completeTask(task, li) {
  const inFocus = state && state.session && state.session.task.id === task.id;
  if (inFocus && !confirm(t("main.tasks.complete_in_focus", { task: task.content }))) return;
  if (li) li.classList.add("busy", "done");
  if (inFocus) {
    const stopped = await api.control("stop");
    if (!stopped.ok) { if (li) li.classList.remove("busy", "done"); return toast(stopped.error, true); }
  }
  const result = await api.complete(task.id);
  if (!result.ok) {
    if (li) li.classList.remove("busy", "done");
    return toast(result.error, true);
  }
  toast(t("main.tasks.completed"));
  tasks = tasks.filter((x) => x.id !== task.id);
  renderTasks();
  poll();
}

// ------------------------------------------------------------ task menu

function closeTaskMenu() {
  const m = $("taskMenu");
  if (m) m.remove();
}

function openTaskMenu(task, li, x, y) {
  closeTaskMenu();
  const menu = el("div", "ctx-menu");
  menu.id = "taskMenu";
  menu.setAttribute("role", "menu");
  const item = (ic, label, fn) => {
    const b = el("button", "ctx-item");
    b.setAttribute("role", "menuitem");
    b.append(icon(ic), label);
    b.onclick = (e) => { e.stopPropagation(); closeTaskMenu(); fn(); };
    menu.append(b);
  };
  item("play", t("main.menu.start"), () => startTask(task, li, false, false, { x, y }));
  if (task.plan || task.subtasks?.length) {
    item("play", t("main.menu.start_usual", { work: pomo.work_minutes }), () => startTask(task, li, true, false, { x, y }));
  }
  item("check", t("main.menu.done"), () => completeTask(task, li));
  if (task.url) item("eye", t("main.menu.todoist"), () => api.open_url(task.url));
  document.body.append(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 4)) + "px";
  menu.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 4)) + "px";
  menu.querySelector("button").focus();
}

// "Start on…": the task's open subtasks (all depths, indented, in Todoist's order), then the
// task itself. Shares #taskMenu with the task menu, so its keys and closing work the same.
// at: {x, y} to open there; else under the row's play button.
function openSubtaskPicker(task, li, fixed, at) {
  closeTaskMenu();
  const menu = el("div", "ctx-menu pick-menu");
  menu.id = "taskMenu";
  menu.setAttribute("role", "menu");
  menu.setAttribute("aria-label", t("main.picker.aria"));
  menu.append(el("div", "pick-head", t("main.picker.head")));
  const item = (one, depth, label) => {
    const b = el("button", "ctx-item pick-item");
    b.setAttribute("role", "menuitem");
    b.style.paddingLeft = 12 + (depth - 1) * 16 + "px";
    b.append(el("span", "pick-name", label || one.content));
    // the pomodoros it would get (unless shift+click asked for the usual ones)
    if (one.plan && !fixed) b.append(el("span", "pick-plan", t("main.picker.plan", { n: one.plan[1], work: one.plan[0] })));
    b.title = one.content;
    b.onclick = (e) => { e.stopPropagation(); closeTaskMenu(); startTask(one, li, fixed, true); };
    menu.append(b);
  };
  for (const s of task.subtasks) item(s, s.depth);
  menu.append(el("div", "pick-sep"));
  item(task, 1, t("main.picker.whole"));
  document.body.append(menu);
  const r = menu.getBoundingClientRect();
  const play = li && li.querySelector(".play");
  const anchor = play ? play.getBoundingClientRect() : null;
  const x = at ? at.x : anchor ? anchor.right - r.width : 4;
  // under the play button, or above it when there is no room below
  let y = at ? at.y : anchor ? anchor.bottom + 4 : 4;
  if (!at && anchor && y + r.height > innerHeight - 4) y = anchor.top - r.height - 4;
  menu.style.left = Math.max(4, Math.min(x, innerWidth - r.width - 4)) + "px";
  menu.style.top = Math.max(4, Math.min(y, innerHeight - r.height - 4)) + "px";
  menu.querySelector("button").focus();
}

// A task of the list or one of its subtasks, by id.
function findTask(id) {
  for (const t of tasks) {
    if (t.id === id) return t;
    const sub = (t.subtasks || []).find((s) => s.id === id);
    if (sub) return sub;
  }
  return null;
}

document.addEventListener("mousedown", (e) => { if (!e.target.closest("#taskMenu")) closeTaskMenu(); });
document.addEventListener("contextmenu", (e) => { if (!e.target.closest(".task")) closeTaskMenu(); });
window.addEventListener("blur", closeTaskMenu);
window.addEventListener("resize", closeTaskMenu);
document.addEventListener("scroll", closeTaskMenu, true);

// ------------------------------------------------------------ pomodoro

function loadPomodoro(p) {
  pomo = { work_minutes: p.work_minutes, rest_minutes: p.rest_minutes, cycles: p.cycles, autostart: p.autostart };
  renderPomodoro();
}

function renderPomodoro() {
  for (const box of document.querySelectorAll(".stepper")) {
    const input = box.querySelector("input");
    if (document.activeElement !== input) input.value = pomo[box.dataset.key];
  }
  $("autostart").checked = pomo.autostart;
  const presets = $("presets");
  presets.textContent = "";
  for (const p of PRESETS) {
    const on = p.work_minutes === pomo.work_minutes && p.rest_minutes === pomo.rest_minutes && p.cycles === pomo.cycles;
    const b = el("button", "preset" + (on ? " on" : ""), `${p.work_minutes} / ${p.rest_minutes}`);
    b.title = t("main.setup.preset", {
      name: t("main.setup.presets." + p.name), cycles: p.cycles, work: p.work_minutes, rest: p.rest_minutes,
    });
    b.onclick = () => {
      Object.assign(pomo, { work_minutes: p.work_minutes, rest_minutes: p.rest_minutes, cycles: p.cycles });
      changed();
    };
    presets.append(b);
  }
  const minutes = pomo.cycles * pomo.work_minutes + (pomo.cycles - 1) * pomo.rest_minutes;
  const end = formatTime(new Date(Date.now() + minutes * 60000), { hour: "2-digit", minute: "2-digit" });
  $("total").textContent = t("main.setup.total", { duration: duration(minutes), time: end });
  if (state) renderTimer();
}

function changed() {
  renderPomodoro();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(savePomodoro, 500);
}

async function savePomodoro() {
  saveTimer = null;
  const result = await api.set_pomodoro(pomo.work_minutes, pomo.rest_minutes, pomo.cycles, pomo.autostart);
  if (!result.ok) toast(result.error, true);
}

function flushPomodoro() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    savePomodoro();
  }
}

function clampStepper(box, value) {
  const min = +box.dataset.min, max = +box.dataset.max;
  return Math.max(min, Math.min(max, Math.round(value)));
}

for (const box of document.querySelectorAll(".stepper")) {
  const key = box.dataset.key, step = +box.dataset.step;
  const input = box.querySelector("input");
  for (const b of box.querySelectorAll("button")) {
    b.onclick = () => {
      const d = +b.dataset.d;
      let v = pomo[key] + d * step;
      // snap to the step grid so 27 + 5 goes to 30, not 32
      if (step > 1) v = d > 0 ? Math.floor(pomo[key] / step) * step + step : Math.ceil(pomo[key] / step) * step - step;
      pomo[key] = clampStepper(box, v);
      changed();
    };
  }
  input.addEventListener("change", () => {
    const v = parseInt(input.value, 10);
    pomo[key] = isNaN(v) ? pomo[key] : clampStepper(box, v);
    input.value = pomo[key];
    changed();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") input.blur();
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      box.querySelector(`button[data-d="${e.key === "ArrowUp" ? 1 : -1}"]`).click();
    }
    e.stopPropagation();
  });
  input.addEventListener("wheel", (e) => {
    e.preventDefault();
    box.querySelector(`button[data-d="${e.deltaY < 0 ? 1 : -1}"]`).click();
  }, { passive: false });
}
$("autostart").onchange = () => { pomo.autostart = $("autostart").checked; changed(); };

// ------------------------------------------------------------- controls

async function control(action) {
  const result = await api.control(action);
  if (!result.ok) toast(result.error, true);
  poll();
}
$("btnStop").onclick = () => control("stop");
$("guard").onclick = () => openSettings($("guard").dataset.tab || "apps");
$("btnSkip").onclick = () => control("skip");
$("btnPause").onclick = () => control(state.timer && state.timer.paused ? "resume" : "pause");

// ------------------------------------------------- what are you working on

// The free field (a session with no task) is a combobox. Focusing it lists
// the activities (state.activities, edited in Settings, General) and the
// latest names used (Api.recent_free). Picking an activity turns it into a
// chip in the field, and what you type after it is the detail ("Email ·
// Q4 budget"). Text that matches no activity is a name of its own, and
// "call: Ana" (an activity's name, then ":") picks the activity as you type.
// The list is position: fixed, because the timer panel scrolls and would cut it.
const ACTIVITY_ICONS = ["mail", "chat", "phone", "code", "book", "pen", "people", "paint", "bulb", "wrench", "chart", "dot"];  // as in buzyist/activities.py

const free = { activity: null, recent: [], options: [], active: -1, open: false };
const activities = () => (state && state.activities) || [];

function activityIcon(name) {
  const svg = icon("x");
  svg.firstChild.setAttribute("href", "#a-" + (ACTIVITY_ICONS.includes(name) ? name : "dot"));
  return svg;
}

function setFreeActivity(activity) {
  free.activity = activity ? { ...activity } : null;
  $("freeChip").classList.toggle("hidden", !activity);
  $("freeField").classList.toggle("has-chip", !!activity);
  if (activity) {
    $("freeChipIcon").setAttribute("href", "#a-" + (ACTIVITY_ICONS.includes(activity.icon) ? activity.icon : "dot"));
    $("freeChipName").textContent = activity.name;
  }
  $("freeName").placeholder = t(activity ? "main.free.detail" : "main.free.placeholder");
}

// An activity renamed, given another icon or removed in Settings meanwhile.
function syncFreeActivity() {
  if (!free.activity) return;
  const now = activities().find((a) => a.id === free.activity.id);
  if (!now) setFreeActivity(null);
  else if (now.name !== free.activity.name || now.icon !== free.activity.icon) setFreeActivity(now);
}

function recentText(r) {
  const a = r.activity && activities().find((x) => x.id === r.activity);
  return a ? `${a.name} · ${r.detail}` : r.detail;
}

function freeOptions() {
  const typed = $("freeName").value.trim();
  const text = typed.toLowerCase();
  const out = [];
  if (free.activity) {
    // With a chip: this activity's recent details, or another activity.
    for (const r of free.recent) {
      const detail = r.detail.toLowerCase();
      if (r.activity === free.activity.id && detail.includes(text) && detail !== text) {
        out.push({ kind: "recent", group: t("main.free.recent_of", { activity: free.activity.name }), recent: r });
      }
    }
    if (!text) {
      for (const a of activities()) {
        if (a.id !== free.activity.id) out.push({ kind: "activity", group: t("main.free.switch_to"), activity: a });
      }
    }
    return out;
  }
  const starts = (s) => s.toLowerCase().startsWith(text);
  const found = activities().filter((a) => a.name.toLowerCase().includes(text));
  found.sort((a, b) => starts(b.name) - starts(a.name));
  for (const a of found) out.push({ kind: "activity", group: t("main.free.activities"), activity: a });
  for (const r of free.recent) {
    const shown = recentText(r).toLowerCase();
    if (shown.includes(text) && shown !== text) out.push({ kind: "recent", group: t("main.free.recent"), recent: r });
  }
  if (typed && !activities().some((a) => a.name.toLowerCase() === text)) out.push({ kind: "custom", group: "", text: typed });
  return out;
}

function renderFreeList() {
  const list = $("freeList");
  free.options = freeOptions();
  if (!free.open || !free.options.length) {
    list.classList.add("hidden");
    $("freeCombo").classList.remove("open");
    $("freeName").setAttribute("aria-expanded", "false");
    $("freeName").removeAttribute("aria-activedescendant");
    return;
  }
  // Enter takes the highlighted option: an activity the text starts with,
  // or (nothing else matching) the text as typed. With nothing typed, or
  // after a chip, nothing is highlighted and Enter starts the session.
  const text = $("freeName").value.trim().toLowerCase();
  if (free.active === -2) {
    free.active = text && !free.activity
      ? free.options.findIndex((o) => (o.kind === "activity" && o.activity.name.toLowerCase().startsWith(text)) || o.kind === "custom")
      : -1;
  }
  if (free.active >= free.options.length) free.active = -1;
  const nodes = [];
  let group = null;
  free.options.forEach((o, i) => {
    if (o.group && o.group !== group) nodes.push(el("div", "combo-head", o.group));
    group = o.group;
    const opt = el("div", "combo-opt");
    opt.id = "freeOpt" + i;
    opt.setAttribute("role", "option");
    if (o.kind === "activity") {
      opt.append(activityIcon(o.activity.icon), el("span", "", o.activity.name));
    } else if (o.kind === "recent") {
      const a = o.recent.activity && activities().find((x) => x.id === o.recent.activity);
      opt.append(icon("recent"));
      if (a && !free.activity) opt.append(el("span", "", a.name), el("span", "sub", o.recent.detail));
      else opt.append(el("span", "grow", o.recent.detail));
    } else {
      opt.classList.add("custom");
      opt.append(icon("play"), el("span", "grow", t("main.free.custom", { text: o.text })));
    }
    opt.onmousedown = (e) => e.preventDefault();  // keep the focus in the field
    opt.onclick = () => chooseFree(o);
    opt.onmousemove = () => { if (free.active !== i) { free.active = i; markFreeActive(); } };
    nodes.push(opt);
  });
  const edit = el("button", "combo-edit", t("main.free.edit"));
  edit.type = "button";
  edit.tabIndex = -1;
  edit.onmousedown = (e) => e.preventDefault();
  edit.onclick = () => { closeFreeList(); openSettings("general"); };
  nodes.push(edit);
  list.replaceChildren(...nodes);
  list.classList.remove("hidden");
  $("freeCombo").classList.add("open");
  $("freeName").setAttribute("aria-expanded", "true");
  placeFreeList();
  markFreeActive();
}

function markFreeActive() {
  for (const n of $("freeList").querySelectorAll(".combo-opt")) {
    const on = n.id === "freeOpt" + free.active;
    n.classList.toggle("on", on);
    n.setAttribute("aria-selected", on);
    if (on) n.scrollIntoView({ block: "nearest" });
  }
  if (free.active >= 0) $("freeName").setAttribute("aria-activedescendant", "freeOpt" + free.active);
  else $("freeName").removeAttribute("aria-activedescendant");
}

// Under the field, or above it when the window has more room there.
function placeFreeList() {
  const list = $("freeList"), r = $("freeField").getBoundingClientRect();
  const below = window.innerHeight - r.bottom - 12, above = r.top - 12;
  const up = below < 220 && above > below;
  list.style.left = r.left + "px";
  list.style.width = r.width + "px";
  list.style.maxHeight = Math.max(120, Math.min(300, up ? above : below)) + "px";
  list.style.top = up ? "" : r.bottom + 6 + "px";
  list.style.bottom = up ? window.innerHeight - r.top + 6 + "px" : "";
}

function refreshFreeList() {
  free.open = true;
  free.active = -2;  // renderFreeList picks the default
  renderFreeList();
}

async function openFreeList() {
  refreshFreeList();
  const result = await api.recent_free();
  if (!result.ok || !free.open) return;
  free.recent = result.recent;
  const keep = free.active;
  free.active = keep < 0 ? -2 : keep;
  renderFreeList();
}

function closeFreeList() {
  if (!free.open) return;
  free.open = false;
  renderFreeList();
}

function chooseFree(o) {
  const input = $("freeName");
  if (o.kind === "custom") { closeFreeList(); return startFree(); }
  if (o.kind === "activity") {
    if (!free.activity) input.value = "";  // what was typed only looked for it
    setFreeActivity(o.activity);
  } else {
    const a = o.recent.activity && activities().find((x) => x.id === o.recent.activity);
    setFreeActivity(a || null);
    input.value = o.recent.detail;
  }
  closeFreeList();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
}

async function startFree(name, activityId) {
  if (typeof name !== "string") {
    name = $("freeName").value;
    activityId = free.activity ? free.activity.id : "";
  }
  if (state && state.session && !confirm(t("main.start.replace", { task: state.session.task.content }))) return;
  flushPomodoro();
  const result = await api.start(null, name, activityId || "");
  if (!result.ok) return toast(result.error, true);
  const onBar = !(state && (state.bar.local || state.bar.offline));
  toast(t(onBar ? "main.start.go_bar" : "main.start.go", { work: pomo.work_minutes }));
  $("freeName").value = "";
  setFreeActivity(null);
  closeFreeList();
  poll();
}
$("startFree").onclick = () => startFree();

$("freeName").addEventListener("focus", () => { if (!free.open) openFreeList(); });
$("freeName").addEventListener("input", () => {
  const input = $("freeName");
  if (!free.activity) {
    // "call: Ana" or "Call · Ana": the activity, then the detail.
    const m = input.value.match(/^\s*([^:·]+?)\s*[:·]\s*(.*)$/);
    const a = m && activities().find((x) => x.name.toLowerCase() === m[1].toLowerCase());
    if (a) { setFreeActivity(a); input.value = m[2]; }
  }
  refreshFreeList();
});
$("freeName").addEventListener("keydown", (e) => {
  const input = $("freeName");
  const n = free.options.length;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    e.stopPropagation();  // not the task list's selection
    if (!free.open) return openFreeList();
    if (!n) return;
    const step = e.key === "ArrowDown" ? 1 : -1;
    free.active = free.active < 0 ? (step > 0 ? 0 : n - 1) : (free.active + step + n) % n;
    markFreeActive();
  } else if (e.key === "Enter") {
    e.preventDefault();
    e.stopPropagation();
    if (free.open && free.active >= 0 && free.options[free.active]) chooseFree(free.options[free.active]);
    else startFree();
  } else if (e.key === "Escape" && free.open && n) {
    e.stopPropagation();  // not "hide the window"
    closeFreeList();
  } else if (e.key === "Backspace" && free.activity && input.selectionStart === 0 && input.selectionEnd === 0) {
    e.preventDefault();
    setFreeActivity(null);
    refreshFreeList();
  } else if (e.key === "Tab") {
    closeFreeList();
  }
});
$("freeCombo").addEventListener("focusout", (e) => { if (!$("freeCombo").contains(e.relatedTarget)) closeFreeList(); });
$("freeField").addEventListener("mousedown", (e) => {
  if (e.target === $("freeName")) { if (!free.open) openFreeList(); return; }
  e.preventDefault();  // the chip and the arrow keep the focus in the field
  $("freeName").focus();
  if (e.target.closest("#freeChipX")) {
    setFreeActivity(null);
    refreshFreeList();
  } else if (e.target.closest("#freeToggle")) {
    if (free.open) closeFreeList();
    else openFreeList();
  } else if (!free.open) {
    openFreeList();
  }
});
document.querySelector(".panel.focus").addEventListener("scroll", () => { if (free.open) placeFreeList(); });
window.addEventListener("resize", () => { if (free.open) placeFreeList(); });

$("endedClose").onclick = async () => { await api.dismiss_ended(); poll(); };
$("endedComplete").onclick = async () => {
  const e = state.ended;
  const result = await api.complete(e.task.id, true);
  if (!result.ok) return toast(result.error, true);
  toast(t("summary.completed"));
  // drop it at once, then reload so the filter shows what Todoist now has
  const gone = new Set([e.task.id]);
  const dropGone = () => {
    tasks = tasks.filter((t) => !gone.has(t.id))
      .map((t) => t.subtasks ? { ...t, subtasks: t.subtasks.filter((s) => !gone.has(s.id)) } : t);
    renderTasks();
  };
  dropGone();
  await poll();
  // That was the last open subtask: offer to close its parent too.
  const parent = result.parent;
  if (parent && confirm(t("summary.parent_confirm", { parent: parent.content || t("summary.parent_unnamed") }))) {
    const closed = await api.complete(parent.id, false, parent.content);
    if (!closed.ok) toast(closed.error, true);
    else { toast(t("summary.both_completed")); gone.add(parent.id); dropGone(); }
  }
  loadTasks(true);
};
$("endedAgain").onclick = async () => {
  const e = state.ended;
  const task = findTask(e.task.id);
  await api.dismiss_ended();
  if (!e.task.id) return e.activity ? startFree(e.activity.detail, e.activity.id) : startFree(e.task.content);
  if (!task) return toast(t("summary.task_gone"), true);
  startTask(task, null, false, true);  // the same one again, no picker
};

// ------------------------------------------------------------- settings

// ---------------------------------------------------------- settings tabs

const TABS = [...document.querySelectorAll("#settingsTabs .stab")].map((b) => b.dataset.tab);
let settingsTab = localStorage.getItem("settingsTab") || "todoist";

function showTab(name, focus) {
  if (!TABS.includes(name)) name = "todoist";
  settingsTab = name;
  localStorage.setItem("settingsTab", name);
  for (const b of document.querySelectorAll("#settingsTabs .stab")) {
    const on = b.dataset.tab === name;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on);
    b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  }
  for (const p of document.querySelectorAll("[data-panel]")) p.classList.toggle("hidden", p.dataset.panel !== name);
  $("settingsForm").scrollTop = 0;
  if (name === "stats" && api) loadStats();
}

// ------------------------------------------------------------- stats tab

// The three sections for one period (Api.stats, App.stats): its `span` (the
// stats_span setting, the picker at the top) and `statsOffset`, how many
// periods back from today (the arrows; not saved). Python keeps the offset
// within the data, and the answer says whether there is anything older.
let statsOffset = 0, statsAsked = 0;
async function loadStats() {
  const asked = ++statsAsked;  // a quick second click wins over a slow first answer
  const picked = formValues(["stats_span"]).stats_span;
  const s = await api.stats(picked ? Number(picked) : null, statsOffset);
  if (asked !== statsAsked) return;
  if (!s.ok) return toast(s.error, true);
  statsOffset = s.range.offset;
  renderRange(s.range);
  renderFocusStats(s.focus);
  renderAppTime(s.apps);
  renderStats(s.blocks);
}

for (const radio of document.querySelectorAll('input[name="stats_span"]')) {
  radio.onchange = async () => {
    const result = await api.save_settings(formValues(["stats_span"]));
    if (!result.ok) return toast(result.error, true);
    statsOffset = 0;
    loadStats();
  };
}
$("statsOlder").onclick = () => { statsOffset += 1; loadStats(); };
$("statsNewer").onclick = () => { statsOffset = Math.max(0, statsOffset - 1); loadStats(); };

// "Mar 2 – Mar 31" between the arrows, with the year when it isn't this one;
// a single day is "Today" or "Wed, Oct 14", and has no charts (.one-day).
function renderRange(r) {
  const thisYear = String(new Date().getFullYear());
  const withYear = r.first.slice(0, 4) !== thisYear || r.last.slice(0, 4) !== thisYear;
  const day = (iso, weekday) => formatDate(new Date(iso + "T12:00"),
    { ...(weekday ? { weekday: "short" } : {}), day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) });
  $("statsRange").textContent = r.span === 1 ? (r.offset ? day(r.last, true) : t("stats.today"))
    : t("stats.range.label", { first: day(r.first), last: day(r.last) });
  $("p_stats").classList.toggle("one-day", r.span === 1);
  for (const radio of document.querySelectorAll('input[name="stats_span"]')) radio.checked = Number(radio.value) === r.span;
  const older = $("statsOlder"), newer = $("statsNewer");
  older.disabled = !r.has_older;
  newer.disabled = r.offset === 0;
  // An arrow that just went off would drop the keyboard focus: hand it to the other one.
  if (older.disabled && document.activeElement === older && !newer.disabled) newer.focus();
  if (newer.disabled && document.activeElement === newer && !older.disabled) older.focus();
}

// Saved at once too; the limiter thread starts or stops counting straight away.
$("s_app_time_on").onchange = async () => {
  const result = await api.save_settings(formValues(["app_time_on"]));
  if (!result.ok) return toast(result.error, true);
  loadStats();
};

// Time in every app (App.app_time_stats, from app_time.json): totals, one
// stacked bar per day (during focus below, outside above), the apps by time.
let appTimeAll = false, siteTimeAll = false;  // "Show all" pressed
$("appTimeMore").onclick = () => { appTimeAll = true; loadStats(); };
$("siteTimeMore").onclick = () => { siteTimeAll = true; loadStats(); };
function renderAppTime(s) {
  $("appTimeBody").classList.toggle("muted", !s.on);
  putTiles($("appTimeTiles"), statTiles(s).map(([label, tot, kind]) =>
    statTile(label, duration(Math.round(tot.focus + tot.other)), kind === "average"
      ? [t("stats.tiles.per_day")]
      : [t("stats.apps.during_focus", { duration: duration(tot.focus) }), ...changeLine(s, kind)])));

  barChart($("appTimeChart"), s.bars, [["focus", "stat-f"], ["other", "stat-l"]],
    t("stats.apps.chart", { ...rangeDays(s), duration: duration(s.totals.range.focus + s.totals.range.other) }),
    { line: (d) => d.focus + d.other ? t("stats.apps.tip", { focus: duration(d.focus), other: duration(d.other) }) : t("stats.apps.tip_none"), max: duration, today: !s.offset });
  axisEnds(s, "appTimeFirst", "appTimeLast");

  timeRows($("appTimeApps"), $("appTimeMore"), s.apps, appTimeAll, "stats.apps.show_all_apps", (a) => a.key + ".exe",
    t(s.on ? "stats.apps.apps_empty" : "stats.apps.apps_off"));
  timeRows($("siteTimeSites"), $("siteTimeMore"), s.sites || [], siteTimeAll, "stats.apps.show_all_sites", (a) => a.key,
    t(!s.on ? "stats.apps.sites_off" : s.extension ? "stats.apps.sites_empty" : "stats.apps.sites_no_extension"));
}

// The 8 rows with the most time (all of them once "Show all" was pressed);
// `moreKey` is the "Show all" button's text. A row has `today` only when
// the period ends today.
function timeRows(list, more, rows, all, moreKey, title, empty) {
  const SHOWN = 8;
  const shown = all ? rows : rows.slice(0, SHOWN);
  list.replaceChildren(...(shown.length ? shown.map((a) => {
    const row = el("div", "stat-site");
    const share = percent(a.range ? a.focus / a.range : 0);
    row.append(el("span", "stat-host", a.name),
               el("span", "help", a.today == null ? t("stats.apps.row_past", { share }) : t("stats.apps.row", { today: duration(a.today), share })),
               el("b", "", duration(a.range)));
    row.title = title(a);
    return row;
  }) : [el("div", "help", empty)]));
  more.classList.toggle("hidden", all || rows.length <= SHOWN);
  more.textContent = t(moreKey, { n: rows.length });
}

// Saved at once, like the streak's switch; the limiter thread starts or stops counting straight away.
$("s_pc_time_on").onchange = async () => {
  const result = await api.save_settings(formValues(["pc_time_on"]));
  if (!result.ok) return toast(result.error, true);
  loadStats();
};

// Saved at once (not with Save), so the streak above changes as you click.
$("s_streak_skip_weekends").onchange = async () => {
  const result = await api.save_settings(formValues(["streak_skip_weekends"]));
  if (!result.ok) return toast(result.error, true);
  loadStats();
};

// The Stats tab's text in the app language: a share (0..1) as a whole
// percent, and a day ("Sat, Oct 17" / "sáb., 17 de out.").
const percent = (share) => formatNumber(share, { style: "percent", maximumFractionDigits: 0 });
const statDay = (iso) => formatDate(new Date(iso + "T12:00"), { weekday: "short", day: "numeric", month: "short" });

// A section's tiles, [title, totals, kind]: Today, the period and the one
// before while the period ends today; further back, the period, the one
// before and the daily average. A single day has two: it and the day before.
function statTiles(s) {
  if (s.span === 1) {
    return [[s.offset ? statDay(s.last) : t("stats.today"), s.totals.range, "range"],
            [t(s.offset ? "stats.tiles.day_before" : "stats.tiles.yesterday"), s.totals.previous, "previous"]];
  }
  const range = [s.offset ? t("stats.tiles.range") : t("stats.span", { n: s.span }), s.totals.range, "range"];
  const before = [t("stats.tiles.previous", { n: s.span }), s.totals.previous, "previous"];
  if (s.offset) return [range, before, [t("stats.tiles.average"), s.totals.average, "average"]];
  return [[t("stats.today"), s.totals.today, "today"], range, before];
}

// The tiles into their row (two to a row for a single day).
function putTiles(box, tiles) {
  box.replaceChildren(...tiles);
  box.classList.toggle("two", tiles.length === 2);
}

function statTile(label, number, lines) {
  const tile = el("div", "stat-tile");
  tile.append(el("div", "stat-label", label), el("div", "stat-num", number), ...lines.map((line) => el("div", "help", line)));
  return tile;
}

// "+12% on the previous 30 days", under the period's tile; nothing when the
// period before had nothing to compare with.
function changeLine(s, kind) {
  if (kind !== "range" || s.change == null) return [];
  const change = formatNumber(s.change, { style: "percent", maximumFractionDigits: 0, signDisplay: "exceptZero" });
  return [s.span === 1 ? t("stats.change_day", { change }) : t("stats.change", { change, n: s.span })];
}

// A chart's first and last day, for its aria text, and under it (the last
// is "Today" while the period ends today).
const rangeDays = (s) => ({ first: statDay(s.first), last: statDay(s.last) });
function axisEnds(s, first, last) {
  $(first).textContent = statDay(s.first);
  $(last).textContent = s.offset ? statDay(s.last) : t("stats.today");
}

// What a bar covers: its day, "Today", or for a week "Mon, Oct 12 – Today".
function barDay(d, isToday) {
  if (d.days > 1) return t("stats.week_tip", { first: statDay(d.day), last: isToday ? t("stats.today") : statDay(d.last) });
  return isToday ? t("stats.today") : statDay(d.day);
}

// One column per bar (a day, or a week for a year), stacked from `series`
// ([key, css class] pairs, the first at the bottom); `tip.line(bar)` is the
// hover line, `tip.today` whether the last bar holds today, `label` the aria text.
function barChart(chart, bars, series, label, tip) {
  const top = Math.max(1, ...bars.map((d) => series.reduce((sum, [k]) => sum + d[k], 0)));
  const box = el("div", "stat-tip hidden");
  chart.replaceChildren(el("div", "stat-max", tip.max ? tip.max(top) : formatNumber(top)), box);
  chart.setAttribute("aria-label", label);
  bars.forEach((d, i) => {
    const col = el("div", "stat-col");
    for (const [key, cls] of series) {  // column-reverse: the first is at the bottom
      if (!d[key]) continue;
      const seg = el("i", "stat-seg " + cls);
      seg.style.height = (100 * d[key]) / top + "%";
      col.append(seg);
    }
    col.onmouseenter = () => {
      box.replaceChildren(el("b", "", barDay(d, tip.today && i === bars.length - 1)), el("span", "", tip.line(d)));
      box.classList.remove("hidden");
      const x = col.offsetLeft + col.offsetWidth / 2;
      box.style.left = Math.min(Math.max(x, 90), chart.clientWidth - 90) + "px";
    };
    col.onmouseleave = () => box.classList.add("hidden");
    chart.append(col);
  });
}

// A stat tile's "3 pomodoros · 2 sessions": a narrow tile wraps at the dots or
// inside a phrase, never between a number and the word after it.
function keepCounts(text) {
  return text.split(" · ").map((part) => part.replace(/^(\S+) /, "$1 ")).join(" · ");
}

// Focus from history.json (App.focus_stats): focus time, pomodoros, sessions
// and tasks for the period, the streak, minutes per bar, top tasks; and, from
// pc_time.json, the time on the PC outside focus (`outside`, stacked on top).
function renderFocusStats(s) {
  const counts = (c) => t("stats.focus.pomodoros", { n: c.pomodoros }) + " · " + t("stats.focus.sessions", { n: c.sessions });
  const pc = s.pc_on || s.totals.range.outside > 0 || s.totals.previous.outside > 0;  // counted, now or before
  const pcLines = (tot, kind) => {
    if (!pc) return [];
    const lines = [t("stats.focus.outside", { duration: duration(Math.round(tot.outside)) })];
    const all = tot.minutes + tot.outside;
    if (kind === "range" && all) lines.push(t("stats.focus.share", { share: percent(tot.minutes / all) }));
    return lines;
  };
  putTiles($("focusTiles"), statTiles(s).map(([label, tot, kind]) =>
    statTile(label, duration(Math.round(tot.minutes)), kind === "average"
      ? [t("stats.tiles.per_day"), ...pcLines(tot, kind)]
      : [keepCounts(counts(tot)),
         keepCounts(t("stats.focus.tasks", { n: tot.tasks }) + " · " + t("stats.focus.completed", { n: tot.completed })),
         ...pcLines(tot, kind),
         ...changeLine(s, kind)])));
  $("focusLegend").classList.toggle("hidden", !pc);
  const current = t("stats.focus.days", { n: s.streak.current });
  const [before, after] = splitAround(t("stats.focus.streak", { current, best: t("stats.focus.days", { n: s.streak.best }) }), current);
  $("focusStreak").replaceChildren(before, el("b", "", current), after);

  const focusLine = (d) => d.minutes ? duration(d.minutes) + " · " + t("stats.focus.pomodoros", { n: d.pomodoros }) : t("stats.focus.tip_none");
  barChart($("focusChart"), s.bars, pc ? [["minutes", "stat-f"], ["outside", "stat-l"]] : [["minutes", "stat-f"]],
    t("stats.focus.chart", { ...rangeDays(s), duration: duration(s.totals.range.minutes) }),
    { line: (d) => pc && d.outside ? focusLine(d) + " · " + t("stats.focus.outside", { duration: duration(d.outside) }) : focusLine(d),
      max: duration, today: !s.offset });
  axisEnds(s, "focusFirst", "focusLast");

  // One row per activity, with Todoist tasks and typed names ("_task",
  // "_other") as their own rows; hidden until an activity has been used.
  const groups = s.activities || [];
  const used = groups.some((g) => !g.id.startsWith("_"));
  const groupName = (g) => g.id === "_task" ? t("stats.focus.activity_task") : g.id === "_other" ? t("stats.focus.activity_other") : g.name;
  $("focusActivities").replaceChildren(...(used ? groups.map((g) => {
    const row = el("div", "stat-site");
    const name = el("span", "stat-host stat-activity");
    name.append(g.icon ? activityIcon(g.icon) : el("i", "stat-noicon"), groupName(g));
    const share = s.totals.range.minutes ? g.minutes / s.totals.range.minutes : 0;
    row.append(name, el("span", "help", t("stats.focus.pomodoros", { n: g.pomodoros }) + " · " + percent(share)), el("b", "", duration(g.minutes)));
    return row;
  }) : [el("div", "help", t("stats.focus.activities_empty"))]));

  $("focusTasks").replaceChildren(...(s.tasks.length ? s.tasks.map((task) => {
    const row = el("div", "stat-site");
    row.append(el("span", "stat-host", task.content), el("span", "help", counts(task)),
               el("b", "", duration(task.minutes)));
    return row;
  }) : [el("div", "help", t("stats.focus.tasks_empty"))]));
}

// How often websites were blocked (App.blocks.stats, from blocks.json):
// totals, one stacked bar per day (focus below, limits above), and the most
// blocked sites.
function renderStats(s) {
  const split = (c) => t("stats.blocks.split", { focus: formatNumber(c.focus), limit: formatNumber(c.limit) });
  putTiles($("statTiles"), statTiles(s).map(([label, tot, kind]) =>
    statTile(label, formatNumber(tot.focus + tot.limit, { maximumFractionDigits: 1 }), kind === "average"
      ? [t("stats.tiles.per_day")]
      : [keepCounts(split(tot)), ...changeLine(s, kind)])));

  barChart($("statChart"), s.bars, [["focus", "stat-f"], ["limit", "stat-l"]],
    t("stats.blocks.chart", { ...rangeDays(s), focus: formatNumber(s.totals.range.focus), limit: formatNumber(s.totals.range.limit) }),
    { line: (d) => d.focus + d.limit ? t("stats.blocks.tip", { focus: formatNumber(d.focus), limit: formatNumber(d.limit) }) : t("stats.blocks.tip_none"), today: !s.offset });
  axisEnds(s, "statFirst", "statLast");

  $("statSites").replaceChildren(...(s.sites.length ? s.sites.map((site) => {
    const row = el("div", "stat-site");
    row.append(el("span", "stat-host", site.host), el("span", "help", split(site)),
               el("b", "", t("stats.blocks.count", { n: site.focus + site.limit })));
    return row;
  }) : [el("div", "help", t("stats.blocks.empty"))]));
}

for (const b of document.querySelectorAll("#settingsTabs .stab")) b.onclick = () => showTab(b.dataset.tab);
$("settingsTabs").addEventListener("keydown", (e) => {
  const i = TABS.indexOf(settingsTab);
  const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key];
  if (next === undefined) return;
  e.preventDefault();
  showTab(TABS[(next + TABS.length) % TABS.length], true);
});

// Small dots on the tabs that need a look: an update, or site blocking
// switched on without the extension connected.
function renderTabDots() {
  $("aboutDot").classList.toggle("hidden", !(state && state.update));
  const sitesOn = $("s_block_sites").checked;
  const connected = !!(state && state.extension_connected && !state.extension_outdated);
  $("sitesDot").classList.toggle("hidden", !sitesOn || connected);
  $("limitsDot").classList.toggle("hidden", !$("s_site_limits_on").checked || connected);
}

async function openSettings(tab) {
  const s = await api.get_settings();
  const form = $("settingsForm");
  for (const input of form.querySelectorAll("input[name]")) {
    if (input.type === "checkbox") input.checked = !!s[input.name];
    else if (input.type === "radio") input.checked = String(s[input.name]) === input.value;
    else input.value = s[input.name] ?? "";
  }
  $("testResult").textContent = "";
  showLanguages(s);
  showSecrets(s);
  blockedApps =(s.blocked_apps || []).map((a) => ({ ...a, exes: [...a.exes] }));
  $("addBlockedApp").value = "";
  renderBlockedApps();
  activityRows = (s.activities || []).map((a) => ({ ...a }));
  callActivity = s.call_activity || "";
  $("s_call_ignore").value = (s.call_ignore_apps || []).join(", ");
  showCallFields();
  iconPicker = -1;
  blockScope = "";
  $("addActivity").value = "";
  renderActivities();
  siteLists = { block: [...(s.blocked_sites || [])], allow: [...(s.allowed_sites || [])] };
  LIMITS.site.rules = JSON.parse(JSON.stringify(s.site_limits || []));
  LIMITS.app.rules = JSON.parse(JSON.stringify(s.app_limits || []));
  LIMITS.site.usage = {};
  LIMITS.app.usage = {};
  closeLimitEditor();
  renderLimits();
  refreshLimitUsage();
  syncOwner = s.sync_owner || "";
  renderSync(s.sync_info);
  $("addBlockedSite").value = "";
  $("extDir").textContent = s.extension_dir || "extension";
  $("extDir").title = s.extension_dir || "";
  const browser = s.extension_browser || "Chrome";
  for (const n of document.querySelectorAll(".browser-page")) n.textContent = t("settings.ext.open_page", { browser });
  renderBlockedSites();
  renderBlocking();
  $("extOutdatedText").textContent = t("settings.ext.outdated", {
    old: s.extension_version ? t("settings.ext.old_version", { version: s.extension_version }) : t("settings.ext.old_unknown"),
    version: s.extension_latest || "",
  });
  renderExtension(s.extension_connected, s.extension_outdated);
  barScreens = JSON.parse(JSON.stringify(s.bar_screens || {}));
  renderBarScreens();
  loadBarThemes();
  showBarFields();
  showTodoistFields();
  showPlanFields();
  $("aboutVersion").textContent = "buZyist " + s.version;
  $("aboutData").textContent = s.data_dir;
  $("autoUpdateRow").classList.toggle("hidden", !s.can_auto_update);
  $("updateResult").textContent = "";
  renderUpdate();
  if (s.use_todoist && !s.has_todoist_token) tab = "todoist";
  statsOffset = 0;  // Stats opens on the period ending today
  showTab(typeof tab === "string" ? tab : settingsTab);
  $("drawer").classList.add("on");
  $("scrim").classList.add("on");
  setTimeout(() => {
    if (s.use_todoist && !s.has_todoist_token) $("s_todoist_token").focus();
    else document.querySelector("#settingsTabs .stab.on").focus();
  }, 220);
}

// The app language, each in its own name; saving a new one reloads the windows.
function showLanguages(s) {
  const select = $("s_language");
  select.textContent = "";
  for (const { code, name } of s.languages || []) {
    const option = el("option", "", name);
    option.value = code;
    option.lang = code;
    select.append(option);
  }
  select.value = s.language || "en";
}

// The token and the PIN never come to the page (get_settings sends "" and
// has_*). An empty field keeps the saved one; Remove asks Python to clear it.
const SECRETS = {
  todoist_token: { input: "s_todoist_token", saved: "tokenSaved", lost: "tokenLost", remove: "removeToken", empty: () => t("settings.todoist.token_placeholder") },
  busybar_pin: { input: "s_busybar_pin", saved: "pinSaved", lost: "pinLost", remove: "removePin", empty: () => "" },
};
let secretsToClear = {};

function showSecrets(s) {
  secretsToClear = {};
  for (const [key, x] of Object.entries(SECRETS)) {
    const has = !!s["has_" + key];
    $(x.input).value = "";
    $(x.input).placeholder = has ? t("settings.secret_saved") : x.empty();
    $(x.saved).classList.toggle("hidden", !has);
    $(x.lost).classList.toggle("hidden", !s[key + "_lost"]);
  }
}

// What a save sends for the secrets: Remove, unless a new one was typed.
function secretClears() {
  const out = {};
  for (const [key, x] of Object.entries(SECRETS)) if (secretsToClear[key] && !$(x.input).value.trim()) out["clear_" + key] = true;
  return out;
}

for (const [key, x] of Object.entries(SECRETS)) {
  $(x.remove).onclick = () => {
    secretsToClear[key] = true;
    $(x.input).value = "";
    $(x.input).placeholder = x.empty();
    $(x.saved).classList.add("hidden");
    $(x.input).focus();
  };
}

// The token and label options only matter with Todoist.
function showTodoistFields() {
  const on = $("s_use_todoist").checked;
  $("todoistFields").classList.toggle("muted", !on);
  $("noTodoistHelp").classList.toggle("hidden", on);
}
$("s_use_todoist").onchange = showTodoistFields;

// (minutes per pomodoro, pomodoros) for a task of `minutes`, or null. Mirrors
// plan_session in buzyist/plan.py; tests/plan_cases.json checks both.
function planSession(minutes, c) {
  if (!c.plan_from_duration || !(minutes > 0)) return null;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  if (minutes <= c.plan_single_max) return [clamp(minutes, 5, 480), 1];
  let rounds = Math.max(1, Math.floor(minutes / c.plan_target + 0.5));
  rounds = Math.max(1, Math.min(rounds, Math.floor(minutes / c.plan_min)));
  rounds = Math.max(rounds, Math.ceil(minutes / c.plan_max));
  rounds = Math.min(rounds, 35);
  return [clamp(Math.floor(minutes / rounds + 0.5), 5, 480), rounds];
}

// The duration options, with what they make of a few task lengths.
function showPlanFields() {
  const v = formValues(["plan_from_duration", "plan_single_max", "plan_target", "plan_min", "plan_max"]);
  $("planFields").classList.toggle("muted", !v.plan_from_duration);
  const c = { ...v };
  for (const k of ["plan_single_max", "plan_target", "plan_min", "plan_max"]) c[k] = +v[k];
  const ok = c.plan_target >= 5 && c.plan_min >= 5 && c.plan_max >= 5 && c.plan_single_max >= 0
    && c.plan_min <= c.plan_target && c.plan_target <= c.plan_max;
  if (!ok) {
    $("planExample").textContent = t("settings.todoist.plan_bad");
    return;
  }
  c.plan_from_duration = true;
  $("planExample").textContent = t("settings.todoist.plan_example", { list: [15, 30, 45, 60, 90, 120].map((m) => {
    const [mins, rounds] = planSession(m, c);
    const plan = rounds === 1 ? t("settings.todoist.plan_one", { min: mins }) : t("settings.todoist.plan_many", { n: rounds, min: mins });
    return `${duration(m)} → ${plan}`;
  }).join(" · ") });
}
for (const name of ["plan_from_duration", "plan_single_max", "plan_target", "plan_min", "plan_max"]) {
  document.querySelector(`#settingsForm input[name="${name}"]`).addEventListener("input", showPlanFields);
}

// The bar's address, PIN and polling only matter with a bar.
function showBarFields() {
  const on = $("s_use_busybar").checked;
  $("barFields").classList.toggle("hidden", !on);
  $("noBarHelp").classList.toggle("hidden", on);
}
$("s_use_busybar").onchange = () => { showBarFields(); loadBarThemes(); };

// ------------------------------------------------------------ bar screens
// What the bar's front screen shows during our sessions (BUSY Bar tab), as
// edited here and saved with the rest: barScreens = {focus, call, done},
// each {mode, theme, text, color}, and an activity's own focus screen in its
// `bar` (none: Focus). clean_bar_screens in buzyist/bar_screens.py has the
// last word; the previews come from Python (bar_display.render).
const BAR_COLORS = {  // as SCREEN_COLORS in buzyist/bar_screens.py (a test checks): [background, text]
  tomate: ["#D9361B", "#FFFFFF"],
  molho: ["#A82A12", "#FFFFFF"],
  manteiga: ["#F5B83D", "#000000"],
  folha: ["#1E6B43", "#FFFFFF"],
  broto: ["#4DB57A", "#000000"],
  terra: ["#3A1410", "#FFF7F3"],
  black: ["#000000", "#FFFFFF"],
};
const MAX_SCREEN_TEXT = 60;  // as in buzyist/bar_screens.py
// Modes as [value, catalog key under settings.bar.modes]; titles are keys too
// (t() runs once the catalogs are in, not at load).
const OWN_MODES = [["card", "card"], ["theme", "theme"], ["text", "text"]];
const SCREEN_EDITS = {
  focus: { title: "focus", modes: OWN_MODES, words: ["task", "activity", "pomodoro", "pomodoros"], minutes: 18, text: "{task}", color: "tomate" },
  call: { title: "call", modes: OWN_MODES, words: ["task", "activity", "apps"], minutes: 12, text: "CALL", color: "molho" },
  done: { title: "done", modes: [["text", "text"], ["off", "off"]], words: ["task", "activity", "pomodoros", "minutes"], minutes: null, text: "DONE", color: "folha" },
};
let barScreens = {};
let barThemes = [];  // the bar's own, the card's first; [] until read
const prettyTheme = (name) => (name ? name[0].toUpperCase() + name.slice(1).replace(/_/g, " ") : "");

// The bar's battery under Test connection (Api.bar_power); hidden without a
// bar, or when it doesn't answer or doesn't say.
const BATTERY_STATES = ["charging", "charged", "discharging"];
async function loadBarPower() {
  const line = $("barPower");
  line.classList.add("hidden");
  if (!$("s_use_busybar").checked) return;
  const result = await api.bar_power();
  if (!result.ok || result.charge == null) return;
  const state = BATTERY_STATES.includes(result.state) ? result.state : "unknown";
  line.textContent = t("settings.bar.battery_" + state, { charge: formatNumber(result.charge) });
  line.classList.remove("hidden");
}

async function loadBarThemes() {
  const note = $("barThemesNote");
  note.textContent = "";
  if (!$("s_use_busybar").checked) return;
  loadBarPower();
  const result = await api.bar_themes();
  if (!result.ok) {
    note.textContent = t("settings.bar.themes_error", { error: result.error });
    return;
  }
  barThemes = result.themes;
  renderBarScreens();
}

function showBarScreenFields() {
  $("barScreenFields").classList.toggle("muted", !$("s_bar_screens_on").checked);
}
$("s_bar_screens_on").addEventListener("change", showBarScreenFields);

function renderBarScreens() {
  const box = $("barScreens");
  box.replaceChildren(...Object.keys(SCREEN_EDITS).map((k) => screenEditor(barScreens, k, SCREEN_EDITS[k])));
  // Calls count under callActivity, and have the Call screen above.
  const per = $("barActivityScreens");
  const rows = activityRows.filter((a) => a.id !== callActivity);
  per.replaceChildren(...rows.map((a) => screenEditor(a, "bar", SCREEN_EDITS.focus, a.name.trim() || t("settings.unnamed"))));
  if (!rows.length) per.append(el("div", "help", t("settings.bar.no_activities")));
  showBarScreenFields();
}

// One screen's editor: holder[key] is the screen ({mode, theme, text, color});
// `name` (an activity's) adds "Same as Focus", which leaves holder[key] out.
function screenEditor(holder, key, edit, name) {
  const value = holder[key] || { mode: "" };
  const title = name || t("settings.bar.screens." + edit.title);
  const box = el("div", "screen-edit");
  const head = el("div", "screen-head");
  head.append(el("span", "screen-name", title));
  const modes = (name ? [["", "same"], ...edit.modes] : edit.modes).map(([v, k]) => [v, t("settings.bar.modes." + k)]);
  head.append(segButtons(t("settings.bar.on_bar", { screen: title }), modes, value.mode || "", (mode) => {
    if (!mode) delete holder[key];
    else {
      const ownTheme = barThemes.find((th) => th === "on_call" && key === "call") || barThemes[1] || barThemes[0] || "";
      holder[key] = { mode, theme: value.theme || (mode === "theme" ? ownTheme : ""), text: value.text || edit.text, color: value.color || edit.color };
    }
    renderBarScreens();
  }));
  box.append(head);
  const mode = value.mode;
  if (!mode) return box;
  const note = el("div", "bar-preview-note");
  if (mode === "theme") {
    const pick = el("select", "input");
    pick.setAttribute("aria-label", t("settings.bar.theme_for", { screen: title }));
    const themes = barThemes.includes(value.theme) || !value.theme ? barThemes : [value.theme, ...barThemes];
    pick.replaceChildren(...themes.map((th) => new Option(prettyTheme(th), th)));
    pick.value = value.theme;
    pick.onchange = () => { value.theme = pick.value; note.textContent = prettyTheme(pick.value).toUpperCase(); };
    const row = el("div", "screen-row");
    row.append(pick);
    box.append(row);
  }
  if (mode !== "text") {
    note.textContent = { card: "BUSY · 24:58 LEFT", theme: prettyTheme(value.theme).toUpperCase() || "…", off: "09:41" }[mode];
    box.append(note, el("div", "help", t({
      card: "settings.bar.help_card",
      theme: barThemes.length ? "settings.bar.help_theme" : "settings.bar.help_theme_wait",
      off: "settings.bar.help_off",
    }[mode])));
    return box;
  }
  const row = el("div", "screen-row");
  const input = el("input", "input slim");  // no name: formValues() leaves it alone
  input.type = "text";
  input.maxLength = MAX_SCREEN_TEXT;
  input.spellcheck = false;
  input.value = value.text;
  input.setAttribute("aria-label", t("settings.bar.text_for", { screen: title }));
  const img = el("img", "bar-preview");
  img.alt = t("settings.bar.preview_alt", { screen: title });
  let timer = 0;
  const refresh = () => {
    clearTimeout(timer);
    timer = setTimeout(() => barPreview(img, input.value, value.color, edit.minutes), 120);
  };
  input.oninput = () => { value.text = input.value; refresh(); };
  const swatches = el("div", "swatches");
  swatches.setAttribute("role", "group");
  swatches.setAttribute("aria-label", t("settings.bar.colour_for", { screen: title }));
  for (const [k, [back]] of Object.entries(BAR_COLORS)) {
    const b = el("button", "swatch");
    b.type = "button";
    b.title = t("settings.bar.colors." + k);
    b.style.background = back;
    b.setAttribute("aria-label", b.title);
    b.setAttribute("aria-pressed", k === value.color);
    b.onclick = () => {
      value.color = k;
      for (const s of swatches.children) s.setAttribute("aria-pressed", s === b);
      refresh();
    };
    swatches.append(b);
  }
  row.append(input, swatches);
  const words = el("div", "placeholders");
  for (const w of edit.words) {
    const b = el("button", "", `{${w}}`);
    b.type = "button";
    b.title = t("settings.bar.insert", { word: `{${w}}` });
    b.onclick = () => {
      const at = input.selectionStart ?? input.value.length;
      input.setRangeText(`{${w}}`, at, input.selectionEnd ?? at, "end");
      input.focus();
      input.dispatchEvent(new Event("input"));
    };
    words.append(b);
  }
  box.append(row, words, img);
  if (key === "done") box.append(el("div", "help", t("settings.bar.done_help")));
  barPreview(img, value.text, value.color, edit.minutes);
  return box;
}

const previews = new Map();
async function barPreview(img, text, color, minutes) {
  const key = JSON.stringify([text, color, minutes]);
  img.dataset.key = key;
  let url = previews.get(key);
  if (!url) {
    const result = await api.bar_preview(text, color, minutes);
    if (!result.ok) return;
    url = result.url;
    previews.set(key, url);
  }
  if (img.dataset.key === key) img.src = url;
}

// The apps kept away during focus, as edited here; saved with the rest.
// The activities offered under "What are you working on?" (General tab):
// [{id, name, icon}], renamed in place (the id stays, so do their stats),
// reordered, removed, added. clean_activities in buzyist/activities.py has
// the last word when Settings are saved.
const MAX_ACTIVITIES = 12;  // as in buzyist/activities.py
let activityRows = [];
let iconPicker = -1;  // the row whose icons are showing

function renderActivities() {
  const list = $("activityList");
  list.replaceChildren();
  activityRows.forEach((a, i) => {
    const row = el("div", "activity-row");
    const pick = el("button", "activity-icon");
    pick.type = "button";
    pick.title = t("settings.general.change_icon");
    pick.setAttribute("aria-label", t("settings.general.icon_for", { name: a.name }));
    pick.setAttribute("aria-expanded", iconPicker === i);
    pick.append(activityIcon(a.icon));
    pick.onclick = () => { iconPicker = iconPicker === i ? -1 : i; renderActivities(); };

    const name = el("input", "input slim");  // no name: formValues() leaves it alone
    name.value = a.name;
    name.type = "text";
    name.maxLength = 30;
    name.spellcheck = false;
    name.setAttribute("aria-label", t("settings.general.activity_name"));
    name.oninput = () => { a.name = name.value; renderScopes(); renderCallActivity(); renderBarScreens(); };

    const move = (d) => {
      const j = i + d;
      if (j < 0 || j >= activityRows.length) return;
      [activityRows[i], activityRows[j]] = [activityRows[j], activityRows[i]];
      iconPicker = -1;
      renderActivities();
      list.querySelectorAll(".activity-row")[j].querySelector(d < 0 ? ".up" : ".down").focus();
    };
    const up = el("button", "remove up");
    up.type = "button";
    up.title = t("settings.general.move_up");
    up.disabled = i === 0;
    up.append(icon("up"));
    up.onclick = () => move(-1);
    const down = el("button", "remove down");
    down.type = "button";
    down.title = t("settings.general.move_down");
    down.disabled = i === activityRows.length - 1;
    down.append(icon("down"));
    down.onclick = () => move(1);
    const x = el("button", "remove", "×");
    x.type = "button";
    x.title = t("settings.remove_item", { name: a.name });
    x.onclick = () => { activityRows.splice(i, 1); iconPicker = -1; renderActivities(); };

    row.append(pick, name, up, down, x);
    list.append(row);
    if (iconPicker === i) {
      const icons = el("div", "icon-grid");
      icons.setAttribute("role", "group");
      icons.setAttribute("aria-label", t("settings.general.icons_for", { name: a.name }));
      for (const k of ACTIVITY_ICONS) {
        const b = el("button", "activity-icon" + (k === a.icon ? " on" : ""));
        b.type = "button";
        b.title = k;
        b.setAttribute("aria-pressed", k === a.icon);
        b.append(activityIcon(k));
        b.onclick = () => { a.icon = k; iconPicker = -1; renderActivities(); list.querySelectorAll(".activity-row")[i].querySelector(".activity-icon").focus(); };
        icons.append(b);
      }
      list.append(icons);
    }
  });
  if (!activityRows.length) list.append(el("div", "help", t("settings.general.no_activities")));
  $("addActivity").disabled = $("addActivityBtn").disabled = activityRows.length >= MAX_ACTIVITIES;
  renderBlocking();  // the picker in Apps and Websites
  renderCallActivity();
  renderBarScreens();  // their screens in the BUSY Bar tab
}

// Calls (General): the activity they count under, from the list above. One
// not in the list (removed) gives sessions on no activity, named "Call".
let callActivity = "call";
function renderCallActivity() {
  const pick = $("s_call_activity");
  const known = activityRows.some((a) => a.id === callActivity);
  pick.replaceChildren(
    ...activityRows.map((a) => new Option(a.name, a.id)),
    ...(known ? [] : [new Option(t("settings.general.no_activity"), callActivity)]),
  );
  pick.value = callActivity;
}
$("s_call_activity").onchange = () => { callActivity = $("s_call_activity").value; renderBarScreens(); };
const callIgnore = () => $("s_call_ignore").value.split(/[,;]/).map((v) => v.trim()).filter(Boolean);
// The programs running now, as under the Apps tab's input; several names, as in an app limit.
suggestApps($("s_call_ignore"), { multi: true });
function showCallFields() {
  $("callFields").classList.toggle("muted", !$("s_call_detect").checked);
}
$("s_call_detect").addEventListener("change", showCallFields);

function addActivity() {
  const input = $("addActivity");
  const name = input.value.trim().replace(/\s+/g, " ");
  if (!name) return;
  if (activityRows.some((a) => a.name.trim().toLowerCase() === name.toLowerCase())) return toast(t("settings.general.exists", { name }), true);
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 20) || "activity";
  let id = base;
  for (let n = 2; activityRows.some((a) => a.id === id); n++) id = `${base}-${n}`;
  activityRows.push({ id, name, icon: "dot" });
  input.value = "";
  iconPicker = activityRows.length - 1;  // a new one picks its icon next
  renderActivities();
}
$("addActivityBtn").onclick = addActivity;
$("addActivity").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addActivity(); } });

// Each is {id, name, exes, on, action: "close"|"hide", custom}; the presets
// come from Python and can only be switched, apps added here can be removed.
const MAX_BLOCKED_APPS = 20;
let blockedApps = [];

function renderBlockedApps() {
  const list = $("blockedApps");
  list.replaceChildren();
  blockedApps.forEach((app, i) => {
    const row = el("div", "app-row" + (app.on ? "" : " off"));

    const label = el("label", "switch");
    const check = el("input");  // no name: formValues() leaves it alone
    check.type = "checkbox";
    check.checked = app.on;
    check.onchange = () => { app.on = check.checked; row.classList.toggle("off", !app.on); };
    label.append(check, el("span", "knob"), el("span", "", app.name), el("span", "exe", app.exes[0]));
    label.title = app.exes.join(", ");

    const action = el("select", "action");
    for (const [value, text] of [["close", t("settings.apps.close")], ["hide", t("settings.apps.hide")]]) {
      const opt = el("option", "", text);
      opt.value = value;
      action.append(opt);
    }
    action.value = app.action;
    action.onchange = () => { app.action = action.value; };

    row.append(label, action);
    if (app.custom) {
      const x = el("button", "remove", "×");
      x.type = "button";
      x.title = t("settings.remove_item", { name: app.name });
      x.onclick = () => { blockedApps.splice(i, 1); renderBlockedApps(); };
      row.append(x);
    } else {
      row.append(el("span"));
    }
    list.append(row);
  });
  $("blockedAppsFields").classList.toggle("muted", !$("s_block_apps").checked);
}

// Programs the blocker never closes: PROTECTED_EXES in buzyist/blocker.py, which
// has the last word when Settings are saved.
const PROTECTED_EXES = new Set([
  "explorer", "svchost", "csrss", "wininit", "winlogon", "lsass", "services", "smss",
  "dwm", "fontdrvhost", "sihost", "taskhostw", "ctfmon", "runtimebroker", "searchhost",
  "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "taskmgr",
  "conhost", "dllhost", "audiodg", "spoolsv", "msmpeng", "securityhealthservice",
  "buzyist", "busyist", "python", "pythonw", "py",
  "chrome", "msedge", "msedgewebview2",
]);

function addBlockedApp() {
  const input = $("addBlockedApp");
  const name = input.value.trim();
  if (!name) return;
  if (/[\\/]/.test(name)) return toast(t("settings.apps.not_program"), true);
  const key = (n) => n.toLowerCase().replace(/\.exe$/, "");
  if (PROTECTED_EXES.has(key(name))) {
    return toast(t("settings.apps.protected", { app: name }), true);
  }
  const known = blockedApps.find((a) => a.exes.some((e) => key(e) === key(name)));
  input.value = "";
  if (known) { known.on = true; return renderBlockedApps(); }  // already listed: just switch it on
  if (blockedApps.filter((a) => a.custom).length >= MAX_BLOCKED_APPS) {
    return toast(t("settings.apps.too_many", { n: MAX_BLOCKED_APPS }), true);
  }
  const exe = /\.exe$/i.test(name) ? name : name + ".exe";
  blockedApps.push({
    id: "custom:" + key(exe), name: exe.replace(/\.exe$/i, "").split(".")[0] || exe,
    exes: [exe], on: true, action: "close", custom: true,
  });
  renderBlockedApps();
}
// Program names to pick from while typing one: what's running now
// (Api.running_apps, windowed apps first), asked again after a few seconds.
let runningApps = { at: 0, list: [] };
async function loadRunningApps() {
  if (Date.now() - runningApps.at < 5000) return runningApps.list;
  const r = await api.running_apps();
  runningApps = { at: Date.now(), list: r.ok ? r.apps : [] };
  return runningApps.list;
}

// The suggestions under an app-name input. The input may hold several names
// separated by commas (`multi`): only the one being typed is completed.
// `skip(app)` leaves an app out; `picked(exe)` runs after a click, Enter or
// Tab on one. Enter with nothing highlighted is left to the input's own handler.
function suggestApps(input, { multi = false, skip = () => false, picked = () => {} } = {}) {
  const box = el("div", "suggest hidden");
  box.setAttribute("role", "listbox");
  input.parentElement.classList.add("suggest-host");
  input.after(box);
  input.setAttribute("autocomplete", "off");
  let items = [];
  let active = -1;
  const key = (n) => n.toLowerCase().replace(/\.exe$/, "");
  const parts = () => (multi ? input.value.split(/[,;]/) : [input.value]);
  const hide = () => { box.classList.add("hidden"); items = []; active = -1; };
  const mark = () => box.querySelectorAll(".suggest-item").forEach((n, i) => n.classList.toggle("active", i === active));

  async function show() {
    const list = await loadRunningApps();
    if (document.activeElement !== input) return;
    const all = parts();
    const typed = all[all.length - 1].trim().toLowerCase();
    const taken = new Set(all.slice(0, -1).map((p) => key(p.trim())));
    items = list.filter((a) => !taken.has(key(a.exe)) && !skip(a)
      && (!typed || a.exe.toLowerCase().includes(typed) || a.name.toLowerCase().includes(typed)))
      .slice(0, 8);
    if (items.length === 1 && key(items[0].exe) === key(typed)) items = [];  // already typed in full
    active = -1;
    box.replaceChildren(...items.map((a, i) => {
      const row = el("div", "suggest-item");
      row.setAttribute("role", "option");
      row.append(el("span", "suggest-name", a.name), el("span", "suggest-exe", a.window ? a.exe : t("settings.apps.in_background", { exe: a.exe })));
      row.onmousedown = (e) => { e.preventDefault(); pick(i); };  // keep the focus in the input
      return row;
    }));
    box.classList.toggle("hidden", !items.length);
    Object.assign(box.style, {  // under the input, wherever its row puts it
      top: input.offsetTop + input.offsetHeight + 4 + "px", left: input.offsetLeft + "px", width: input.offsetWidth + "px",
    });
  }

  function pick(i) {
    const exe = items[i].exe;
    if (multi) {
      const kept = parts().slice(0, -1).map((p) => p.trim()).filter(Boolean);
      input.value = [...kept, exe].join(", ") + ", ";
    } else {
      input.value = exe;
    }
    input.dispatchEvent(new Event("input"));
    hide();
    picked(exe);
    if (multi && document.activeElement === input) show();
  }

  input.addEventListener("focus", show);
  input.addEventListener("input", (e) => { if (e.isTrusted) show(); });
  input.addEventListener("blur", hide);
  input.addEventListener("keydown", (e) => {
    if (!items.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : items.length - 1 + (active < 0 ? 1 : 0))) % items.length;
      mark();
    } else if ((e.key === "Enter" || e.key === "Tab") && active >= 0) {
      e.preventDefault(); e.stopImmediatePropagation();
      pick(active);
    } else if (e.key === "Escape") {
      e.preventDefault(); e.stopImmediatePropagation();
      hide();
    }
  });
}

// Registered before the Enter handler below, so picking a suggestion comes first.
suggestApps($("addBlockedApp"), {
  skip: (a) => a.protected || blockedApps.some((b) => b.on && b.exes.some((x) => x.toLowerCase() === a.exe.toLowerCase())),
  picked: addBlockedApp,
});
$("addBlockedAppBtn").onclick = addBlockedApp;
$("addBlockedApp").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); addBlockedApp(); }
});
$("s_block_apps").onchange = renderBlockedApps;

// The website lists, one per mode: blocked during focus, or the only ones
// allowed. Entries are domains or URL patterns with * wildcards; the Chrome
// extension reads them from buZyist. Edited here, saved with the rest.
let siteLists = { block: [], allow: [] };
const siteMode = () => (document.querySelector('input[name="site_mode"]:checked') || {}).value || "block";

function renderBlockedSites() {
  const mode = siteMode();
  const sites = siteLists[mode];
  const list = $("blockedSites");
  list.replaceChildren();
  $("addBlockedSite").placeholder = t(mode === "allow" ? "settings.sites.placeholder_allow" : "settings.sites.placeholder_block");
  $("sitesHelp").textContent = t(mode === "allow"
    ? (sites.length ? "settings.sites.help_allow" : "settings.sites.help_allow_empty")
    : "settings.sites.help_block");
  sites.forEach((site, i) => {
    const row = el("div", "app-row");
    row.append(el("span", "site", site));
    const x = el("button", "remove", "\u00d7");
    x.type = "button";
    x.title = t("settings.remove_item", { name: site });
    x.onclick = () => { sites.splice(i, 1); renderBlockedSites(); };
    row.append(el("span"), x);
    list.append(row);
  });
  $("blockedSitesFields").classList.toggle("muted", !$("s_block_sites").checked);
  renderTabDots();
}

// The setup steps show until the extension has called buZyist.
// A connected extension older than the one buZyist ships needs a reload.
function renderExtension(connected, outdated) {
  const status = $("extStatus");
  const good = connected && !outdated;
  status.textContent = t(!connected ? "settings.ext.status_off"
    : outdated ? "settings.ext.status_reload" : "settings.ext.status_on");
  status.classList.toggle("ok", good);
  status.classList.toggle("bad", !good);
  $("extSetup").classList.toggle("hidden", connected);
  $("extOutdated").classList.toggle("hidden", !outdated);
  $("extDone").classList.toggle("hidden", !good);
  renderTabDots();
}

function addBlockedSite() {
  const input = $("addBlockedSite");
  const site = cleanSitePattern(input.value);
  input.value = "";
  if (site === "") return;
  if (site === null) return toast(t("settings.sites.not_site"), true);
  const sites = siteLists[siteMode()];
  if (sites.includes(site)) return;
  if (sites.length >= 50) return toast(t("settings.sites.too_many", { n: 50 }), true);
  sites.push(site);
  renderBlockedSites();
}
// Mirrors clean_site_pattern in buzyist/site_rules.py, which has the final say on save.
// "" for nothing typed, null for something that isn't a website.
function cleanSitePattern(raw) {
  let text = String(raw).trim().toLowerCase();
  if (!text) return "";
  text = text.replace(/^[a-z*]+:\/\//, "").split("#")[0].trim().replace(/\*{2,}/g, "*");
  const slash = text.indexOf("/");
  let host = slash < 0 ? text : text.slice(0, slash);
  let path = slash < 0 ? "" : text.slice(slash);
  if (host.startsWith("www.")) host = host.slice(4);
  if (!host && path) host = "*";
  if (!/^[a-z0-9.*-]+$/.test(host) || (!host.includes(".") && !host.includes("*")) || path.includes(" ")) return null;
  if (path === "/" || path === "/*") path = "";
  return host + path;
}
// ---------------------------------------------------- blocking per activity
//
// The Apps and Websites tabs edit either the global settings ("Everything") or
// what one activity changes in them during its work phases: its `apps` and
// `sites` rules, kept on its row in activityRows and saved with it.
// appBlocking and siteBlocking mirror app_blocking (blocker.py) and
// site_blocking (site_rules.py) for the summary under the lists; Python has the
// last word (tests/activity_blocking_cases.json feeds both).
let blockScope = "";  // "" for the global settings, else an activity's id
const scopeActivity = () => activityRows.find((a) => a.id === blockScope) || null;
const appKey = (n) => n.trim().toLowerCase().replace(/\.exe$/, "");  // app_key in blocker.py
const blockStates = () => [["inherit", t("settings.scope.inherit")], ["off", t("settings.scope.off")], ["on", t("settings.scope.on")]];
const RULE_DEFAULTS = {
  apps: { state: "inherit", allow: [], block: [] },
  sites: { state: "inherit", mode: "", allow: [], block: [] },
};
// An activity's rules, or a fresh copy of the defaults when it has none:
// editActivityRules stores them on the first change, so looking changes nothing.
const activityRules = (a, key) => a[key] || structuredClone(RULE_DEFAULTS[key]);

function editActivityRules(a, key, change) {
  const rules = activityRules(a, key);
  change(rules);
  a[key] = rules;
  renderBlocking();
}

function appBlocking(cfg, id, protect = PROTECTED_EXES) {
  const key = (n) => n.trim().toLowerCase().replace(/\.exe$/, "");
  const label = (exe) => exe.replace(/\.exe$/i, "").split(".")[0] || exe;
  const activity = id ? (cfg.activities || []).find((a) => a.id === id) : null;
  const rules = (activity && activity.apps) || {};
  const state = rules.state || "inherit";
  if (state === "off" || (state === "inherit" && !cfg.block_apps)) return [];
  const skip = new Set([...(rules.allow || []).map(key), ...protect]);
  const entries = cfg.blocked_apps || [];
  const out = [];
  const taken = new Set();
  for (const entry of entries) {
    const exes = entry.on ? entry.exes.filter((e) => !skip.has(key(e))) : [];
    if (exes.length) {
      out.push({ ...entry, exes });
      for (const e of exes) taken.add(key(e));
    }
  }
  for (const exe of rules.block || []) {
    const k = key(exe);
    if (taken.has(k) || skip.has(k)) continue;
    taken.add(k);
    const owner = entries.find((e) => e.exes.some((x) => key(x) === k));
    out.push(owner ? { ...owner, exes: [exe], on: true }
      : { id: "activity:" + k, name: label(exe), exes: [exe], on: true, action: "close", custom: true });
  }
  return out;
}

function siteBlocking(cfg, id) {
  const activity = id ? (cfg.activities || []).find((a) => a.id === id) : null;
  const rules = (activity && activity.sites) || {};
  const state = rules.state || "inherit";
  const globalMode = cfg.site_mode || "block";
  const mode = rules.mode || globalMode;
  if (state === "off" || (state === "inherit" && !cfg.block_sites)) return { on: false, mode, patterns: [], except: [] };
  const base = mode === globalMode ? [...(cfg[mode === "allow" ? "allowed_sites" : "blocked_sites"] || [])] : [];
  const carve = [...(rules[mode === "allow" ? "block" : "allow"] || [])];
  const patterns = [];
  for (const p of [...base, ...(rules[mode] || [])]) {
    if (!patterns.includes(p) && !carve.includes(p)) patterns.push(p);
  }
  return { on: true, mode, patterns, except: carve };
}

// The settings as edited here, in the shape the mirrors take.
const draftBlocking = () => ({
  block_apps: $("s_block_apps").checked, blocked_apps: blockedApps,
  block_sites: $("s_block_sites").checked, site_mode: siteMode(),
  blocked_sites: siteLists.block, allowed_sites: siteLists.allow, activities: activityRows,
});

// The picker at the top of both tabs: Everything, then each activity. Drawn
// again when an activity is added, renamed or removed in General.
function renderScopes() {
  if (blockScope && !scopeActivity()) blockScope = "";
  for (const id of ["appsScope", "sitesScope"]) {
    const box = $(id);
    box.classList.toggle("hidden", !activityRows.length);
    box.replaceChildren();
    const choice = (value, name, iconName) => {
      const b = el("button", "scope");
      b.type = "button";
      b.setAttribute("aria-pressed", blockScope === value);
      if (iconName) b.append(activityIcon(iconName));
      b.append(el("span", "", name));
      b.onclick = () => {
        blockScope = value;
        renderBlocking();
        $(id).querySelector('[aria-pressed="true"]').focus();
      };
      box.append(b);
    };
    choice("", t("settings.scope.everything"));
    for (const a of activityRows) choice(a.id, a.name.trim() || t("settings.unnamed"), a.icon);
  }
}

function renderBlocking() {
  renderScopes();
  const a = scopeActivity();
  for (const [global, own] of [["appsGlobal", "appsActivity"], ["sitesGlobal", "sitesActivity"]]) {
    $(global).classList.toggle("hidden", !!a);
    $(own).classList.toggle("hidden", !a);
  }
  if (!a) return;
  renderActivityApps(a);
  renderActivitySites(a);
}

// A row of choices that looks like .seg's radios without being a setting of
// its own (formValues reads every named input).
function segButtons(label, options, value, onpick) {
  const seg = el("div", "seg");
  seg.setAttribute("role", "radiogroup");
  seg.setAttribute("aria-label", label);
  for (const [v, text] of options) {
    const b = el("button", "", text);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", v === value);
    b.onclick = () => {
      onpick(v);
      const again = [...document.querySelectorAll(".seg")].find((s) => s.getAttribute("aria-label") === label);
      if (again) again.querySelector('[aria-checked="true"]').focus();
    };
    seg.append(b);
  }
  return seg;
}

// A list of program names or websites with an input to add one. `suggest`
// (apps only) is suggestApps' skip.
function editableList(box, { title, label, help, items, placeholder, add, remove, suggest }) {
  box.append(el("h4", "", title));
  if (help) box.append(el("div", "help", help));
  const list = el("div", "app-list");
  items.forEach((item, i) => {
    const row = el("div", "app-row");
    row.append(el("span", "site", item), el("span"), removeButton(() => remove(i), t("settings.remove_item", { name: item })));
    list.append(row);
  });
  const line = el("div", "input-row");
  const input = el("input", "input");  // no name: formValues() leaves it alone
  input.spellcheck = false;
  input.placeholder = placeholder;
  input.setAttribute("aria-label", label);
  const btn = el("button", "btn", t("settings.add"));
  btn.type = "button";
  line.append(input, btn);
  box.append(list, line);
  const go = () => {
    const value = input.value;
    input.value = "";
    add(value);
    const again = [...document.querySelectorAll("#appsActivity input, #sitesActivity input")]
      .find((n) => n.getAttribute("aria-label") === label);
    if (again) again.focus();
  };
  if (suggest) suggestApps(input, { skip: suggest, picked: go });  // before the Enter handler, as above
  btn.onclick = go;
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); go(); } });
}

// One global entry under an activity, with a switch that takes it out (or
// puts it back) for that activity. `key` finds the switch again after a render.
function inheritedRow(boxId, key, text, exe, kept, state, toggle) {
  const row = el("div", "app-row" + (kept ? "" : " off"));
  row.dataset.key = key;
  const label = el("label", "switch");
  const check = el("input");  // no name: formValues() leaves it alone
  check.type = "checkbox";
  check.checked = kept;
  check.onchange = () => {
    toggle(check.checked);
    const again = $(boxId).querySelector(`[data-key="${CSS.escape(key)}"] input`);
    if (again) again.focus();
  };
  label.append(check, el("span", "knob"), el("span", exe ? "" : "site", text));
  if (exe) label.append(el("span", "exe", exe));
  row.append(label, el("span", "state", state), el("span"));
  return row;
}

const activityName = (a) => a.name.trim() || t("settings.scope.this_activity");

function renderActivityApps(a) {
  const box = $("appsActivity");
  box.replaceChildren();
  const rules = activityRules(a, "apps");
  const name = activityName(a);
  const globalOn = $("s_block_apps").checked;
  box.append(el("div", "help", t("settings.apps.activity.intro", { name })));
  box.append(segButtons(t("settings.apps.activity.during", { name }), blockStates(), rules.state, (v) => editActivityRules(a, "apps", (r) => { r.state = v; })));
  box.append(el("div", "help", t({
    inherit: globalOn ? "settings.apps.activity.inherit_on" : "settings.apps.activity.inherit_off",
    off: "settings.apps.activity.off",
    on: "settings.apps.activity.on",
  }[rules.state], { name })));
  if (rules.state === "off") return;
  const fields = el("div", "activity-fields" + (rules.state === "inherit" && !globalOn ? " muted" : ""));
  box.append(fields);

  fields.append(el("h4", "", t("settings.apps.activity.from_global")));
  const allowed = new Set(rules.allow.map(appKey));
  const shown = blockedApps.filter((e) => e.on);
  const list = el("div", "app-list");
  for (const entry of shown) {
    const kept = !entry.exes.some((e) => allowed.has(appKey(e)));
    list.append(inheritedRow("appsActivity", entry.id, entry.name, entry.exes[0], kept,
      t(kept ? (entry.action === "hide" ? "settings.apps.activity.hidden" : "settings.apps.activity.closed") : "settings.scope.let_through"),
      (keep) => editActivityRules(a, "apps", (r) => {
        const keys = new Set(entry.exes.map(appKey));
        r.allow = r.allow.filter((e) => !keys.has(appKey(e)));
        if (!keep) r.allow.push(...entry.exes);
      })));
  }
  if (!shown.length) list.append(el("div", "help", t("settings.apps.activity.global_none")));
  fields.append(list);

  const listed = new Set(shown.flatMap((e) => e.exes.map(appKey)));
  const extra = rules.allow.filter((e) => !listed.has(appKey(e)));
  editableList(fields, {
    title: t("settings.apps.activity.also_let"), label: t("settings.apps.activity.also_let_label"),
    help: t("settings.apps.activity.also_let_help"),
    items: extra, placeholder: t("settings.apps.activity.add_let"),
    add: (raw) => addActivityApp(a, "allow", raw),
    remove: (i) => editActivityRules(a, "apps", (r) => { r.allow = r.allow.filter((e) => e !== extra[i]); }),
    suggest: (app) => allowed.has(appKey(app.exe)),
  });
  editableList(fields, {
    title: t("settings.apps.activity.also_block"), label: t("settings.apps.activity.also_block_label"),
    help: t("settings.apps.activity.also_block_help", { name }),
    items: rules.block, placeholder: t("settings.apps.activity.add_block"),
    add: (raw) => addActivityApp(a, "block", raw),
    remove: (i) => editActivityRules(a, "apps", (r) => { r.block.splice(i, 1); }),
    suggest: (app) => app.protected || rules.block.some((e) => appKey(e) === appKey(app.exe)),
  });
  const names = [...new Set(appBlocking(draftBlocking(), a.id).map((e) => e.name))];
  fields.append(el("div", "help preview", names.length
    ? t("settings.apps.activity.summary", { n: names.length, name, apps: names.join(", ") })
    : t("settings.apps.activity.summary_none", { name })));
}

function addActivityApp(a, field, raw) {
  const name = raw.trim().replace(/[,;\s]+$/, "");
  if (!name) return;
  if (/[\\/]/.test(name)) return toast(t("settings.apps.not_program"), true);
  if (field === "block" && PROTECTED_EXES.has(appKey(name))) {
    return toast(t("settings.apps.protected", { app: name }), true);
  }
  const rules = activityRules(a, "apps");
  if (rules[field].some((e) => appKey(e) === appKey(name))) return;
  if (rules[field].length >= MAX_BLOCKED_APPS) {
    return toast(t(field === "allow" ? "settings.apps.too_many_allowed" : "settings.apps.too_many_blocked", { n: MAX_BLOCKED_APPS }), true);
  }
  const exe = /\.exe$/i.test(name) ? name : name + ".exe";
  const other = field === "allow" ? "block" : "allow";
  editActivityRules(a, "apps", (r) => {
    r[other] = r[other].filter((e) => appKey(e) !== appKey(exe));  // one or the other
    r[field].push(exe);
  });
}

function renderActivitySites(a) {
  const box = $("sitesActivity");
  box.replaceChildren();
  const rules = activityRules(a, "sites");
  const name = activityName(a);
  const globalOn = $("s_block_sites").checked;
  const globalMode = siteMode();
  box.append(el("div", "help", t("settings.sites.activity.intro", { name })));
  box.append(segButtons(t("settings.sites.activity.during", { name }), blockStates(), rules.state, (v) => editActivityRules(a, "sites", (r) => { r.state = v; })));
  box.append(el("div", "help", t({
    inherit: globalOn ? "settings.sites.activity.inherit_on" : "settings.sites.activity.inherit_off",
    off: "settings.sites.activity.off",
    on: "settings.sites.activity.on",
  }[rules.state], { name })));
  if (rules.state === "off") return;
  const fields = el("div", "activity-fields" + (rules.state === "inherit" && !globalOn ? " muted" : ""));
  box.append(fields);

  fields.append(segButtons(t("settings.sites.activity.mode_during", { name }), [
    ["", t(globalMode === "allow" ? "settings.sites.activity.like_global_allow" : "settings.sites.activity.like_global_block")],
    ["block", t("settings.sites.activity.block_these")], ["allow", t("settings.sites.activity.allow_these")],
  ], rules.mode, (v) => editActivityRules(a, "sites", (r) => { r.mode = v; })));
  const mode = rules.mode || globalMode;
  const carve = mode === "allow" ? "block" : "allow";  // site_blocking's `except`
  const inherited = mode === globalMode;
  if (inherited) {
    fields.append(el("h4", "", t(mode === "allow" ? "settings.sites.activity.global_allowed" : "settings.sites.activity.global_blocked")));
    const list = el("div", "app-list");
    for (const site of siteLists[mode]) {
      const kept = !rules[carve].includes(site);
      list.append(inheritedRow("sitesActivity", site, site, "", kept,
        t(mode === "allow" ? (kept ? "settings.sites.activity.allowed" : "settings.sites.activity.blocked")
          : (kept ? "settings.sites.activity.blocked" : "settings.scope.let_through")),
        (keep) => editActivityRules(a, "sites", (r) => {
          r[carve] = r[carve].filter((p) => p !== site);
          if (!keep) r[carve].push(site);
        })));
    }
    if (!siteLists[mode].length) {
      list.append(el("div", "help", t(mode === "allow" ? "settings.sites.activity.global_none_allow" : "settings.sites.activity.global_none_block")));
    }
    fields.append(list);
  } else {
    fields.append(el("div", "help", t(globalMode === "allow" ? "settings.sites.activity.not_inherited_allow" : "settings.sites.activity.not_inherited_block", { name })));
  }
  editableList(fields, {
    title: t(inherited ? (mode === "allow" ? "settings.sites.activity.also_allow" : "settings.sites.activity.also_block")
      : (mode === "allow" ? "settings.sites.activity.allow_these" : "settings.sites.activity.block_these")),
    label: t(mode === "allow" ? "settings.sites.activity.allowed_label" : "settings.sites.activity.blocked_label"),
    items: rules[mode],
    placeholder: t(mode === "allow" ? "settings.sites.activity.add_allowed" : "settings.sites.activity.add_blocked"),
    add: (raw) => addActivitySite(a, mode, raw),
    remove: (i) => editActivityRules(a, "sites", (r) => { r[mode].splice(i, 1); }),
  });
  const base = new Set(inherited ? siteLists[mode] : []);
  const extra = rules[carve].filter((p) => !base.has(p));
  editableList(fields, {
    title: t(mode === "allow" ? "settings.sites.activity.still_block" : "settings.sites.activity.let_through"),
    label: t(mode === "allow" ? "settings.sites.activity.still_blocked_label" : "settings.sites.activity.let_through_label"),
    help: t(mode === "allow" ? "settings.sites.activity.still_block_help" : "settings.sites.activity.let_through_help"),
    items: extra,
    placeholder: t(mode === "allow" ? "settings.sites.activity.still_block_placeholder" : "settings.sites.activity.let_through_placeholder"),
    add: (raw) => addActivitySite(a, carve, raw),
    remove: (i) => editActivityRules(a, "sites", (r) => { r[carve] = r[carve].filter((p) => p !== extra[i]); }),
  });
  const b = siteBlocking(draftBlocking(), a.id);
  const n = b.patterns.length;
  const m = b.except.length;
  const summary = b.mode === "allow"
    ? (n ? (m ? "allow_except" : "allow") : "allow_none")
    : (n ? (m ? "block_except" : "block") : "block_none");
  fields.append(el("div", "help preview", t("settings.sites.activity.summary." + summary, { n, m, name })));
}

function addActivitySite(a, field, raw) {
  const site = cleanSitePattern(raw);
  if (site === "") return;
  if (site === null) return toast(t("settings.sites.not_site"), true);
  const rules = activityRules(a, "sites");
  if (rules[field].includes(site)) return;
  if (rules[field].length >= 50) return toast(t("settings.sites.too_many", { n: 50 }), true);
  const other = field === "allow" ? "block" : "allow";
  editActivityRules(a, "sites", (r) => {
    r[other] = r[other].filter((p) => p !== site);  // one or the other
    r[field].push(site);
  });
}

// ------------------------------------------------------- website and app limits
//
// Time budgets and blocked hours that apply all day (the Limits tab), for
// websites (the extension enforces them) and Windows apps (buZyist closes the
// app, or only beeps). Rules are edited in memory and saved with the rest;
// buZyist checks them (clean_site_limit, clean_app_limit) and keeps the
// totals. One editor is open at a time.
const LIMITS = {
  site: { rules: [], usage: {}, list: "limitList", editor: "limitEditor", add: "addLimitBtn",
          fields: "limitsFields", on: "s_site_limits_on", usageCall: "site_usage" },
  app: { rules: [], usage: {}, list: "appLimitList", editor: "appLimitEditor", add: "addAppLimitBtn",
         fields: "appLimitsFields", on: "s_app_limits_on", usageCall: "app_usage" },
};
let limitDraft = null;    // {kind, index, rule} while an editor is open
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];  // catalog keys under settings.limits.days
const dayName = (d) => t("settings.limits.days." + DAYS[d]);

// "5 min per hour", "30 min per 2 days, Mon–Fri"
function budgetText(b) {
  const budget = t("settings.limits.budget." + b.unit, { n: b.every, minutes: b.minutes });
  return !b.days || b.days.length === 7 ? budget : t("settings.limits.budget_days", { budget, days: daysText(b.days) });
}
const clockText = (ms) => formatTime(new Date(ms), { hour: "2-digit", minute: "2-digit" });
const appLabel = (exe) => exe.replace(/\.exe$/i, "").split(".")[0] || exe;  // exe_label in blocker.py
const limitNames = (kind, rule) => (kind === "site" ? rule.patterns : [...new Set(rule.apps.map(appLabel))]).join(", ");
const REMIND_MIN = 1, REMIND_MAX = 120;  // APP_LIMIT_REMIND_MIN/MAX in buzyist/constants.py
const beeps = (kind, rule) => kind === "app" && rule.action === "beep";

function daysText(days) {
  if (days.length === 7) return t("settings.limits.every_day");
  if (days.join() === "0,1,2,3,4") return t("settings.limits.weekdays");
  if (days.join() === "5,6") return t("settings.limits.weekend");
  return days.map(dayName).join(", ");
}

// One kind's list ("site" or "app"), or both without one.
function renderLimits(kind) {
  if (!kind) { renderLimits("site"); renderLimits("app"); return; }
  const k = LIMITS[kind];
  const list = $(k.list);
  list.replaceChildren();
  k.rules.forEach((rule, i) => {
    const status = k.usage[rule.id];
    const row = el("div", "limit-row" + (rule.enabled ? "" : " off"));
    const head = el("div", "limit-head");
    const sw = el("label", "switch");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = rule.enabled;
    box.onchange = () => { rule.enabled = box.checked; renderLimits(kind); };
    sw.append(box, el("span", "knob"));
    sw.title = t(rule.enabled ? "settings.limits.turn_off" : "settings.limits.turn_on");
    const edit = el("button", "btn sm ghost", t("settings.limits.edit"));
    edit.type = "button";
    edit.onclick = () => openLimitEditor(kind, i);
    const x = el("button", "remove", "×");
    x.type = "button";
    x.title = t("settings.limits.remove");
    x.onclick = () => { k.rules.splice(i, 1); renderLimits(kind); };
    const name = el("span", "site", limitNames(kind, rule));
    if (kind === "app") name.title = rule.apps.join(", ");
    head.append(sw, name, edit, x);
    row.append(head);
    if (kind === "app") {
      row.append(el("div", "limit-line", rule.action === "beep"
        ? t("settings.limits.app_beeps", { n: rule.remind || 5 })
        : t("settings.limits.app_closes")));
    }
    if (status && status.blocked) {
      const what = beeps(kind, rule) ? "over_until" : kind === "app" ? "closed_until" : "blocked_until";
      row.append(el("div", "limit-tag", t("settings.limits." + what, { time: clockText(status.until_ms) })));
    }
    rule.budgets.forEach((b, j) => {
      const line = el("div", "limit-line");
      const live = status && status.budgets && status.budgets[j];
      if (live && live.budget_s === b.minutes * 60) {
        const used = Math.min(live.used_s, live.budget_s);
        const text = el("div");
        text.append(el("b", "", budgetText(b)),
          " · " + (live.active ? t("settings.limits.used", { used: Math.floor(used / 60), minutes: b.minutes }) : t("settings.limits.not_today")));
        const bar = el("div", "limit-bar" + (live.remaining_s <= 0 ? " full" : ""));
        const fill = el("i");
        fill.style.width = Math.round((100 * used) / live.budget_s) + "%";
        bar.append(fill);
        line.append(text, bar);
      } else {
        line.append(el("div", "", budgetText(b)));
      }
      row.append(line);
    });
    for (const w of rule.windows) {
      const line = el("div", "limit-line");
      line.append(el("div", "", t(beeps(kind, rule) ? "settings.limits.window_beeps" : "settings.limits.window_blocked",
        { days: daysText(w.days), from: w.from, to: w.to })));
      row.append(line);
    }
    list.append(row);
  });
  $(k.fields).classList.toggle("muted", !$(k.on).checked);
  renderTabDots();
}

async function refreshLimitUsage(kind) {
  if (!kind) { refreshLimitUsage("site"); refreshLimitUsage("app"); return; }
  const k = LIMITS[kind];
  const r = await api[k.usageCall]();
  if (!r.ok) return;
  k.usage = Object.fromEntries(r.rules.map((rule) => [rule.id, rule]));
  renderLimits(kind);
}
setInterval(async () => {
  if (!$("drawer").classList.contains("on") || settingsTab !== "limits") return;
  refreshLimitUsage();
  const r = await api.sync_info();
  if (r.ok) renderSync(r);
}, 5000);

// Sharing limits with other PCs through Chrome sync: one browser profile per
// PC does it (buzyist/app_sync.py, "sharing across PCs"). The profiles come from the
// extensions that have called buZyist; the first one is picked by itself.
let syncOwner = "";  // as picked here, saved with the rest

const agoText = (ms) => {
  const min = Math.round((Date.now() - ms) / 60000);
  if (min < 1) return t("settings.sync.ago.now");
  if (min < 60) return t("settings.sync.ago.min", { n: min });
  if (min < 48 * 60) return t("settings.sync.ago.h", { n: Math.round(min / 60) });
  return t("settings.sync.ago.days", { n: Math.round(min / 1440) });
};

function renderSync(info) {
  info = info || { instances: [], devices: [] };
  const select = $("s_sync_owner");
  if (document.activeElement !== select) {
    select.replaceChildren();
    const auto = el("option", "", t(info.instances.length ? "settings.sync.first_profile" : "settings.sync.waiting_extension"));
    auto.value = "";
    select.append(auto);
    for (const inst of info.instances) {
      const name = inst.email || t("settings.sync.unsigned_profile", { id: inst.id.slice(0, 4) });
      const opt = el("option", "", inst.connected ? name : t("settings.sync.last_seen", { name, ago: agoText(inst.seen_ms) }));
      opt.value = inst.id;
      select.append(opt);
    }
    if (syncOwner && !info.instances.some((i) => i.id === syncOwner)) {
      const gone = el("option", "", t("settings.sync.not_seen"));
      gone.value = syncOwner;
      select.append(gone);
    }
    select.value = syncOwner;
  }
  const owner = info.instances.find((i) => i.id === syncOwner);
  const status = $("syncStatus");
  const others = info.devices.map((d) => t("settings.sync.device", { name: d.name, ago: agoText(d.t) })).join(", ");
  const synced = !!info.last_sync_ms;
  let text;
  if (!owner) text = t("settings.sync.no_owner");
  else if (!owner.email) text = t("settings.sync.unsigned");
  else if (!synced) text = t("settings.sync.waiting_sync");
  else if (others) text = t("settings.sync.synced_others", { ago: agoText(info.last_sync_ms), others });
  else text = t("settings.sync.synced_alone", { ago: agoText(info.last_sync_ms) });
  status.textContent = text;
  const good = !!(owner && owner.email && synced);
  status.classList.toggle("ok", good);
  status.classList.toggle("bad", !good);
  syncMuted();
}
const syncMuted = () => $("syncFields").classList.toggle("muted", !$("s_sync_limits").checked && !$("s_sync_session").checked);
$("s_sync_owner").onchange = () => { syncOwner = $("s_sync_owner").value; };
$("s_sync_limits").onchange = $("s_sync_session").onchange = syncMuted;

let namesText = "";  // what's typed in the editor's websites (or apps) box

function openLimitEditor(kind, index) {
  closeLimitEditor();
  const fresh = kind === "site"
    ? { patterns: [], budgets: [{ minutes: 5, every: 1, unit: "hour", days: [0, 1, 2, 3, 4, 5, 6] }] }
    : { apps: [], action: "close", remind: 5, budgets: [{ minutes: 30, every: 1, unit: "day", days: [0, 1, 2, 3, 4, 5, 6] }] };
  const rule = index === undefined
    ? { enabled: true, windows: [], ...fresh }
    : JSON.parse(JSON.stringify(LIMITS[kind].rules[index]));
  limitDraft = { kind, index, rule };
  namesText = (kind === "site" ? rule.patterns : rule.apps).join(", ");
  renderLimitEditor();
}

function closeLimitEditor() {
  limitDraft = null;
  for (const k of Object.values(LIMITS)) {
    $(k.editor).classList.add("hidden");
    $(k.editor).replaceChildren();
    $(k.add).classList.remove("hidden");
  }
}

function numberInput(value, min, onchange) {
  const input = el("input", "input");
  input.type = "number";
  input.min = min;
  input.value = value;
  input.oninput = () => onchange(parseInt(input.value, 10) || 0);
  return input;
}

function removeButton(onclick, title) {
  const x = el("button", "remove", "×");
  x.type = "button";
  x.title = title;
  x.onclick = onclick;
  return x;
}

// Seven day toggles editing item.days (a time limit or blocked hours).
function dayButtons(item) {
  if (!item.days) item.days = [0, 1, 2, 3, 4, 5, 6];
  const wrap = el("div", "limit-days");
  DAYS.forEach((_, d) => {
    const b = el("button", "day", dayName(d));
    b.type = "button";
    b.setAttribute("aria-pressed", item.days.includes(d));
    b.onclick = () => {
      item.days = item.days.includes(d) ? item.days.filter((x) => x !== d) : [...item.days, d].sort();
      b.setAttribute("aria-pressed", item.days.includes(d));
    };
    wrap.append(b);
  });
  return wrap;
}

function renderLimitEditor() {
  const { kind, rule } = limitDraft;
  const k = LIMITS[kind];
  const box = $(k.editor);
  box.replaceChildren();
  box.classList.remove("hidden");
  $(k.add).classList.add("hidden");

  const names = el("input", "input");
  names.id = kind === "site" ? "limitSite" : "limitApps";
  names.spellcheck = false;
  names.placeholder = t(kind === "site" ? "settings.limits.editor.sites_placeholder" : "settings.limits.editor.apps_placeholder");
  names.value = namesText;
  names.oninput = () => { namesText = names.value; };
  box.append(names, el("div", "help", t(kind === "site" ? "settings.limits.editor.sites_help" : "settings.limits.editor.apps_help")));
  if (kind === "app") suggestApps(names, { multi: true, skip: (a) => a.protected && rule.action === "close" });
  box.onkeydown = (e) => {
    if (e.key === "Enter" && e.target.tagName !== "BUTTON" && e.target.tagName !== "SELECT") {
      e.preventDefault(); e.stopPropagation(); commitLimitDraft();
    }
  };

  const hoursHead = el("h4");
  const addWindow = el("button", "btn sm");
  const hoursText = () => {
    hoursHead.textContent = t(beeps(kind, rule) ? "settings.limits.editor.hours_beep" : "settings.limits.editor.hours_blocked");
    addWindow.textContent = t(beeps(kind, rule) ? "settings.limits.editor.add_hours" : "settings.limits.editor.add_blocked_hours");
  };
  if (kind === "app") {
    const row = el("div", "limit-sub");
    const action = el("select", "input");
    for (const [value, text] of [["close", t("settings.limits.editor.action_close")], ["beep", t("settings.limits.editor.action_beep")]]) {
      const o = el("option", "", text);
      o.value = value;
      action.append(o);
    }
    action.value = rule.action;
    if (!rule.remind) rule.remind = 5;
    const remind = el("span", "limit-sub");
    remind.append(t("settings.limits.editor.again_every"), numberInput(rule.remind, REMIND_MIN, (n) => { rule.remind = n; }), t("settings.min"));
    remind.classList.toggle("hidden", rule.action !== "beep");
    action.onchange = () => {
      rule.action = action.value;
      remind.classList.toggle("hidden", rule.action !== "beep");
      hoursText();
    };
    row.append(t("settings.limits.editor.when_reached"), action, remind);
    box.append(row);
  }

  box.append(el("h4", "", t("settings.limits.editor.time_allowed")));
  rule.budgets.forEach((b, i) => {
    const row = el("div", "limit-sub");
    row.append(dayButtons(b));
    const amount = el("div", "limit-sub");
    const unit = el("select", "input");
    for (const u of ["minute", "hour", "day", "week"]) {
      const o = el("option", "", t("settings.limits.units." + u));
      o.value = u;
      o.selected = u === b.unit;
      unit.append(o);
    }
    unit.onchange = () => { b.unit = unit.value; };
    amount.append(numberInput(b.minutes, 1, (n) => { b.minutes = n; }), t("settings.limits.editor.min_every"),
                  numberInput(b.every, 1, (n) => { b.every = n; }), unit,
                  removeButton(() => { rule.budgets.splice(i, 1); renderLimitEditor(); }, t("settings.limits.editor.remove_budget")));
    const group = el("div", "limit-group");
    group.append(amount, row);
    box.append(group);
  });
  const addBudget = el("button", "btn sm", t("settings.limits.editor.add_budget"));
  addBudget.type = "button";
  addBudget.onclick = () => { rule.budgets.push({ minutes: 30, every: 1, unit: "day", days: [0, 1, 2, 3, 4, 5, 6] }); renderLimitEditor(); };
  box.append(addBudget);

  box.append(hoursHead);
  rule.windows.forEach((w, i) => {
    const row = el("div", "limit-sub");
    row.append(dayButtons(w));
    const from = el("input", "input");
    from.type = "time";
    from.value = w.from;
    from.oninput = () => { w.from = from.value; };
    const to = el("input", "input");
    to.type = "time";
    to.value = w.to;
    to.oninput = () => { w.to = to.value; };
    row.append(from, t("settings.limits.editor.to"), to, removeButton(() => { rule.windows.splice(i, 1); renderLimitEditor(); }, t("settings.limits.editor.remove_hours")));
    box.append(row);
  });
  addWindow.type = "button";
  addWindow.onclick = () => { rule.windows.push({ days: [0, 1, 2, 3, 4], from: "08:00", to: "17:00" }); renderLimitEditor(); };
  hoursText();
  box.append(addWindow);
  box.append(el("div", "help", t("settings.limits.editor.hours_help")));

  const actions = el("div", "limit-actions");
  const cancel = el("button", "btn ghost", t("settings.cancel"));
  cancel.type = "button";
  cancel.onclick = closeLimitEditor;
  const done = el("button", "btn primary", t(limitDraft.index === undefined ? "settings.limits.editor.add_limit" : "settings.limits.editor.done"));
  done.type = "button";
  done.onclick = commitLimitDraft;
  actions.append(cancel, done);
  box.append(actions);
  names.focus();
}

// The apps typed in the editor as program names (clean_app_limit); null (with a toast) if one can't be.
function draftApps(action) {
  const key = (n) => n.toLowerCase().replace(/\.exe$/, "");
  const apps = [];
  for (const part of namesText.split(/[,;\n]+/).map((p) => p.trim().replace(/^"|"$/g, "")).filter(Boolean)) {
    if (/[\\/]/.test(part)) { toast(t("settings.apps.not_program"), true); return null; }
    const exe = /\.exe$/i.test(part) ? part : part + ".exe";
    if (action === "close" && PROTECTED_EXES.has(key(exe))) {
      toast(t("settings.limits.check.protected", { app: exe }), true);
      return null;
    }
    if (!apps.some((a) => key(a) === key(exe))) apps.push(exe);
  }
  if (!apps.length) { toast(t("settings.limits.check.no_app"), true); return null; }
  if (apps.length > 20) { toast(t("settings.limits.check.too_many_apps", { n: 20 }), true); return null; }
  return apps;
}

// The websites typed in the editor as patterns; null (with a toast) if one isn't a website.
function draftSites() {
  const patterns = [];
  for (const part of namesText.split(/[\s,;]+/).filter(Boolean)) {
    const pattern = cleanSitePattern(part);
    if (pattern === null) { toast(t("settings.limits.check.not_site", { text: part }), true); return null; }
    if (pattern && !patterns.includes(pattern)) patterns.push(pattern);
  }
  if (!patterns.length) { toast(t("settings.limits.check.no_site"), true); return null; }
  if (patterns.length > 20) { toast(t("settings.limits.check.too_many_sites", { n: 20 }), true); return null; }
  return patterns;
}

// Move the editor's rule into its list. False (with a toast) if it isn't usable yet.
function commitLimitDraft() {
  const { kind, index, rule } = limitDraft;
  const k = LIMITS[kind];
  const names = kind === "site" ? draftSites() : draftApps(rule.action);
  if (!names) return false;
  if (beeps(kind, rule) && !(rule.remind >= REMIND_MIN && rule.remind <= REMIND_MAX)) {
    toast(t("settings.limits.check.remind", { min: REMIND_MIN, max: REMIND_MAX }), true);
    return false;
  }
  if (!rule.budgets.length && !rule.windows.length) { toast(t("settings.limits.check.empty"), true); return false; }
  if (rule.budgets.some((b) => !b.days.length)) { toast(t("settings.limits.check.budget_days"), true); return false; }
  if (rule.budgets.some((b) => b.minutes < 1 || b.every < 1)) { toast(t("settings.limits.check.budget_minutes"), true); return false; }
  if (rule.windows.some((w) => !w.days.length || !w.from || !w.to)) { toast(t("settings.limits.check.hours"), true); return false; }
  if (k.rules.length >= 30 && index === undefined) {
    toast(t(kind === "site" ? "settings.limits.check.too_many_site_limits" : "settings.limits.check.too_many_app_limits", { n: 30 }), true);
    return false;
  }
  if (kind === "site") rule.patterns = names;
  else rule.apps = names;
  if (index === undefined) k.rules.push(rule);
  else k.rules[index] = rule;
  closeLimitEditor();
  renderLimits(kind);
  return true;
}

$("addLimitBtn").onclick = () => openLimitEditor("site");
$("addAppLimitBtn").onclick = () => openLimitEditor("app");
$("s_site_limits_on").onchange = () => renderLimits("site");
$("s_app_limits_on").onchange = () => renderLimits("app");

for (const r of document.querySelectorAll('input[name="site_mode"]')) r.onchange = () => renderBlockedSites();
$("addBlockedSiteBtn").onclick = addBlockedSite;
$("addBlockedSite").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); addBlockedSite(); }
});
$("s_block_sites").onchange = () => renderBlockedSites();
$("extChip").onclick = () => openSettings("sites");
$("openExtensionsPage").onclick = () => $("installExtension").click();
$("openExtensionFolder").onclick = () => api.open_extension_folder();
$("copyExtensionPath").onclick = async () => {
  const r = await api.copy_extension_path();
  toast(t(r.ok ? "settings.ext.copied" : "settings.ext.copy_failed"), !r.ok);
};
$("installExtension").onclick = async () => {
  const r = await api.install_extension();
  if (!r.ok) return toast(r.error, true);
  const copied = r.copied ? "_copied" : "";
  if (r.browser) toast(t("settings.ext.opened" + copied, { browser: r.browser }));
  else toast(t("settings.ext.not_found" + copied), true);
};

function closeSettings() {
  $("drawer").classList.remove("on");
  if (!(state && state.ended)) $("scrim").classList.remove("on");
}

function formValues(names) {
  const out = {};
  for (const input of $("settingsForm").querySelectorAll("input[name], select[name]")) {
    if (names && !names.includes(input.name)) continue;
    if (input.type === "radio") { if (input.checked) out[input.name] = input.value; continue; }
    out[input.name] = input.type === "checkbox" ? input.checked : input.value;
  }
  return out;
}

$("openSettings").onclick = () => openSettings();
$("openData").onclick = () => api.open_data_folder();
$("openRepo").onclick = () => api.open_repo();
$("closeSettings").onclick = closeSettings;
$("updateChip").onclick = async () => {
  await openSettings("about");
};
$("checkUpdate").onclick = async () => {
  const out = $("updateResult");
  out.className = "test-result";
  out.textContent = t("settings.about.checking");
  $("checkUpdate").disabled = true;
  const result = await api.check_update();
  $("checkUpdate").disabled = false;
  if (!result.ok) { out.className = "test-result bad"; out.textContent = result.error; return; }
  await poll();
  if (!result.update) { out.className = "test-result ok"; out.textContent = t("settings.about.latest"); }
};
$("installUpdate").onclick = async () => {
  const u = state && state.update;
  if (!u) return;
  if (!u.installable) return api.open_url(u.url);
  const ask = state.bar.local || state.bar.offline ? "settings.about.confirm" : "settings.about.confirm_bar";
  if (state.session && !confirm(t(ask))) return;
  $("installUpdate").disabled = true;
  $("installUpdate").textContent = t("settings.about.installing");
  const result = await api.install_update();
  if (!result.ok) { await poll(); return toast(result.error, true); }
  toast(t("settings.about.installing_toast", { version: u.version }));
};
$("releaseNotes").onclick = () => state && state.update && api.open_url(state.update.url);
$("cancelSettings").onclick = closeSettings;
$("scrim").onclick = () => {
  if (editing !== undefined) closeFilterEditor();
  else if ($("drawer").classList.contains("on")) closeSettings();
};
$("revealToken").onclick = () => {
  const i = $("s_todoist_token");
  i.type = i.type === "password" ? "text" : "password";
};
$("settingsForm").onsubmit = (e) => { e.preventDefault(); $("saveSettings").click(); };
$("saveSettings").onclick = async () => {
  addBlockedApp(); // a name typed but not yet added still counts
  addBlockedSite(); // same for a website typed but not yet added
  addActivity(); // and an activity
  if (limitDraft && !commitLimitDraft()) return; // a limit being edited counts too
  // A new language reloads the page (Python does it): open Settings again after.
  const relang = $("s_language").value !== I18N.lang;
  if (relang) sessionStorage.setItem("reopenSettings", "general");
  const result = await api.save_settings({ ...formValues(), ...secretClears(), blocked_apps: blockedApps, blocked_sites: siteLists.block, allowed_sites: siteLists.allow, site_limits: LIMITS.site.rules, app_limits: LIMITS.app.rules, activities: activityRows, bar_screens: barScreens, call_activity: callActivity, call_ignore_apps: callIgnore(), sync_owner: syncOwner });
  if (!result.ok) {
    sessionStorage.removeItem("reopenSettings");
    return toast(result.error, true);
  }
  closeSettings();
  toast(t("settings.saved"));
  await poll();
  loadTasks();
};
$("testBar").onclick = async () => {
  const out = $("testResult");
  out.className = "test-result";
  out.textContent = t("settings.bar.testing");
  const { clear_busybar_pin } = secretClears();
  const saved = await api.save_settings({ ...formValues(["use_busybar", "busybar_ip", "busybar_pin", "usb_fallback"]), clear_busybar_pin });
  if (!saved.ok) { out.className = "test-result bad"; out.textContent = saved.error; return; }
  const result = await api.test_bar();
  out.className = "test-result " + (result.ok ? "ok" : "bad");
  out.textContent = !result.ok ? result.error
    : t(result.version ? "settings.bar.connected_firmware" : "settings.bar.connected", { via: result.via, version: result.version });
  loadBarPower();
};

// ------------------------------------------------------------- keyboard

document.addEventListener("keydown", (e) => {
  const inDrawer = $("drawer").classList.contains("on");
  const inEditor = editing !== undefined;
  const menu = $("taskMenu");
  if (menu) {
    const items = [...menu.querySelectorAll("button")];
    const at = items.indexOf(document.activeElement);
    if (e.key === "Escape") closeTaskMenu();
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
    } else if (e.key === "Enter" && at >= 0) items[at].click();
    else if (e.key !== "Tab") return;
    e.preventDefault();
    return;
  }
  if (e.key === "Escape") {
    if (inEditor) return closeFilterEditor();
    if (inDrawer) return closeSettings();
    if (state && state.ended) return $("endedClose").click();
    if ($("search").value) { $("search").value = ""; renderTasks(); return; }
    api.hide_main();
    return;
  }
  if (inEditor || inDrawer || (state && state.ended)) return;
  if (e.ctrlKey && !e.altKey && /^[1-9]$/.test(e.key)) {
    e.preventDefault();
    const f = filters[+e.key - 1];
    if (f) selectFilter(f.name);
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    sel = Math.max(0, Math.min(shown.length - 1, sel + (e.key === "ArrowDown" ? 1 : -1)));
    markSelection(true);
  } else if (e.key === "Enter" && shown[sel] && !e.target.closest(".stepper")) {
    e.preventDefault();
    startTask(shown[sel], $("list").querySelectorAll(".task")[sel]);
  } else if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey && e.target.tagName !== "INPUT") {
    $("search").focus();
  }
});
$("search").addEventListener("input", () => { sel = 0; renderTasks(); });
$("refresh").onclick = () => loadTasks();

// ------------------------------------------------------------- startup

// Polling: every second while the window is on screen, every 15 s while it
// sits in the tray. Python says which through App.setVisible on every hide
// and show (it hides windows with SW_HIDE, which document.hidden doesn't
// always notice), and onShown means visible too.
let visible = true;
const onScreen = () => visible && !document.hidden;

// Overlays appear without their fade while the window is hidden and for a
// moment after it shows (body.instant in style.css): the fade may not run
// until the first input, and the end-of-session prompt would stay invisible.
let instantTimer = 0;
function instantOverlays(hidden) {
  clearTimeout(instantTimer);
  document.body.classList.add("instant");
  if (!hidden) instantTimer = setTimeout(() => { if (onScreen()) document.body.classList.remove("instant"); }, 1500);
}

window.App = {
  setVisible(v) {
    visible = !!v;
    instantOverlays(!visible);
    if (visible) poll();
  },
  onShown(focusSearch, fresh) {
    visible = true;
    instantOverlays(false);
    if (fresh) applyState(fresh);  // what Python sees now, before any poll answers
    if (Date.now() - lastTaskLoad > 30000) loadTasks(true);
    poll();
    // A window that was hidden in the tray can still be throttled for a
    // moment after it shows; poll again so the end-of-session prompt
    // doesn't wait for a click.
    setTimeout(poll, 200);
    setTimeout(poll, 800);
    if (focusSearch) {
      $("search").focus();
      $("search").select();
    }
  },
};

// Re-sync as soon as the window is restored or focused.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return instantOverlays(true);
  instantOverlays(false);
  poll();
});
window.addEventListener("focus", () => { visible = true; poll(); });  // focused means on screen
window.addEventListener("resize", () => poll());

async function boot() {
  api = window.pywebview.api;
  await loadI18n(api);
  await poll();
  const reopen = sessionStorage.getItem("reopenSettings");  // reloaded for a new language
  sessionStorage.removeItem("reopenSettings");
  if (reopen && !state.needs_setup) {
    openSettings(reopen);
    toast(t("settings.saved"));
  }
  if (state.needs_setup) {
    showEmpty(t("main.tasks.welcome"), t("main.tasks.welcome_text"), true);
    tasksWereOff = true;
    openSettings();
  } else if (state.todoist) {
    loadTasks();
  }
  setInterval(() => { if (onScreen()) poll(); }, 1000);
  setInterval(() => { if (!onScreen()) poll(); }, 15000);  // hidden: a slow safety net
  setInterval(() => { if (onScreen()) renderTimer(); }, 250);
  // keep the list fresh while the window stays open
  setInterval(() => { if (!document.hidden && Date.now() - lastTaskLoad > 300000) loadTasks(true); }, 60000);
  setInterval(() => { if (!document.hidden && !loadingTasks) resortTasks(); }, 60000);
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
