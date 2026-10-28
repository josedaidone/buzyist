"use strict";

const $ = (id) => document.getElementById(id);
// The phase chip's text: common.phase.<key>, as in ui/main.js.
const PHASE_KEYS = { idle: "ready", paused: "paused", work: "focus", rest: "break", call: "call" };
const ADD_MINUTES = 5;  // ADD_MINUTES in buzyist/constants.py (a test checks)
let api = null;
let state = null;

function clock(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function render() {
  if (!state) return;
  const tm = state.timer, s = state.session;
  const phase = !tm ? "idle" : tm.phase === "call" ? "call" : tm.paused ? "paused" : tm.phase === "work" ? "work" : "rest";
  document.body.dataset.phase = phase;
  const call = phase === "call";  // no end: the time so far, the line full
  const left = tm ? Math.max(0, tm.paused ? tm.left_ms : tm.left_ms - (Date.now() - state.now)) : 0;
  const time = call ? clock(tm.elapsed_ms + (Date.now() - state.now)) : tm ? clock(left) : "--:--";
  $("mTime").textContent = time;
  $("mTimeS").textContent = tm ? time : t("common.phase.ready");
  $("mProgress").style.width = call ? "100%" : tm ? `${(100 * (1 - left / tm.total_ms)).toFixed(2)}%` : "0";
  $("mPhase").textContent = t("common.phase." + PHASE_KEYS[phase]);
  $("mRound").textContent = tm && !call && tm.rounds > 1 ? `· ${tm.round}/${tm.rounds}` : "";
  const r = !s && tm && state.remote;  // started on another PC
  const task = s ? s.task.content : r ? r.task.content
    : tm ? t(state.bar.local || state.bar.offline ? "mini.timer_running" : "mini.on_bar") : t("mini.no_session");
  const project = s ? s.task.project : r ? r.task.project || t("mini.by", { by: r.by }) : "";
  $("mPauseIcon").setAttribute("href", tm && tm.paused ? "#i-play" : "#i-pause");
  $("mPause").title = t(tm && tm.paused ? "mini.resume" : "mini.pause");
  $("mAdd").title = t("mini.add", { n: ADD_MINUTES });
  $("mStop").style.display = tm ? "" : "none";
  for (const id of ["mPause", "mAdd", "mSkip"]) $(id).style.display = tm && !call ? "" : "none";
  // Done / carry over: the running session's own Todoist task.
  const ownTask = !!(s && !s.call && s.task.id && state.todoist);
  $("mComplete").style.display = $("mCarry").style.display = ownTask ? "" : "none";
  if (!ownTask) unconfirm();
  if (phase !== "paused") $("mini").classList.remove("alert");
  const line = $("mTask");
  if (errorUntil > Date.now()) return;  // an error is on show instead
  line.classList.remove("error", "ask");
  const b = document.createElement("b");
  b.textContent = task;
  line.replaceChildren(b, project ? ` · ${project}` : "");
  line.title = project ? `${task} · ${project}` : task;
}

// An error from a button, or the question of a two-step button, on the
// task's line for a while ("error" / "ask").
let errorUntil = 0, noteTimer = 0;
function showNote(text, kind = "error", ms = 5000) {
  const line = $("mTask");
  errorUntil = Date.now() + ms;
  line.classList.remove("error", "ask");
  line.classList.add(kind);
  line.textContent = line.title = text;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(render, ms + 50);
}
function clearNote() {
  errorUntil = 0;
  $("mTask").classList.remove("error", "ask");
  render();
}
const showError = (text) => showNote(text);

async function poll() {
  try {
    state = await api.get_state();
    render();
  } catch (err) {
    /* the window is being torn down */
  }
}

// Buttons must not start a window drag.
for (const b of document.querySelectorAll(".m-card button")) {
  b.addEventListener("mousedown", (e) => e.stopPropagation());
  b.addEventListener("dblclick", (e) => e.stopPropagation());  // nor open the main window
}

// One call at a time; the buttons wait for it, then the state is read again.
let busy = false;
async function act(call) {
  if (busy) return;
  busy = true;
  for (const b of document.querySelectorAll(".m-card button")) b.disabled = true;
  try {
    const result = await call();
    if (result && result.ok === false) showError(result.error);
  } catch (err) { /* the window is being torn down */ }
  busy = false;
  for (const b of document.querySelectorAll(".m-card button")) b.disabled = false;
  poll();
}

$("mPause").onclick = () => act(() => api.control(state.timer && state.timer.paused ? "resume" : "pause"));
$("mStop").onclick = () => act(() => api.control("stop"));
$("mAdd").onclick = () => act(() => api.control("add"));
$("mSkip").onclick = () => act(() => api.control("skip"));
$("mOpen").onclick = $("mGo").onclick = () => api.show_main();
$("mHide").onclick = () => api.hide_mini();
$("mini").addEventListener("dblclick", () => api.show_main());

// Done and carry over close the task in Todoist and end the session: the
// first click turns the button green and asks again in its tooltip, the
// second (within a few seconds) does it.
let confirming = null, confirmTimer = 0;
function unconfirm() {
  clearTimeout(confirmTimer);
  if (confirming) {
    confirming.classList.remove("confirm");
    confirming = null;
    if ($("mTask").classList.contains("ask")) clearNote();
  }
}
function twoStep(button, followup) {
  button.onclick = () => {
    if (confirming !== button) {
      unconfirm();
      confirming = button;
      button.classList.add("confirm");
      showNote(t(followup ? "mini.confirm_carry" : "mini.confirm_done"), "ask", 3500);
      confirmTimer = setTimeout(unconfirm, 3500);
      return;
    }
    unconfirm();
    act(() => api.complete_running(followup));
  };
}
twoStep($("mComplete"), false);
twoStep($("mCarry"), true);

// Compact (the time only) until the mouse rests on it, then Python grows the
// window (Api.mini_expand) and the task and the buttons show; it shrinks back
// a moment after the mouse leaves. The class follows the size: set after
// growing, before shrinking, so neither layout is drawn in the other's window.
const EXPAND_MS = 150, COLLAPSE_MS = 450;
let expanded = false, wantExpanded = false, hoverTimer = 0;

async function applySize() {
  const want = wantExpanded;
  if (want === expanded) return;
  expanded = want;
  if (!want) document.body.classList.remove("expanded");
  try { await api.mini_expand(want); } catch (err) { /* the window is being torn down */ }
  if (want && wantExpanded) document.body.classList.add("expanded");
  if (wantExpanded !== expanded) applySize();
}

function hover(on, delay) {
  wantExpanded = on;
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(applySize, delay);
}

document.documentElement.addEventListener("mouseenter", () => hover(true, EXPAND_MS));
document.documentElement.addEventListener("mouseleave", () => { unconfirm(); hover(false, COLLAPSE_MS); });

// A window that isn't focused can miss its mouseleave: ask where the mouse is.
async function checkHover() {
  if (!expanded) return;
  try {
    const r = await api.mini_hovered();
    if (r && r.ok && !r.hovered && expanded) hover(false, 0);
  } catch (err) { /* the window is being torn down */ }
}

// A long pause: flash the whole mini timer for a few seconds (Python beeps).
let alertTimer = 0;
function alertPause() {
  const m = $("mini");
  m.classList.remove("alert");
  void m.offsetWidth; // restart the animation
  m.classList.add("alert");
  clearTimeout(alertTimer);
  alertTimer = setTimeout(() => m.classList.remove("alert"), 3600);
  poll();
}

// Polling: every second while the mini timer is on screen, every 15 s while
// it's hidden. Python calls Mini.setVisible on every show and hide.
let visible = true;
const onScreen = () => visible && !document.hidden;

function setVisible(v) {
  visible = !!v;
  if (!visible) { wantExpanded = expanded = false; clearTimeout(hoverTimer); document.body.classList.remove("expanded"); }
  if (visible) poll();
}

window.Mini = { refresh: poll, alert: alertPause, setVisible };

async function boot() {
  api = window.pywebview.api;
  await loadI18n(api);
  poll();
  setInterval(() => { if (onScreen()) { poll(); checkHover(); } }, 1000);
  setInterval(() => { if (!onScreen()) poll(); }, 15000);  // hidden: a slow safety net
  setInterval(() => { if (onScreen()) render(); }, 250);
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
