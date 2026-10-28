"""The mini timer's controls: +5 minutes on the phase running now, and done / carry over on the running task."""

from __future__ import annotations

import re

import pytest
from busylib import types
from test_engine import BAR_CONFIG, device, engine, notes, start  # noqa: F401 - fixtures

from buzyist import constants, paths
from buzyist.constants import AppError

ADD_MS = constants.ADD_MINUTES * 60000


def left_ms(app):
    return app.timer_view()["left_ms"]


def test_add_time_makes_the_work_phase_longer_and_counts_it(engine, deferred_threads):  # noqa: F811
    app = engine({"work_minutes": 25, "rest_minutes": 5, "cycles": 2})
    start(app, deferred_threads, "t1")
    app.step(minutes=10)
    app.control("add")
    view = app.timer_view()
    assert (view["phase"], view["left_ms"], view["total_ms"]) == ("work", 20 * 60000, 30 * 60000)
    assert app.session["extra"] == {"0": ADD_MS}
    app.step(minutes=19)
    assert app.timer_view()["phase"] == "work"
    app.step(minutes=1, seconds=1)  # 30 min in: the break, of its usual length
    view = app.timer_view()
    assert (view["phase"], view["total_ms"]) == ("rest", 5 * 60000)
    assert app.session["focus_ms"] == 30 * 60000
    app.step(minutes=5)
    assert app.timer_view()["total_ms"] == 25 * 60000  # the next work phase isn't longer
    app.step(minutes=25)
    (entry,) = app.history
    assert (entry["pomodoros"], entry["minutes"], entry["reason"]) == (2, 55, "finished")


def test_add_time_while_paused_and_on_a_break(engine, deferred_threads):  # noqa: F811
    app = engine({"work_minutes": 25, "rest_minutes": 5, "cycles": 2})
    start(app, deferred_threads, "t1")
    app.step(minutes=25, seconds=1)  # the break
    app.control("pause")
    before = left_ms(app)
    app.control("add")
    app.control("add")
    view = app.timer_view()
    assert view["paused"] and view["left_ms"] == before + 2 * ADD_MS
    assert view["total_ms"] == 5 * 60000 + 2 * ADD_MS
    assert "extra" not in app.session  # a break's minutes aren't focus
    app.control("resume")
    app.step(minutes=4)
    assert app.timer_view()["total_ms"] == 5 * 60000 + 2 * ADD_MS  # a resume keeps the longer break


def test_a_pause_keeps_the_added_time(engine, deferred_threads):  # noqa: F811
    app = engine({"work_minutes": 25, "cycles": 1})
    start(app, deferred_threads, "t1")
    app.step(minutes=20)
    app.control("add")
    app.control("pause")
    view = app.timer_view()
    assert (view["left_ms"], view["total_ms"]) == (10 * 60000, 30 * 60000)
    app.control("resume")
    app.step(minutes=10, seconds=1)
    (entry,) = app.history
    assert (entry["pomodoros"], entry["minutes"]) == (1, 30)


def test_add_time_on_the_bar(engine, deferred_threads, device):  # noqa: F811
    app = engine({**BAR_CONFIG, "work_minutes": 25, "cycles": 2})
    start(app, deferred_threads, "t1")
    app.step(minutes=10)
    app.control("add")
    inner = device.snap.snapshot
    assert (inner.current_interval_time_total_ms, inner.current_interval_time_left_ms) == (30 * 60000, 20 * 60000)
    app.step()
    assert left_ms(app) == 20 * 60000


@pytest.mark.parametrize("followup", [False, True])
def test_done_on_the_mini_timer_closes_the_task_and_ends_quietly(engine, deferred_threads, notes, followup):  # noqa: F811
    app = engine({"cycles": 2})
    start(app, deferred_threads, "t1", "Write the tests")
    app.step(minutes=10)
    result = app.complete_running(followup)
    deferred_threads.run_pending()
    assert app.todoist.closed == ["t1"]
    assert ("copy_to_tomorrow" in app.todoist.names()) is followup
    assert ("followup" in result) is followup
    assert app.session is None and not paths.SESSION_PATH.exists()
    assert app.ended is None  # no summary asking to complete it again
    stopped, done = sorted(app.history, key=lambda h: "completed" in h)
    assert (stopped["reason"], stopped["minutes"]) == ("stopped", 10)
    assert done["completed"] and done["task_id"] == "t1"
    assert notes == []


def test_done_fails_without_ending_the_session(engine, deferred_threads):  # noqa: F811
    app = engine({"cycles": 2})
    start(app, deferred_threads, "t1")
    app.step(minutes=10)
    app.todoist.errors["close"] = AppError("Todoist answered 500")
    with pytest.raises(AppError):
        app.complete_running()
    assert app.session is not None and app.timer_view()["phase"] == "work"


def test_done_needs_a_todoist_task(engine, deferred_threads):  # noqa: F811
    app = engine()
    start(app, deferred_threads, None, "Reading")
    with pytest.raises(AppError):
        app.complete_running()
    assert app.session is not None
    assert isinstance(app.bar.local.busy_snapshot().snapshot, types.BusySnapshotInterval)


def test_the_mini_timer_knows_how_many_minutes_it_adds():
    source = (paths.UI / "mini.js").read_text(encoding="utf-8")
    assert int(re.search(r"const ADD_MINUTES = (\d+);", source).group(1)) == constants.ADD_MINUTES
