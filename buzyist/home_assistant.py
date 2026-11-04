"""
Home Assistant (use_home_assistant, off by default): what buZyist is doing,
POSTed as JSON to a webhook that the BUSY Bar Apps integration for Home
Assistant (busybar-ha, its "buZyist" entry) registers and shows the URL of.
It turns each message into entities (state, focus, call, activity, task, the
phase's end) and events. Only this way: Home Assistant never drives buZyist.

`status_payload` is the message; HomeAssistantLink (its own thread, asleep
while off) sends it when it changes, every HA_HEARTBEAT_S anyway, and a
`{"closed": true}` on quit so the entities go unavailable at once.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from buzyist.constants import (
    APP,
    APP_NAME,
    DEVICE_NAME,
    HA_HEARTBEAT_S,
    HA_PROTOCOL,
    HA_RETRY_S,
    HA_TIMEOUT_S,
    app_version,
    now_ms,
)
from buzyist.i18n import app_error

if TYPE_CHECKING:
    from buzyist.app import App

log = logging.getLogger(APP)

MAX_URL = 500
# A phase's end that moves by less than this (rounding, a poll late) isn't news.
END_SLACK_MS = 5000


def clean_ha_url(value) -> str:
    """The webhook URL as saved: "" (none) or an http(s) URL with a host; AppError otherwise."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if len(text) > MAX_URL or parts.scheme not in ("http", "https") or not parts.hostname:
        raise app_error("errors.ha.bad_url")
    return text


def iso(ms: int | None) -> str | None:
    """Epoch ms as an ISO 8601 time in UTC, which Home Assistant's timestamp sensors take."""
    return None if not ms else datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="seconds")


def status_payload(app: App, ended: dict | None = None) -> dict:
    """
    What buZyist is doing, as Home Assistant gets it (version HA_PROTOCOL):

    - `state`: "idle", "focus" (a work phase), "break", "paused" or "call";
      `focus` whether a work phase or call is on (App.focus: what blocks
      apps and sites, a timer started on the bar included);
    - `session`: null, or what runs: `kind` ("pomodoro" or "call"), `source`
      ("here", or "bar" for a timer started on the bar or another PC),
      `task`, `activity` ({id, name, detail} or null), the phase and its
      end (`ends_at`, null while paused or on a call), round and rounds,
      `apps` on a call;
    - `ended`: the last session that ended ({reason, task, activity,
      pomodoros, minutes, at}), so automations can react to it;
    - `today`: pomodoros, focus minutes and tasks completed today.
    """
    with app.lock:
        view = app.timer_view()
        session = app.session
        remote = None if session else app.remote_session()
        focus = app.focus()
        today = app.today_stats()
        if session and session.get("call"):
            state = "call"
        elif not view:
            state = "idle"
        elif view["paused"]:
            state = "paused"
        else:
            state = "focus" if view["phase"] == "work" else "break"
        running = None
        if view:
            call = (session or {}).get("call")
            activity = (session or {}).get("activity")
            task = session["task"] if session else (remote or {}).get("task") or {}
            ends = None
            if not call and not view["paused"]:
                ends = now_ms() + view["left_ms"]
            running = {
                "kind": "call" if call else "pomodoro",
                "source": "here" if session else "bar",
                "task": task.get("content") or "",
                "task_id": task.get("id") or "",
                "project": task.get("project") or "",
                "activity": dict(activity) if activity else None,
                "phase": view["phase"],
                "paused": view["paused"],
                "ends_at": iso(ends),
                "ends_ms": ends,
                "phase_minutes": round(view["total_ms"] / 60000),
                "round": view["round"],
                "rounds": view["rounds"],
                "done": (session or {}).get("done", 0),
                "started_at": iso(call["started_ms"]) if call else (session or remote or {}).get("started_at") or None,
                "apps": [str(a) for a in call.get("apps") or []] if call else [],
            }
    return {
        "v": HA_PROTOCOL,
        "app": APP_NAME,
        "version": app_version(),
        "device": DEVICE_NAME,
        "state": state,
        "focus": bool(focus["active"]),
        "session": running,
        "ended": ended,
        "today": today,
    }


def same(a: dict | None, b: dict | None) -> bool:
    """Two payloads that say the same thing: the phase's end may move by up to END_SLACK_MS."""
    if a is None or b is None:
        return a is b

    def strip(p):
        s = p.get("session")
        return {**p, "session": s and {k: v for k, v in s.items() if k not in ("ends_at", "ends_ms")}}

    if strip(a) != strip(b):
        return False
    ends = [(p.get("session") or {}).get("ends_ms") for p in (a, b)]
    if None in ends:
        return ends[0] == ends[1]
    return abs(ends[0] - ends[1]) < END_SLACK_MS


