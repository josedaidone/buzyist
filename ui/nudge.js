"use strict";

// A "beep" app limit's popup: Python fills it (Nudge.show) and shows it
// without taking the focus; it goes after a while, unless the mouse is on it.
const $ = (id) => document.getElementById(id);
const SHOW_MS = 20000;  // on screen this long
const LEAVE_MS = 5000;  // and this long after the mouse leaves it
const DONE_MS = 1800;   // "Beeps muted until ..." before it goes
let api = null;
let hideTimer = 0;
let hovered = false;

function hideIn(ms) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => api && api.hide_nudge(), ms);
}

function show(data) {
  $("nTitle").textContent = $("nTitle").title = data.title;
  $("nText").textContent = $("nText").title = data.text;
  $("nMute").classList.remove("hidden");
  $("nDone").classList.add("hidden");
  for (const b of document.querySelectorAll("[data-mute]")) b.disabled = false;
  hovered = false;  // mouseenter tells again if the pointer is on it
  hideIn(SHOW_MS);
}

function mutedText(until) {
  const end = new Date(until), midnight = new Date();
  midnight.setHours(24, 0, 0, 0);
  return end.getTime() === midnight.getTime()
    ? t("nudge.muted_tomorrow")
    : t("nudge.muted_until", { time: formatTime(end, { hour: "2-digit", minute: "2-digit" }) });
}

async function mute(choice) {
  for (const b of document.querySelectorAll("[data-mute]")) b.disabled = true;
  const r = await api.mute_beeps(choice);
  $("nMute").classList.add("hidden");
  $("nDone").classList.remove("hidden");
  $("nDone").textContent = r.ok ? mutedText(r.muted_until) : r.error;
  hideIn(DONE_MS);
}

window.Nudge = { show };

async function boot() {
  api = window.pywebview.api;
  await loadI18n(api);
  for (const b of document.querySelectorAll("[data-mute]")) b.onclick = () => mute(b.dataset.mute);
  $("nClose").onclick = () => { clearTimeout(hideTimer); api.hide_nudge(); };
  $("nudge").addEventListener("mouseenter", () => { hovered = true; clearTimeout(hideTimer); });
  $("nudge").addEventListener("mouseleave", () => { hovered = false; hideIn(LEAVE_MS); });
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener("pywebviewready", boot, { once: true });
