"""
App limits: the site limits' time budgets and blocked hours, for Windows
programs. buZyist measures the time itself (the program in front, while the
PC isn't idle) and, once a limit is reached, either closes the app or only
beeps to say how long it has been in front (AppLimiter).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from buzyist import paths
from buzyist.blocker import PROTECTED_EXES, app_key, exe_label
from buzyist.constants import (
    APP,
    APP_IDLE_S,
    APP_LIMIT_REMIND,
    APP_LIMIT_REMIND_MAX,
    APP_LIMIT_REMIND_MIN,
    MAX_APP_LIMITS,
    MAX_LIMIT_APPS,
    AppError,
    now_ms,
)
from buzyist.i18n import app_error, t
from buzyist.site_rules import budget_label, clean_limit_times, site_limit_status
from buzyist.win32.input import idle_seconds
from buzyist.win32.processes import list_processes, process_session, terminate_process
from buzyist.win32.windows import close_windows, foreground_app

if TYPE_CHECKING:
    from buzyist.app import App
    from buzyist.usage import SiteUsage

log = logging.getLogger(APP)


# A rule is {"id", "apps", "action", "remind", "enabled", "budgets", "windows"}: the
# budgets and windows of a site limit (see clean_limit_times), for one or more
# programs that share the time. "close" closes the apps while the limit is
# reached, as the app blocker does; "beep" leaves them open and beeps, again
# every "remind" minutes, while one of them is in front.
APP_LIMIT_ACTIONS = ("close", "beep")


def clean_app_name(raw) -> str:
    """'whatsapp' -> 'whatsapp.exe': a program name, as the app blocker takes them; AppError if it isn't one."""
    name = str(raw).strip().strip('"').strip()
    if "/" in name or "\\" in name:
        raise app_error("errors.apps.just_program")
    if not name or len(name) > 100 or re.search(r'[\x00-\x1f<>:"|?*]', name):
        raise app_error("errors.apps.not_program", name=str(raw).strip())
    return name if name.lower().endswith(".exe") else name + ".exe"


def app_names(apps: list[str]) -> list[str]:
    """['WhatsApp.Root.exe', 'WhatsApp.exe'] -> ['WhatsApp']: what messages and Settings call a rule's apps."""
    return list(dict.fromkeys(exe_label(a) for a in apps))


def clean_app_limit(raw) -> dict:
    """Check and tidy one app-limit rule from Settings; AppError if it's wrong."""
    if not isinstance(raw, dict):
        raise app_error("errors.app_limits.missing")
    names = raw.get("apps")
    if not isinstance(names, list):
        raise app_error("errors.app_limits.apps_missing")
    apps: list[str] = []
    for item in names:
        if str(item).strip():
            name = clean_app_name(item)
            if app_key(name) not in {app_key(a) for a in apps}:
                apps.append(name)
    if not apps:
        raise app_error("errors.app_limits.no_apps")
    if len(apps) > MAX_LIMIT_APPS:
        raise app_error("errors.app_limits.too_many_apps", max=MAX_LIMIT_APPS)
    action = raw.get("action", "close")
    if action not in APP_LIMIT_ACTIONS:
        raise app_error("errors.app_limits.action")
    if action == "close":
        for name in apps:
            if app_key(name) in PROTECTED_EXES:
                raise app_error("errors.app_limits.protected", exe=name)
    name = ", ".join(app_names(apps))
    try:
        remind = int(raw.get("remind", APP_LIMIT_REMIND))
    except (TypeError, ValueError):
        remind = 0
    if not APP_LIMIT_REMIND_MIN <= remind <= APP_LIMIT_REMIND_MAX:
        raise app_error("errors.app_limits.remind", name=name, min=APP_LIMIT_REMIND_MIN, max=APP_LIMIT_REMIND_MAX)
    rid, budgets, windows = clean_limit_times(raw, name)
    return {
        "id": rid,
        "apps": apps,
        "action": action,
        "remind": remind,
        "enabled": bool(raw.get("enabled", True)),
        "budgets": budgets,
        "windows": windows,
    }


