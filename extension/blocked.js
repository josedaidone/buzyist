// The page a blocked site is redirected to. Two kinds of block land here:
//
//  * focus: a pomodoro work phase is running. Shows the task and a live
//    countdown, and sends the tab back the moment focus ends.
//  * limit (?l=<rule id>): a site's time budget is used up, or it's inside
//    blocked hours. Shows why and when it opens again, and offers a short pass.
//
// It asks the extension's background worker where things stand, so it also
// works (from the last known state) while buZyist is closed.

// The original URL is everything after "u=" (passed raw, so no decoding).
const marker = location.href.search(/[?&]u=/);
const original = marker >= 0 ? location.href.slice(marker + 3) : "";
const limitId = new URLSearchParams(location.search).get("l") || "";
const $ = (id) => document.getElementById(id);

try {
  $("url").textContent = original ? new URL(original).hostname : "";
} catch (e) {
  $("url").textContent = "";
}

function goBack() {
  if (original) location.replace(original);
  else history.length > 1 ? history.back() : location.replace("about:blank");
}

// In the app language (i18n.js): "17:00", or "Thu 08:00" when it isn't today.
const clock = formatClock;
const when = (ms) => {
  const day = new Date(ms).toDateString() === new Date().toDateString() ? "" : formatWeekday(ms) + " ";
  return day + clock(ms);
};
const minutes = (s) => Math.round(s / 60);

let endsAt = 0;
let passMinutes = 2;

// Ticks the focus countdown. A limit page's line ("Opens again at") is left
// alone: clearing it here between asks made it blink.
function renderCount() {
  if (limitId) return;
  const count = $("count");
  if (!endsAt) { count.textContent = ""; return; }
  const mins = Math.ceil(Math.max(0, endsAt - Date.now()) / 60000);
  setNodes(count, tBold("blocked.focus_until", { time: clock(endsAt), n: mins }));
}

// Replaces a line's content (text with bold parts) only when it changed.
function setNodes(node, parts) {
  const next = document.createElement("p");
  next.append(...parts);
  if (node.innerHTML !== next.innerHTML) node.replaceChildren(...next.childNodes);
}

// Sets text only when it changed, so nothing flickers on a re-render.
function setText(id, text) {
  if ($(id).textContent !== text) $(id).textContent = text;
}

// Tell the worker this page showed a block (buZyist's Stats tab and the popup
// count them), once: history.state survives a reload and a restored tab.
function countBlock() {
  if (history.state && history.state.counted) return;
  history.replaceState({ counted: true }, "");
  let host = "";
  try { host = new URL(original).hostname.replace(/^www\./, ""); } catch (e) { return; }
  chrome.runtime.sendMessage({ type: "blocked", kind: limitId ? "limit" : "focus", host }).catch(() => {});
}

async function render(status) {
  if (!status) return; // no answer this round: keep what's shown
  // buZyist's language, as the worker last heard it (English until then).
  if (await useLanguage(status.language)) applyI18n();
  const { focus, limit } = status;
  if (limitId) {
    if (!limit) { goBack(); return; }
    countBlock();
    const eyebrow = $("eyebrow").lastChild;
    if (eyebrow.textContent !== t("blocked.limit")) eyebrow.textContent = t("blocked.limit");
    $("eyebrow").classList.add("limit");
    if (limit.reason === "window") {
      setText("title", t("blocked.window_title"));
      setText("task", t("blocked.window_line", { pattern: limit.pattern }));
    } else {
      setText("title", t("blocked.budget_title"));
      setText("task", t("blocked.budget_line", {
        used: minutes(limit.used_s), budget: minutes(limit.budget_s), pattern: limit.pattern, label: limitLabel(limit.label),
      }));
    }
    endsAt = 0;
    setNodes($("count"), tBold("blocked.opens_at", { when: when(limit.until) }));
    passMinutes = limit.pass_minutes;
    setText("pass", t("blocked.pass", { n: passMinutes }));
    $("pass").classList.remove("hidden");
    return;
  }
  if (!focus.active) { goBack(); return; }
  countBlock();
  const eyebrow = $("eyebrow").lastChild;
  const label = focus.activity ? t("blocked.focus_activity", { activity: focus.activity }) : t("blocked.focus");
  if (eyebrow.textContent !== label) eyebrow.textContent = label;
  setText("task", focus.task || "");
  endsAt = focus.ends_at_ms || 0;
  renderCount();
}

async function check() {
  try {
    await render(await chrome.runtime.sendMessage({ type: "status", limit: limitId }));
  } catch (e) {
    // The worker is waking up; ask again on the next round.
  }
}

$("pass").onclick = async () => {
  $("pass").disabled = true;
  try {
    await render(await chrome.runtime.sendMessage({ type: "grant", id: limitId, limit: limitId }));
  } catch (e) { /* the next check picks it up */ }
  $("pass").disabled = false;
};

check();
setInterval(check, 4000);   // re-ask buZyist
setInterval(renderCount, 1000); // tick the countdown between asks