def post_json(url: str, payload: dict, timeout: float = HA_TIMEOUT_S) -> dict:
    """
    POST `payload` to the webhook; its answer. busybar-ha answers
    {"ok": true, ...}; Home Assistant answers an unknown webhook with an
    empty 200, which is an error here (a wrong or removed URL).
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = response.read(65536)
    except urllib.error.HTTPError as err:
        raise app_error("errors.ha.refused", status=err.code) from err
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise app_error("errors.ha.unreachable", error=reason) from err
    try:
        data = json.loads(answer or b"null")
    except ValueError:
        data = None
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise app_error("errors.ha.unknown_webhook")
    return data


class HomeAssistantLink:
    """
    Sends status_payload to the webhook in `ha_webhook_url` while
    use_home_assistant is on: when what it says changes (the engine pokes it
    every poll; a phase's end moving by a few seconds isn't a change), and
    every HA_HEARTBEAT_S anyway. A send that fails is tried again after
    HA_RETRY_S. `last_ok_ms` and `error` are for Settings.
    """

    def __init__(self, app: App, send=post_json):
        self.app = app
        self.send = send
        self.wake = threading.Event()
        self.lock = threading.Lock()  # guards everything below
        self.sent: dict | None = None  # the last payload Home Assistant took
        self.sent_ms = 0
        self.failed_ms = 0  # when the last send failed (0: it didn't)
        self.last_ok_ms = 0
        self.error = ""
        self.ended: dict | None = None  # the last session that ended, in every payload from then on
        self._url = ""  # the URL `sent` went to: a new one gets everything again

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="home-assistant").start()

    def poke(self) -> None:
        self.wake.set()

    def on(self) -> bool:
        return bool(self.app.cfg.get("use_home_assistant")) and bool(self.app.cfg.get("ha_webhook_url"))

    def _loop(self) -> None:
        while not self.app.quitting:
            try:
                self.step()
            except Exception:
                log.exception("home assistant")
            self.wake.wait(HA_RETRY_S if self.on() else None)
            self.wake.clear()

    def note_ended(self, session: dict, reason: str, pomodoros: int, minutes: int) -> None:
        """A session ended (App.finish): the payloads say so from now on."""
        activity = session.get("activity")
        with self.lock:
            self.ended = {
                "reason": reason,
                "kind": "call" if session.get("call") else "pomodoro",
                "task": session["task"].get("content") or "",
                "task_id": session["task"].get("id") or "",
                "activity": dict(activity) if activity else None,
                "pomodoros": pomodoros,
                "minutes": minutes,
                "at": iso(now_ms()),
            }
        self.poke()

    def step(self, force: bool = False) -> bool:
        """Send the payload if it changed, the heartbeat is due or `force`; whether it went."""
        if not self.on():
            with self.lock:
                self.sent, self.failed_ms, self.error = None, 0, ""
            return False
        url = self.app.cfg.get("ha_webhook_url")
        with self.lock:
            ended = self.ended
        payload = status_payload(self.app, ended)
        now = now_ms()
        with self.lock:
            if not force:
                if self.failed_ms and now - self.failed_ms < HA_RETRY_S * 1000:
                    return False
                fresh = url == self._url and now - self.sent_ms < HA_HEARTBEAT_S * 1000
                if fresh and same(payload, self.sent):
                    return False
        try:
            self.send(url, payload)
        except Exception as err:
            with self.lock:
                first = not self.failed_ms
                self.failed_ms, self.error = now_ms(), str(err)
            if first:
                log.warning("home assistant: %s", getattr(err, "english", err))
            return False
        with self.lock:
            if self.failed_ms:
                log.info("home assistant: reached again")
            self.sent, self.sent_ms, self._url = payload, now, url
            self.failed_ms, self.error, self.last_ok_ms = 0, "", now_ms()
        return True

    def test(self, url: str) -> dict:
        """Settings' Send now: the payload to `url` at once (AppError when it fails)."""
        url = clean_ha_url(url)
        if not url:
            raise app_error("errors.ha.no_url")
        with self.lock:
            ended = self.ended
        payload = status_payload(self.app, ended)
        answer = self.send(url, payload)
        with self.lock:
            if url == self.app.cfg.get("ha_webhook_url"):
                self.sent, self.sent_ms, self._url = payload, now_ms(), url
                self.failed_ms, self.error, self.last_ok_ms = 0, "", now_ms()
        return {"state": payload["state"], "name": str((answer or {}).get("name") or "")}

    def close(self) -> None:
        """On quit: tell Home Assistant buZyist is gone (best effort, a short wait)."""
        if not self.on():
            return
        payload = {"v": HA_PROTOCOL, "app": APP_NAME, "device": DEVICE_NAME, "closed": True}
        try:
            self.send(self.app.cfg.get("ha_webhook_url"), payload, timeout=2)
        except Exception as err:
            log.info("home assistant: couldn't say goodbye: %s", getattr(err, "english", err))

    def view(self) -> dict:
        """For Settings: whether it is on, when Home Assistant last took a message, the last error."""
        with self.lock:
            return {"on": self.on(), "last_ok_ms": self.last_ok_ms, "error": self.error if self.failed_ms else ""}
