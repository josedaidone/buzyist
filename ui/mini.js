"use strict";

const $ = (id) => document.getElementById(id);
const RING = 2 * Math.PI * 33;
// The phase chip's text: common.phase.<key>, as in ui/main.js.
const PHASE_KEYS = { idle: "ready", paused: "paused", work: "focus", rest: "break", call: "call" };
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
  const call = phase === "call";  // no timer: the time so far, on a full ring
  const left = tm ? Math.max(0, tm.paused ? tm.left_ms : tm.left_ms - (Date.now() - state.now)) : 0;
  $("mTime").textContent = call ? clock(tm.elapsed_ms + (Date.now() - state.now)) : tm ? clock(left) : "--:--";
  $("mProgress").style.strokeDasharray = RING;
  $("mProgress").style.strokeDashoffset = call ? 0 : tm ? RING * (1 - left / tm.total_ms) : RING;
  $("mPhase").textContent = t("common.phase." + PHASE_KEYS[phase]);
  $("mRound").textContent = call ? t("mini.until_end") : tm ? t("mini.round", { round: tm.round, rounds: tm.rounds }) : "";
  const r = !s && tm && state.remote;  // started on another PC
  $("mTask").textContent = s ? s.task.content : r ? r.task.content
    : tm ? t(state.bar.local || state.bar.offline ? "mini.timer_running" : "mini.on_bar") : t("mini.no_session");
  $("mProject").textContent = s ? s.task.project : r ? r.task.project || t("mini.by", { by: r.by }) : "";
  $("mPauseIcon").setAttribute("href", tm && tm.paused ? "#i-play" : "#i-pause");
  $("mPause").title = t(tm && tm.paused ? "mini.resume" : "mini.pause");
  $("mStop").style.display = tm ? "" : "none";
  $("mPause").style.display = tm && !call ? "" : "none";
  if (phase !== "paused") $("mini").classList.remove("alert");
}

async function poll() {
  try {
    state = await api.get_state();
    render();
  } catch (err) {
    /* the window is being torn down */
  }
}

// Buttons must not start a window drag.
for (const b of document.querySelectorAll(".m-actions button")) {
  b.addEventListener("mousedown", (e) => e.stopPropagation());
}
$("mPause").onclick = async () => {
  await api.control(state.timer && state.timer.paused ? "resume" : "pause");
  poll();
};
$("mStop").onclick = async () => { await api.control("stop"); poll(); };
$("mOpen").onclick = () => api.show_main();
$("mHide").onclick = () => api.hide_mini();
$("mini").addEventListener("dblclick", () => api.show_main());

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
  if (visible) poll();
}

window.Mini = { refresh: poll, alert: alertPause, setVisible };

async function boot() {
  api = window.pywebview.api;
  await loadI18n(api);
  poll();
  setInterval(() => { if (onScreen()) poll(); }, 1000);
  setInterval(() => { if (!onScreen()) poll(); }, 15000);  // hidden: a slow safety net
  setInterval(() => { if (onScreen()) render(); }, 250);
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
