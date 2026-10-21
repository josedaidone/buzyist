"""
The Stats tab's focus numbers, worked out from history.json (plus the
running session): focus time, tomatoes, sessions and tasks per day, the
streak of days with focus, and the tasks that got the most focus.
"""

from __future__ import annotations

from datetime import date, timedelta

from buzyist.i18n import app_error
from buzyist.stats_common import (
    DEFAULT_SPAN,
    buckets,
    earliest_day,
    minutes_of,
    range_info,
    sum_rows,
    summary,
    top,
    window,
)

# How a session ended, as finish() logs it; the other entries are completed tasks.
SESSION_REASONS = ("finished", "stopped", "replaced")


def session_minutes(session: dict) -> int:
    """
    A session's focus minutes so far, to the nearest minute: the time its
    work phases ran, a stopped or skipped one included (session.track_focus),
    or for a call (kind "call" in the history) the time its devices were in
    use. A session saved before that was tracked counts its finished tomatoes.
    """
    call = session.get("call")
    if call:
        return max(0, call["seen_ms"] - call["started_ms"]) // 60000
    if "focus_ms" in session:
        return (int(session["focus_ms"]) + int(session.get("partial_ms") or 0) + 30000) // 60000
    return session.get("done", 0) * session["work_ms"] // 60000


def _counted(entry: dict) -> bool:
    """A session that counts: one that gave a minute of focus or more (a call too)."""
    return int(entry.get("minutes") or 0) > 0 or bool(entry.get("pomodoros"))


def _worked(entry: dict) -> bool:
    """A session that counts as work on its task: a counted one that isn't a call."""
    return _counted(entry) and entry.get("kind") != "call"


def _task_key(entry: dict) -> str:
    """A task's id, or for a session with no task its name."""
    return entry.get("task_id") or "name:" + (entry.get("content") or "")


def focus_stats(
    history: list[dict],
    session: dict | None,
    today: date,
    span: int = DEFAULT_SPAN,
    skip_weekends: bool = False,
    activities: list[dict] | None = None,
    offset: int = 0,
    pc: dict[str, tuple[float, float]] | None = None,
    only: dict | None = None,
) -> dict:
    """
    The Focus section for the range `offset` spans of `span` days back from
    `today` (stats_common.window): `range_info`'s fields; `bars`, its days
    (or weeks, for a year) with {minutes, pomodoros}; `totals` and `change`
    (stats_common.summary) of {minutes, pomodoros, sessions, tasks: worked
    on, completed}; the streak; the 10 tasks with the most focus in the
    range and the time per activity.

    Minutes count the time work phases ran, as the history does (a stopped
    or skipped one too; older entries hold finished tomatoes only), plus the
    time on calls (calls.py). A session counts once it gave a minute of
    focus, finished tomato or not; calls add to the minutes, the sessions
    and the activities, not to the tasks or the streak. The running session
    counts today with its focus so far, so only in the range that ends
    today. The streak always runs up to `today`, whatever the range; with
    `skip_weekends`, a Saturday or Sunday without focus doesn't break it.

    `activities` (the setting) names the groups of the time per activity:
    one per activity used, plus "_task" (Todoist tasks) and "_other"
    (sessions with only a typed name); see _by_activity.

    `pc` is the time the PC was in use per day, `{day: (seconds, seconds
    during a work phase)}` (App.pc_time): the bars and totals get `outside`,
    the minutes of it outside work phases (breaks, pauses, no session).

    `only` narrows everything but the streak to one activity, `{"activity":
    id}` (an id of the time per activity, "_task" and "_other" included), or
    one task, `{"task": key}` (a row's `key`): the totals, the range before,
    the bars, the tasks (an activity's) and the activities (where a task's
    time went); tasks done are those of the sessions kept. The PC time isn't
    split that way, so it is left out (`outside` 0). The answer's `filter`
    is `{kind, id, name}` (check_filter), or None; an unknown one raises
    AppError.
    """
    info = range_info(today, span, offset)
    first, last = info["first"], info["last"]
    entries = _entries(history)
    if session:
        entries.append(
            {
                "date": today.isoformat(),
                "task_id": session["task"]["id"],
                "content": session["task"]["content"],
                "pomodoros": session.get("done", 0),
                "minutes": session_minutes(session),
                "reason": "running",
                **({"activity": session["activity"]} if session.get("activity") else {}),
                **({"kind": "call"} if session.get("call") else {}),
            }
        )
    streak = _streak({h["date"] for h in _sessions(entries) if _worked(h)}, today, skip_weekends)
    sessions = _sessions(entries)
    done = [h for h in entries if h.get("completed")]
    picked = check_filter(sessions, activities or [], only)
    if picked:
        match = (
            (lambda h: _group(h) == picked["id"])
            if picked["kind"] == "activity"
            else (lambda h: _task_key(h) == picked["id"])
        )
        sessions = [h for h in sessions if match(h)]
        worked = {_task_key(h) for h in sessions}
        done = [h for h in done if _task_key(h) in worked]
        pc = None

    pc = pc or {}

    def outside(d: str) -> int:
        seconds, focus_s = pc.get(d, (0.0, 0.0))
        return minutes_of(max(0.0, seconds - focus_s))

    def total(days: list[str]) -> dict:
        lo, hi = days[0], days[-1]
        mine = [h for h in sessions if lo <= h["date"] <= hi]
        return {
            "minutes": sum(int(h.get("minutes") or 0) for h in mine),
            "pomodoros": sum(int(h.get("pomodoros") or 0) for h in mine),
            "sessions": sum(1 for h in mine if _counted(h)),
            "tasks": len({_task_key(h) for h in mine if _worked(h)}),
            "completed": sum(1 for h in done if lo <= h["date"] <= hi),
            "outside": sum(outside(d) for d in days),
        }

    days = window(today, span, offset)
    per_day = {d: {"day": d, "minutes": 0, "pomodoros": 0, "outside": outside(d)} for d in days}
    in_range = [h for h in sessions if first <= h["date"] <= last]
    for h in in_range:
        per_day[h["date"]]["minutes"] += int(h.get("minutes") or 0)
        per_day[h["date"]]["pomodoros"] += int(h.get("pomodoros") or 0)

    tasks: dict[str, dict] = {}
    for h in in_range:
        if not _worked(h):
            continue
        key = _task_key(h)
        task = tasks.setdefault(key, {"key": key, "content": "", "minutes": 0, "pomodoros": 0, "sessions": 0})
        task["content"] = h.get("content") or "Focus session"  # the latest name wins
        task["minutes"] += int(h.get("minutes") or 0)
        task["pomodoros"] += int(h.get("pomodoros") or 0)
        task["sessions"] += 1
    most = top(tasks.values(), lambda t: (-t["minutes"], -t["pomodoros"], t["content"]))

    return {
        **info,
        "bars": sum_rows(list(per_day.values()), buckets(days, info["by"]), ("minutes", "pomodoros", "outside")),
        **summary(total, today, span, offset, lambda t: t["minutes"]),
        "streak": streak,
        "tasks": most,
        "activities": _by_activity(in_range, activities or []),
        "filter": picked,
    }