def clean_app_limits(values) -> list[dict]:
    if not isinstance(values, list):
        raise app_error("errors.app_limits.list")
    if len(values) > MAX_APP_LIMITS:
        raise app_error("errors.app_limits.too_many", max=MAX_APP_LIMITS)
    out = [clean_app_limit(v) for v in values]
    seen: set[str] = set()
    for r in out:  # a copied rule keeps its id: give the later ones new ones
        if r["id"] in seen:
            r["id"] = uuid.uuid4().hex[:8]
        seen.add(r["id"])
    return out


def _sanitize_app_limits(values) -> list[dict]:
    """Tidy saved rules without raising, for load time: bad ones are dropped."""
    out = []
    for item in values if isinstance(values, list) else []:
        try:
            out.append(clean_app_limit(item))
        except AppError:
            continue
    return out[:MAX_APP_LIMITS]


def app_limit_status(rule: dict, usage: SiteUsage, now: datetime) -> dict:
    """Where one app rule stands (site_limit_status, with the apps' names as `pattern`)."""
    return {
        **site_limit_status({**rule, "patterns": app_names(rule["apps"])}, usage, now),
        "apps": rule["apps"],
        "action": rule["action"],
    }


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # notify.weekdays.<day>, Monday first


def _clock(ms: int) -> str:
    """'17:00', or 'Mon 00:00' when it isn't today (the day in the app language)."""
    when = datetime.fromtimestamp(ms / 1000)
    clock = when.strftime("%H:%M")
    if when.date() == datetime.now().date():
        return clock
    return t("notify.day_time", day=t(f"notify.weekdays.{WEEKDAYS[when.weekday()]}"), time=clock)


def _and(names: list[str]) -> str:
    """['WhatsApp', 'Slack', 'Discord'] -> 'WhatsApp, Slack and Discord'."""
    return names[0] if len(names) == 1 else t("notify.and", list=", ".join(names[:-1]), last=names[-1])


def _budget_words(rule: dict, status: dict) -> tuple[int, str]:
    """
    The minutes allowed by the budget a status is about, and its period in
    words: (30, 'today'), (5, 'this hour'), (1, 'in this 5-minute stretch').
    """
    for b in rule["budgets"]:
        if budget_label(b) == status["limit_label"]:
            if b["every"] == 1:
                return b["minutes"], t(f"notify.limit.this_{b['unit']}")
            return b["minutes"], t(f"notify.limit.stretch_{b['unit']}", n=b["every"])
    return round((status["budget_s"] or 0) / 60), t("notify.limit.so_far")


