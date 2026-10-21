"""
App's sessions: starting one on a task (or none), pause/resume/skip/stop,
following its progress from the bar's snapshot (follow_session) and, when
it ends, logging it to Todoist and the history (finish).
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime

from busylib.features import timer_state

from buzyist import paths
from buzyist.activities import call_name, session_name
from buzyist.bar import BarUnreachable
from buzyist.constants import APP, HISTORY_KEEP, AppError, now_ms
from buzyist.focus_stats import session_minutes
from buzyist.i18n import app_error, english, t
from buzyist.plan import plan_session
from buzyist.storage import write_json

log = logging.getLogger(APP)

# How often a running session's focus time so far is saved to session.json.
FOCUS_SAVE_MS = 60_000


def track_focus(session: dict, state) -> bool:
    """
    Bring the session's focus time up to `state` (busylib's timer_state of an
    interval timer): `focus_ms` holds the work phases behind us (`counted` of
    them), `partial_ms` the time spent so far in the one running (paused or
    not). A work phase that ran out counts in full; one skipped counts what
    it ran (control). Work phases the bar runs past the session's `rounds`
    never count. True while a work phase of the session runs.
    """
    work = int(session.get("work_ms") or 0)
    rounds = int(session.get("rounds") or 0) or None
    session.setdefault("focus_ms", 0)
    session.setdefault("counted", 0)
    if state.is_finished:
        behind, running = rounds or int(session.get("done") or 0), False
    else:
        interval = state.interval or 0
        behind, current = (interval + 1) // 2, interval // 2
        # Even intervals are work; one already counted (skipped) is a stale reading.
        running = interval % 2 == 0 and current >= session["counted"] and (not rounds or current < rounds)
    if rounds:
        behind = min(behind, rounds)
    if behind > session["counted"]:
        session["focus_ms"] += (behind - session["counted"]) * work
        session["counted"] = behind
    left = int(state.time_left_ms or 0)
    session["partial_ms"] = max(0, min(work, work - left)) if running else 0
    return running


class SessionMixin:
    """
    A session's life. Progress is read from the bar's state, never from a
    timer of our own: even interval indices are work, odd ones rest, and
    snapshots older than the session's stamp are ignored.
    """

    def follow_session(self) -> None:
        notify = None
        stop_bar = False
        with self.lock:
            session, snap = self.session, self.snap
            if not session or snap is None or not self.bar_ok:
                return
            if snap.snapshot_timestamp_ms < session["stamp"]:
                return  # a reading from before our own start
            state = timer_state(snap)
            inner = snap.snapshot
            # The bar may run more work phases than the session asked for
            # (BAR_CYCLES_MIN); what it ran past ours never counts.
            rounds = session.get("rounds")
            if session.get("call"):
                # Its end comes from the devices (end_call); the bar only
                # ends it when the call's timer was stopped or replaced there.
                reason = None if state.mode == "infinite" else "stopped"
                if reason:
                    session["call"]["seen_ms"] = now_ms()
            elif state.mode != "interval":
                reason = "stopped"  # its focus time is as last seen (or noted by control)
            elif state.is_finished:
                reason = "finished"
                count = inner.interval_settings.interval_work_cycles_count
                session["done"] = min(count, int(rounds)) if rounds else count
                track_focus(session, state)
            else:
                reason = None
                # Even intervals are work, odd ones rest: the work phases
                # fully behind us are the even indices below the current one.
                interval = state.interval or 0
                done = max(session["done"], (interval + 1) // 2)
                session["done"] = min(done, int(rounds)) if rounds else done
                track_focus(session, state)
                if rounds and interval >= rounds * 2 - 1:
                    # Fewer rounds than the bar runs: our session ends here.
                    reason, stop_bar = "finished", True
                    session["done"] = rounds
                elif interval == session.get("interval", 0) and now_ms() - session.get("saved_ms", 0) >= FOCUS_SAVE_MS:
                    # The focus time so far survives a restart (or a crash).
                    session["saved_ms"] = now_ms()
                    write_json(paths.SESSION_PATH, session)
                elif interval != session.get("interval", 0):
                    session["interval"] = interval
                    write_json(paths.SESSION_PATH, session)
                    rounds = inner.interval_settings.interval_work_cycles_count
                    if state.phase == "rest":
                        mins = inner.interval_settings.interval_rest_ms // 60000
                        notify = (
                            t("notify.break.title"),
                            t("notify.break.text", round=interval // 2 + 1, rounds=rounds, minutes=mins),
                        )
                    else:
                        notify = (
                            t("notify.back.title"),
                            t(
                                "notify.back.text",
                                round=interval // 2 + 1,
                                rounds=rounds,
                                task=session["task"]["content"],
                            ),
                        )
            if reason:
                self.session = None
                paths.SESSION_PATH.unlink(missing_ok=True)
                silent = self.recovering
                threading.Thread(target=self.finish, args=(session, reason, silent), daemon=True).start()
            self.recovering = False
        if stop_bar:
            try:
                self.bar.stop()
                self.wake.set()  # read the stopped bar back now
            except AppError as err:
                log.warning("couldn't stop the bar after the last round: %s", english(err))
        if notify:
            self.notify(*notify)

    def finish(self, session: dict, reason: str, silent: bool = False) -> None:
        """Log the session to Todoist and history, then offer to complete it."""
        task, done = session["task"], session["done"]
        minutes = session_minutes(session)
        call = session.get("call")
        problems = []
        linked = bool(task["id"]) and self.todoist_on  # a free session has no task
        if linked and (done or minutes) and self.cfg["log_comments"]:
            try:
                # Written once, in the language of the moment; never rewritten.
                when = datetime.now().strftime(t("todoist.when"))
                key = "todoist.comment_local" if session.get("local") else "todoist.comment_bar"
                self.todoist.comment(task["id"], t(key, n=done, minutes=minutes, when=when))
            except AppError as err:
                problems.append(t("errors.session.comment_failed", error=err))
        if linked and session.get("label_added") and self.cfg["remove_label_on_end"]:
            try:
                self.todoist.set_label(task["id"], session["label"], False)
            except AppError as err:
                problems.append(t("errors.session.unlabel_failed", error=err))
        with self.lock:
            self.history.append(
                {
                    "date": date.today().isoformat(),
                    "task_id": task["id"],
                    "content": task["content"],
                    "pomodoros": done,
                    "minutes": minutes,
                    "reason": reason,
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                    **({"activity": session["activity"]} if session.get("activity") else {}),
                    **({"kind": "call"} if call else {}),
                }
            )
            self.history = self.history[-HISTORY_KEEP:]
            write_json(paths.HISTORY_PATH, self.history)
            if reason == "replaced" or silent:
                return
        if call:  # no summary to read: it started and ended on its own
            self.update_tray()
            self.hide_mini()
            self.notify(t("notify.call_over.title"), t("notify.call_over.text", n=minutes, task=task["content"]))
            return
        with self.lock:
            self.ended = {
                "task": task,
                "done": done,
                "minutes": minutes,
                "reason": reason,
                "problems": problems,
                "activity": session.get("activity"),  # so "Again" starts the same one
            }
        self.update_tray()
        if reason == "finished":
            self.notify(t("notify.finished.title"), t("notify.finished.text", n=done, task=task["content"]))
            if self.cfg["open_on_finish"]:
                self.show_main()
        self.hide_mini()
        if reason == "stopped":
            self.show_main()  # after hiding the mini timer, which may hold the focus

    def start_task(
        self, task_id: str | None = None, name: str = "", activity_id: str = "", fixed: bool = False
    ) -> dict:
        """
        Start a session on a task, or (task_id None) one with no task: on one of
        the `activities` (by id; one since removed is ignored) and/or named.
        A task with a duration sets its own tomatoes (plan_session) unless
        `fixed` asks for the usual settings.
        """
        activity = None
        if task_id is None:
            found = next((a for a in self.cfg["activities"] if activity_id and a["id"] == activity_id), None)
            if found:
                activity = {"id": found["id"], "name": found["name"], "detail": " ".join((name or "").split())}
            task = {
                "id": "",
                "content": session_name(found, name),
                "project": "",
                "color": "",
                "priority": 1,
                "url": "",
            }
        else:
            with self.lock:
                task = self.tasks.get(task_id)
            if task is None:
                raise app_error("errors.session.task_gone")
        cfg = self.cfg.view()
        planned = None if fixed else plan_session(task.get("minutes"), cfg)
        work_min, cycles = planned or (int(cfg["work_minutes"]), int(cfg["cycles"]))
        work = work_min * 60000
        rest = int(cfg["rest_minutes"]) * 60000
        with self.bar.lock:  # nothing reads the bar between the start and our bookkeeping
            stamp = self._on_bar(lambda: self.bar.start(work, rest, cycles, bool(self.cfg["autostart"])))
            with self.lock:
                old, self.ended = self.session, None
                self.session = {
                    "task": {
                        **{k: task[k] for k in ("id", "content", "project", "color", "priority", "url")},
                        # a subtask's parent, offered for completion after its last subtask
                        **{k: task[k] for k in ("parent_id", "parent") if task.get(k)},
                    },
                    "stamp": stamp,
                    "started_at": datetime.now().strftime("%H:%M"),
                    "work_ms": work,
                    "done": 0,
                    "interval": 0,
                    "label": self.cfg["focus_label"].strip(),
                    "label_added": False,
                    "local": self.bar.standalone,
                    "rounds": cycles,  # the bar may run more (BAR_CYCLES_MIN)
                    "activity": activity,  # {id, name, detail} for the Stats tab, or None
                }
                write_json(paths.SESSION_PATH, self.session)
                self.recovering = False
        if old:
            threading.Thread(target=self.finish, args=(old, "replaced"), daemon=True).start()
        if task["id"] and self.todoist_on:
            threading.Thread(target=self._label_task, args=(self.session,), daemon=True).start()
        self.wake.set()
        # Read the fresh interval straight back so focus (and its blockers)
        # act at once, not on the next poll.
        try:
            snap = self.bar.snapshot()
            with self.lock:
                self.snap, self.bar_ok, self.bar_fails = snap, True, 0
        except AppError:
            pass
        self.notify_focus()
        # Out of the way for the focus block; the mini timer takes over.
        self.hide_main()
        self.show_mini()
        return {"via": self.bar.via, "work_minutes": work_min, "rounds": cycles, "planned": bool(planned)}

    # A call (calls.py) is a session with no timer: an open-ended (INFINITE)
    # timer on the bar, `call` = {apps: names, keys, started_ms, seen_ms} in
    # the session (keys as calls.call_app gives them).
    # It counts from started_ms (the devices first seen in use) to seen_ms
    # (last seen in use), on the call_activity, and blocks what it blocks.

    def start_call(self, apps: list[dict], since_ms: int) -> bool:
        """
        Start a call session on `apps` (calls.call_app's), counted from `since_ms`.
        False when something else runs: a session, or a timer started on the
        bar or from another PC, which a call doesn't take over.
        """
        cfg = self.cfg.view()
        found = next((a for a in cfg["activities"] if a["id"] == cfg["call_activity"]), None)
        detail = ", ".join(a["name"] for a in apps)
        activity = {"id": found["id"], "name": found["name"], "detail": detail} if found else None
        content = session_name(found, detail) if found else call_name(detail)
        with self.bar.lock:
            with self.lock:
                if self.session or self.timer_view():
                    return False
            stamp = self._on_bar(self.bar.start_open)
            now = now_ms()
            with self.lock:
                self.session = {
                    "task": {"id": "", "content": content, "project": "", "color": "", "priority": 1, "url": ""},
                    "stamp": stamp,
                    "started_at": datetime.fromtimestamp(since_ms / 1000).strftime("%H:%M"),
                    "work_ms": 0,
                    "done": 0,
                    "interval": 0,
                    "label": "",
                    "label_added": False,
                    "local": self.bar.standalone,
                    "rounds": 0,
                    "activity": activity,
                    "call": {
                        "apps": [a["name"] for a in apps],
                        "keys": [a["key"] for a in apps],
                        "started_ms": min(since_ms, now),
                        "seen_ms": now,
                        "saved_ms": now,
                    },
                }
                write_json(paths.SESSION_PATH, self.session)
                self.recovering = False
        self.wake.set()
        try:
            snap = self.bar.snapshot()
            with self.lock:
                self.snap, self.bar_ok, self.bar_fails = snap, True, 0
        except AppError:
            pass
        self.notify_focus()
        # Silent: it comes up while the call is starting, where Windows' chime would be heard.
        self.notify(t("notify.call.title"), t("notify.call.text", task=content), quiet=True)
        self.show_mini()
        return True

    def note_call(self, apps: list[dict], when_ms: int) -> None:
        """The running call's devices are still in use: it counts until `when_ms` (saved about once a minute)."""
        with self.lock:
            call = self.session and self.session.get("call")
            if not call:
                return
            call["seen_ms"] = when_ms
            for app in apps:  # one more app may join (camera in another)
                if app["key"] not in call["keys"]:
                    call["keys"].append(app["key"])
                    call["apps"].append(app["name"])
            if when_ms - call.get("saved_ms", 0) >= 60_000:
                call["saved_ms"] = when_ms
                write_json(paths.SESSION_PATH, self.session)

    def end_call(self) -> None:
        """The call is over: log it (up to when its devices were last in use) and stop its timer."""
        with self.bar.lock:
            with self.lock:
                session = self.session
                if not (session and session.get("call")):
                    return
                self.session = None
                paths.SESSION_PATH.unlink(missing_ok=True)
                silent = self.recovering
                self.recovering = False
            try:
                self._on_bar(self.bar.stop)
            except AppError as err:
                log.warning("couldn't stop the bar after the call: %s", english(err))
        threading.Thread(target=self.finish, args=(session, "finished", silent), daemon=True).start()
        self.wake.set()
        try:
            snap = self.bar.snapshot()
            with self.lock:
                self.snap, self.bar_ok, self.bar_fails = snap, True, 0
        except AppError:
            pass
        self.notify_focus()

    def complete_task(self, task_id: str, offer_parent: bool = False, content: str = "") -> dict:
        """
        Close the task in Todoist and count it in today's history (`content` names
        a task the list doesn't hold). With `offer_parent`, a subtask that was its
        parent's last open one returns that parent ({"parent": {id, content}}) so
        the UI can offer to complete it too.
        """
        self.todoist.close(task_id)
        with self.lock:
            task = self.tasks.pop(task_id, None) or (self.ended and self.ended["task"]) or {"content": content}
            if self.ended and self.ended["task"]["id"] == task_id:
                self.ended = None
            self.history.append(
                {
                    "date": date.today().isoformat(),
                    "task_id": task_id,
                    "content": task.get("content", ""),
                    "completed": True,
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            self.history = self.history[-HISTORY_KEEP:]
            write_json(paths.HISTORY_PATH, self.history)
        parent_id = task.get("parent_id")
        if not (offer_parent and parent_id):
            return {}
        try:
            if self.todoist.open_children(parent_id):
                return {}
        except AppError as err:
            log.warning("couldn't check the parent's other subtasks: %s", english(err))
            return {}
        return {"parent": {"id": parent_id, "content": task.get("parent", "")}}

    def _label_task(self, session: dict) -> None:
        label = session["label"]
        if not label:
            return
        try:
            self.todoist.set_label(session["task"]["id"], label, True)
            with self.lock:
                session["label_added"] = True
                if self.session is session:
                    write_json(paths.SESSION_PATH, session)
        except AppError as err:
            self.notify(t("notify.label_failed.title"), t("notify.label_failed.text", label=label, error=err))

    def _on_bar(self, call):
        """call() on the bar; if the bar is out of reach, once more on this PC (go_standalone)."""
        try:
            return call()
        except BarUnreachable:
            if not self.go_standalone():
                raise
            return call()

    def _note_focus(self) -> bool:
        """
        Bring the running session's focus time up to now (track_focus), from
        the last reading of the bar. True while one of its work phases runs.
        """
        with self.lock:
            session, snap = self.session, self.snap
            if not session or session.get("call") or snap is None:
                return False
            if snap.snapshot_timestamp_ms < session["stamp"]:
                return False
            state = timer_state(snap)
            return state.mode == "interval" and track_focus(session, state)

    def _skipped_work(self) -> None:
        """The work phase that ran has been skipped: it counts what it ran, not its full length."""
        with self.lock:
            session = self.session
            if session:
                session["focus_ms"] = session.get("focus_ms", 0) + session.get("partial_ms", 0)
                session["counted"] = session.get("counted", 0) + 1
                session["partial_ms"] = 0

    def control(self, action: str) -> None:
        with self.lock:
            on_call = bool(self.session and self.session.get("call"))
        if on_call and action != "stop":
            raise app_error("errors.session.call_control")
        try:
            if action == "stop":
                self._note_focus()  # the time up to the stop, not to the last poll
                self._on_bar(self.bar.stop)
            elif action == "pause":
                self._on_bar(lambda: self.bar.set_paused(True))
            elif action == "resume":
                self._on_bar(lambda: self.bar.set_paused(False))
                self.show_mini()
            elif action == "skip":
                # Held so no fresh reading of the bar comes between the skip
                # and its bookkeeping (it would count the phase in full).
                with self.bar.lock:
                    working = self._note_focus()
                    self._on_bar(self.bar.next_phase)
                    if working:
                        self._skipped_work()
        except BarUnreachable:
            # Nothing to run the timer from here (the app started with the
            # bar already gone): a stop still ends the session.
            with self.lock:
                session = self.session if action == "stop" else None
                if session:
                    self.session = None
                    paths.SESSION_PATH.unlink(missing_ok=True)
            if not session:
                raise
            log.warning("stopped the session without the bar, which is out of reach")
            threading.Thread(target=self.finish, args=(session, "stopped"), daemon=True).start()
        self.wake.set()
        # Read the result straight back so the window updates at once, and
        # follow it now: a stop ends the session (and opens its summary)
        # without waiting for the engine's next look at the bar.
        try:
            snap = self.bar.snapshot()
            with self.lock:
                self.snap, self.bar_ok, self.bar_fails = snap, True, 0
            self.follow_session()
        except AppError:
            pass
        # A pause/resume/stop from the app lifts or applies the block now.
        self.notify_focus()