def check_filter(sessions: list[dict], activities: list[dict], only) -> dict | None:
    """
    `only` (see focus_stats) as `{kind: "activity"|"task", id, name}`, the
    name from the setting or the latest session that has it ("" for "_task"
    and "_other", which the page names); None for None. Raises AppError for
    anything else, or an id no session nor activity has.
    """
    if only is None:
        return None
    if isinstance(only, dict) and len(only) == 1:
        ((kind, key),) = only.items()
        if kind in ("activity", "task") and isinstance(key, str) and key:
            if kind == "activity":
                if key in ("_task", "_other"):
                    return {"kind": kind, "id": key, "name": ""}
                known = next((a for a in activities if a.get("id") == key), None)
                names = [h["activity"].get("name") for h in sessions if _group(h) == key]
                if known or names:
                    return {"kind": kind, "id": key, "name": (known or {}).get("name") or names[-1] or key}
            else:
                names = [h.get("content") for h in sessions if _task_key(h) == key and h.get("kind") != "call"]
                if names:
                    return {"kind": kind, "id": key, "name": names[-1] or "Focus session"}
    raise app_error("errors.stats.filter")


def earliest(history: list[dict]) -> str | None:
    """The oldest day the history holds a session or a completed task on."""
    return earliest_day(h["date"] for h in _entries(history))


def _entries(history: list[dict]) -> list[dict]:
    """The history's entries that have a valid day."""
    return [h for h in history if isinstance(h, dict) and _is_day(h.get("date"))]


def _sessions(entries: list[dict]) -> list[dict]:
    """The entries that are sessions (the rest are completed tasks)."""
    return [h for h in entries if h.get("reason") in (*SESSION_REASONS, "running")]


def _by_activity(sessions: list[dict], activities: list[dict]) -> list[dict]:
    """
    Focus per activity, most first: `{id, name, icon, minutes, pomodoros,
    sessions}`. An activity takes its name and icon from the setting, or
    for one since removed the name the history kept. Sessions on a Todoist
    task are "_task", those with no activity "_other" (icon "" for both).
    """
    known = {a["id"]: a for a in activities}
    groups: dict[str, dict] = {}
    for h in sessions:
        if not _counted(h):
            continue
        key = _group(h)
        if key == "_task":
            name, icon = "Todoist tasks", ""
        elif key == "_other":
            name, icon = "Other", ""
        else:
            current = known.get(key, {})
            name, icon = current.get("name") or h["activity"].get("name") or key, current.get("icon") or "dot"
        group = groups.setdefault(
            key, {"id": key, "name": name, "icon": icon, "minutes": 0, "pomodoros": 0, "sessions": 0}
        )
        group["minutes"] += int(h.get("minutes") or 0)
        group["pomodoros"] += int(h.get("pomodoros") or 0)
        group["sessions"] += 1
    return top(groups.values(), lambda g: (-g["minutes"], -g["pomodoros"], g["name"]), None)


def _group(entry: dict) -> str:
    """The time-per-activity group of a session: "_task", its activity's id or "_other"."""
    activity = entry.get("activity") if isinstance(entry.get("activity"), dict) else {}
    if entry.get("task_id"):
        return "_task"
    return str(activity["id"]) if activity.get("id") else "_other"


def _is_day(value) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _streak(active: set[str], today: date, skip_weekends: bool = False) -> dict:
    """
    Days in a row with a tomato: up to today (or yesterday, today may still
    come), and the longest. With `skip_weekends` a weekend day without one
    is passed over instead of ending the run.
    """

    def skipped(d: date) -> bool:
        return skip_weekends and d.weekday() >= 5 and d.isoformat() not in active

    current, day = 0, today if today.isoformat() in active else today - timedelta(days=1)
    while day.isoformat() in active or skipped(day):
        current += day.isoformat() in active
        day -= timedelta(days=1)
    best = run = 0
    previous = None
    for iso in sorted(active):
        d = date.fromisoformat(iso)
        if previous:
            gap = previous + timedelta(days=1)
            while gap < d and skipped(gap):
                gap += timedelta(days=1)
        run = run + 1 if previous and gap == d else 1
        best, previous = max(best, run), d
    return {"current": current, "best": best}