def nudge_text(rule: dict, status: dict) -> tuple[str, str]:
    """The (title, message) of a "beep" limit's reminder."""
    name = _and(app_names(rule["apps"]))
    title = t("notify.limit.nudge_title", name=name)
    if status["reason"] != "budget":
        return title, t("notify.limit.nudge_hours", name=name, until=_clock(status["until_ms"]))
    allowed, words = _budget_words(rule, status)
    used = int((status["used_s"] or 0) // 60)
    return title, t("notify.limit.nudge_budget", used=used, period=words, allowed=allowed)


def closed_text(rule: dict, status: dict) -> tuple[str, str]:
    """The (title, message) of the toast for a "close" limit closing its apps."""
    name, until = _and(app_names(rule["apps"])), _clock(status["until_ms"])
    if status["reason"] != "budget":
        return t("notify.limit.closed_hours_title", name=name, until=until), t("notify.limit.closed_hours_text")
    allowed, words = _budget_words(rule, status)
    return (
        t("notify.limit.closed_budget_title", name=name),
        t("notify.limit.closed_budget_text", allowed=allowed, period=words, until=until),
    )


class AppLimiter:
    """
    Counts the time the limited apps spend in front and acts on their limits;
    while app_time_on is set, the time of every app in front (AppTime,
    app_time.json, for the Stats tab); and while pc_time_on is set, the time
    the PC is in use at all (pc_time.json, one row, no app names). Both are
    split by whether a work phase runs.

    The thread sleeps on an Event while app limits are off or there are none
    and neither time is counted; save_settings() pokes it. Otherwise, every TICK
    seconds, unless nobody has touched the keyboard or mouse for APP_IDLE_S,
    the time since the last tick goes to the PC, and the program whose window
    is in front gets it (time goes to every enabled rule that lists it, into
    app_usage.json). Then each reached limit acts:

    - "close": its apps are asked to close (WM_CLOSE) and ended CLOSE_GRACE
      seconds later, or at once without a window, as long as the limit holds,
      whether or not they're in front. One toast per block. Programs in
      PROTECTED_EXES and buZyist's own process are never touched.
    - "beep": nothing is closed. While one of its apps is in front, buZyist
      beeps and says how long it has been in front, again every
      rule's "remind" minutes.
    """

    TICK = 1.0
    CLOSE_GRACE = 3.0  # seconds a "close" app gets to close itself (and save) before it's ended

    def __init__(self, app: App):
        self.app = app
        self.wake = threading.Event()
        self._last: float | None = None  # monotonic time of the last tick
        self._beeped: dict[str, float] = {}  # rule id -> monotonic time of its last beep
        self._told: dict[str, int] = {}  # rule id -> until_ms of the block its "closed" toast was for
        self._closing: dict[int, float] = {}  # pid -> monotonic deadline: asked to close, ended after it
        self._denied: set[int] = set()  # pids we couldn't end; logged once, not retried
        # Never this process; from source, not the python.exe/launcher that started it either.
        self._own_pids = {os.getpid()} | (set() if paths.FROZEN else {os.getppid()})
        self._session = process_session(os.getpid())
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="app-limits")
        self._thread.start()

    def poke(self) -> None:
        self.wake.set()

    def rules(self) -> list[dict]:
        """The rules in force: the enabled ones, while app limits are on."""
        cfg = self.app.cfg.view()
        if not cfg.get("app_limits_on"):
            return []
        return [r for r in cfg.get("app_limits") or [] if r.get("enabled", True)]

    def status(self) -> list[dict]:
        """Where every app rule stands right now, for the Limits tab."""
        now = datetime.now()
        return [app_limit_status(r, self.app.app_usage, now) for r in self.app.cfg.get("app_limits") or []]

    def tracking(self) -> bool:
        """Whether every app's time is counted for the Stats tab."""
        return bool(self.app.cfg.get("app_time_on"))

    def counting_pc(self) -> bool:
        """Whether the time on the PC at all is counted for the Focus section."""
        return bool(self.app.cfg.get("pc_time_on"))

    def _loop(self) -> None:
        while not self.app.quitting:
            rules = self.rules()
            if rules or self.tracking() or self.counting_pc():
                try:
                    self.step(rules)
                except Exception:
                    log.exception("app limits")
                self.wake.wait(self.TICK)
            else:
                self._last = None
                self._closing.clear()
                self._denied.clear()
                self.app.app_usage.flush(force=True)
                self.app.app_time.flush(force=True)
                self.app.pc_time.flush(force=True)
                self.wake.wait()  # idle until the settings change
            self.wake.clear()

    def step(self, rules: list[dict]) -> None:
        mono = time.monotonic()
        # A long gap (sleep, a stalled thread) isn't time spent in the app.
        elapsed = 0.0 if self._last is None else min(mono - self._last, 3 * self.TICK)
        self._last = mono
        in_use = idle_seconds() < APP_IDLE_S
        tracking = self.tracking()
        # Which program is in front matters only to the limits and the time per app.
        pid, exe = foreground_app() if in_use and (rules or tracking) else (0, "")
        front = app_key(exe) if exe and pid not in self._own_pids else ""
        usage = self.app.app_usage
        focused = {r["id"] for r in rules if front and front in {app_key(a) for a in r["apps"]}}
        if elapsed > 0:
            minute = now_ms() // 60000
            for r in rules:
                if r["id"] in focused:
                    usage.add(r["id"], minute, elapsed)
            pc = in_use and self.counting_pc()
            if (front and tracking) or pc:
                focus_s = elapsed if self.app.focus()["active"] else 0.0
                if front and tracking:
                    self.app.app_time.add(exe, elapsed, focus_s)
                if pc:  # buZyist itself in front counts too: the PC is in use
                    self.app.pc_time.add("pc", elapsed, focus_s)
        usage.flush()
        self.app.app_time.flush()
        self.app.pc_time.flush()
        now = datetime.now()
        to_close: dict[str, tuple[dict, dict]] = {}  # app_key -> (rule, status) of the rule that closes it
        for r in rules:
            if r["action"] == "beep" and r["id"] not in focused:
                continue  # beeps only for the app in front; its last beep is kept for when it's back
            status = app_limit_status(r, usage, now)
            if not status["blocked"]:
                self._beeped.pop(r["id"], None)
                self._told.pop(r["id"], None)
            elif r["action"] == "beep":
                self._beep(r, status, mono)
            else:
                for name in r["apps"]:
                    if app_key(name) not in PROTECTED_EXES:  # also checked on save and load
                        to_close.setdefault(app_key(name), (r, status))
        self._close(to_close, mono)

    def _beep(self, rule: dict, status: dict, mono: float) -> None:
        last = self._beeped.get(status["id"])
        if last is not None and mono - last < rule.get("remind", APP_LIMIT_REMIND) * 60:
            return
        self._beeped[status["id"]] = mono
        title, text = nudge_text(rule, status)
        log.info("app limit reached, beeping: %s %s", title, text)
        self.app.alert(title, text)

    def _close(self, wanted: dict[str, tuple[dict, dict]], mono: float) -> None:
        if not wanted:
            self._closing.clear()
            return
        alive: set[int] = set()
        found: list[tuple[int, str, tuple[dict, dict]]] = []
        for pid, image in list_processes():
            alive.add(pid)
            hit = wanted.get(app_key(image))
            if hit is None or pid in self._own_pids or pid in self._denied:
                continue
            if self._session is not None and process_session(pid) != self._session:
                continue  # another user's process: not ours to touch
            found.append((pid, image, hit))
        self._denied &= alive  # forget pids that are gone, so a reused pid is tried
        listed = {pid for pid, *_ in found}
        self._closing = {pid: deadline for pid, deadline in self._closing.items() if pid in listed}
        to_ask: dict[str, list[tuple[int, str]]] = {}  # app_key -> processes to ask to close now
        for pid, image, hit in found:
            if pid in self._closing:
                if mono >= self._closing[pid]:  # asked, and still running after the grace time
                    del self._closing[pid]
                    if self._end(pid, image):
                        log.info("%s (pid %d) didn't close in %g s; ended it", image, pid, self.CLOSE_GRACE)
                continue
            to_ask.setdefault(app_key(image), []).append((pid, image))
            self._tell_closed(*hit)
        if not to_ask:
            return
        # Ask first, like the X button; an app with no window at all can't be asked, so it's ended now.
        asked = close_windows({pid for procs in to_ask.values() for pid, _ in procs})
        for procs in to_ask.values():
            if any(pid in asked for pid, _ in procs):
                for pid, image in procs:
                    self._closing[pid] = mono + self.CLOSE_GRACE
                    if pid in asked:
                        log.info("asked %s (pid %d) to close: its app limit is reached", image, pid)
            else:
                for pid, image in procs:
                    if self._end(pid, image):
                        log.info("closed %s (pid %d): its app limit is reached", image, pid)

    def _tell_closed(self, rule: dict, status: dict) -> None:
        """One toast per block of a rule, the first time it closes something."""
        if self._told.get(status["id"]) == status["until_ms"]:
            return
        self._told[status["id"]] = status["until_ms"]
        self.app.notify(*closed_text(rule, status))

    def _end(self, pid: int, image: str) -> bool:
        """TerminateProcess it; False (and never retried) if Windows says no."""
        error = terminate_process(pid)
        if error:
            self._denied.add(pid)
            log.warning("couldn't close %s (pid %d): Win32 error %d", image, pid, error)
            return False
        return True
