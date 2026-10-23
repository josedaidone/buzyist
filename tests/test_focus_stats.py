"""focus_stats: the Stats tab's focus numbers from history.json and the running session."""

from datetime import date, timedelta

from buzyist.api import Api
from buzyist.focus_stats import earliest, focus_stats

TODAY = date(2026, 10, 14)


def day(ago: int) -> str:
    return (TODAY - timedelta(days=ago)).isoformat()


def ran(ago, task_id="t1", content="Write the tests", pomodoros=2, reason="finished"):
    return {
        "date": day(ago),
        "task_id": task_id,
        "content": content,
        "pomodoros": pomodoros,
        "minutes": pomodoros * 25,
        "reason": reason,
    }


def completed(ago, task_id="t1"):
    return {"date": day(ago), "task_id": task_id, "content": "Write the tests", "completed": True}


def test_totals_for_today_the_range_and_the_range_before():
    history = [
        ran(0),
        ran(0, "t2", "Review", 1, "stopped"),
        ran(0, "", "Focus session", 0, "stopped"),  # no tomato: not counted
        completed(0),
        ran(3, "t1", pomodoros=4),
        ran(20, "t3", "Plan", 1),
        completed(20, "t3"),
        ran(40, "t9", "Too old", 8),  # the 30 days before
    ]
    s = focus_stats(history, None, TODAY, span=30)
    assert (s["span"], s["offset"], s["by"], s["first"], s["last"]) == (30, 0, "day", day(29), day(0))
    assert s["totals"]["today"] == {
        "minutes": 75,
        "pomodoros": 3,
        "sessions": 2,
        "tasks": 2,
        "completed": 1,
        "outside": 0,
    }
    assert s["totals"]["range"] == {
        "minutes": 200,
        "pomodoros": 8,
        "sessions": 4,
        "tasks": 3,
        "completed": 2,
        "outside": 0,
    }
    assert s["totals"]["previous"] == {
        "minutes": 200,
        "pomodoros": 8,
        "sessions": 1,
        "tasks": 1,
        "completed": 0,
        "outside": 0,
    }
    assert s["totals"]["average"] == {
        "minutes": 6.7,
        "pomodoros": 0.3,
        "sessions": 0.1,
        "tasks": 0.1,
        "completed": 0.1,
        "outside": 0.0,
    }
    assert s["change"] == 0.0
    assert len(s["bars"]) == 30
    assert s["bars"][-1] == {"day": day(0), "last": day(0), "days": 1, "minutes": 75, "pomodoros": 3, "outside": 0}
    assert s["bars"][-4] == {"day": day(3), "last": day(3), "days": 1, "minutes": 100, "pomodoros": 4, "outside": 0}
    assert s["bars"][0]["day"] == day(29)
    assert focus_stats(history[:-1], None, TODAY, span=30)["change"] is None  # nothing before: no change to show


def test_a_year_has_a_bar_per_week():
    s = focus_stats([ran(0), ran(1), ran(300, pomodoros=1)], None, TODAY, span=365)
    assert s["by"] == "week" and len(s["bars"]) == 53
    assert sum(b["minutes"] for b in s["bars"]) == s["totals"]["range"]["minutes"] == 125
    assert s["bars"][-1] == {
        "day": day(2),
        "last": day(0),
        "days": 3,
        "minutes": 100,
        "pomodoros": 4,
        "outside": 0,
    }  # Mon-Wed


def test_the_running_session_counts_today():
    session = {"task": {"id": "t1", "content": "Write the tests"}, "done": 1, "work_ms": 25 * 60_000}
    s = focus_stats([ran(0, pomodoros=1)], session, TODAY)
    assert s["totals"]["today"] == {
        "minutes": 50,
        "pomodoros": 2,
        "sessions": 2,
        "tasks": 1,
        "completed": 0,
        "outside": 0,
    }
    assert s["tasks"] == [{"key": "t1", "content": "Write the tests", "minutes": 50, "pomodoros": 2, "sessions": 2}]


def test_top_tasks_by_focus_time_and_free_sessions_by_name():
    history = [
        ran(1, "t1", "Small", 1),
        ran(2, "t2", "Big", 3),
        ran(2, "", "Reading", 2),
        ran(5, "", "Reading", 2),
        ran(6, "t2", "Big (renamed)", 1),
    ]
    tasks = focus_stats(history, None, TODAY)["tasks"]
    assert [(t["content"], t["minutes"], t["sessions"]) for t in tasks] == [
        ("Big (renamed)", 100, 2),  # the name written last wins
        ("Reading", 100, 2),
        ("Small", 25, 1),
    ]


def test_the_streak_allows_today_to_come_and_keeps_the_best():
    history = [ran(1), ran(2), ran(3), ran(10), ran(11), ran(12), ran(13), ran(14), ran(5, pomodoros=0)]
    assert focus_stats(history, None, TODAY)["streak"] == {"current": 3, "best": 5}
    assert focus_stats([*history, ran(0)], None, TODAY)["streak"] == {"current": 4, "best": 5}
    assert focus_stats([ran(2)], None, TODAY)["streak"] == {"current": 0, "best": 1}


def test_the_streak_can_skip_weekends():
    # TODAY is a Wednesday: Mon, Tue, then Fri and Thu, the weekend between them empty.
    history = [ran(1), ran(2), ran(5), ran(6)]
    assert focus_stats(history, None, TODAY)["streak"] == {"current": 2, "best": 2}
    assert focus_stats(history, None, TODAY, skip_weekends=True)["streak"] == {"current": 4, "best": 4}
    # A weekend tomato still counts; an empty Friday still breaks it.
    assert focus_stats([ran(1), ran(2), ran(3), ran(6)], None, TODAY, skip_weekends=True)["streak"] == {
        "current": 3,
        "best": 3,
    }
    # On a Sunday with nothing done yet, Friday's run is still going.
    sunday = TODAY + timedelta(days=4)
    assert focus_stats([ran(-2), ran(-1)], None, sunday, skip_weekends=True)["streak"] == {"current": 2, "best": 2}
    assert focus_stats([ran(-2), ran(1)], None, sunday, skip_weekends=True)["streak"] == {"current": 1, "best": 1}


def test_nothing_yet_and_odd_entries():
    s = focus_stats([{"date": 5}, "junk", {"date": "not a day", "pomodoros": 1, "reason": "finished"}], None, TODAY)
    assert s["totals"]["range"] == {
        "minutes": 0,
        "pomodoros": 0,
        "sessions": 0,
        "tasks": 0,
        "completed": 0,
        "outside": 0,
    }
    assert s["tasks"] == []
    assert s["streak"] == {"current": 0, "best": 0}


def test_focus_by_activity():
    email = {"id": "email", "name": "Email", "detail": "Q4"}
    old = {"id": "gone", "name": "Old name", "detail": ""}
    history = [
        {**ran(0, "", "Email · Q4", 2), "activity": email},
        {**ran(1, "", "Email", 1), "activity": {**email, "name": "Mail"}},  # renamed since: the setting's name wins
        {**ran(2, "", "Old name", 3), "activity": old},  # removed from the setting
        ran(3, "t1", "Write the tests", 1),
        ran(4, "", "Plan", 1),
        {**ran(5, "", "Email", 0, "stopped"), "activity": email},  # no tomato: not counted
        {**ran(40, "", "Email", 8), "activity": email},  # too old
    ]
    running = {"task": {"id": "", "content": "Email"}, "done": 1, "work_ms": 25 * 60000, "activity": email}
    s = focus_stats(history, running, TODAY, activities=[{"id": "email", "name": "Email", "icon": "mail"}])
    assert [(g["id"], g["name"], g["icon"], g["minutes"], g["sessions"]) for g in s["activities"]] == [
        ("email", "Email", "mail", 100, 3),
        ("gone", "Old name", "dot", 75, 1),
        ("_other", "Other", "", 25, 1),
        ("_task", "Todoist tasks", "", 25, 1),
    ]


def test_an_earlier_range():
    history = [
        ran(0),
        ran(5, "t2", "Review", 1),
        ran(35, "t3", "Plan", 3),
        completed(36, "t3"),
        ran(70, "t9", pomodoros=8),
    ]
    running = {"task": {"id": "t1", "content": "Write the tests"}, "done": 1, "work_ms": 25 * 60_000}
    s = focus_stats(history, running, TODAY, span=30, offset=1)
    assert (s["first"], s["last"], s["offset"]) == (day(59), day(30), 1)
    assert s["bars"][-1]["day"] == day(30)
    assert s["totals"]["range"] == {
        "minutes": 75,
        "pomodoros": 3,
        "sessions": 1,
        "tasks": 1,
        "completed": 1,
        "outside": 0,
    }
    assert s["totals"]["previous"]["minutes"] == 200
    assert "today" not in s["totals"]  # not in this range
    assert s["change"] == round((75 - 200) / 200, 4)
    assert [t["content"] for t in s["tasks"]] == ["Plan"]  # not the running session, not the newer ones
    # The streak still runs up to today, the running session included.
    assert s["streak"] == focus_stats(history, running, TODAY)["streak"] == {"current": 1, "best": 1}
    assert focus_stats(history, None, TODAY, span=7, offset=10)["totals"]["range"]["pomodoros"] == 8


def test_the_earliest_day():
    assert earliest([ran(3), completed(40), {"date": "junk"}, "junk"]) == day(40)
    assert earliest([]) is None


def test_the_running_session_keeps_the_streak_going():
    running = {"task": {"id": "t1", "content": "Write the tests"}, "done": 1, "work_ms": 25 * 60_000}
    assert focus_stats([ran(1)], running, TODAY)["streak"] == {"current": 2, "best": 2}


def test_the_stats_tab_answer(make_app, clock):
    app = make_app({"stats_span": 7})
    today = clock.now().date()

    def back(n: int) -> str:
        return (today - timedelta(days=n)).isoformat()

    app.history = [{**ran(0), "date": back(0)}, {**ran(0), "date": back(20)}]
    s = Api(app).stats()
    assert s["ok"] and s["range"] == {
        "span": 7,
        "offset": 0,
        "by": "day",
        "first": back(6),
        "last": back(0),
        "has_older": True,  # 20 days back is two weeks before this one
    }
    assert s["focus"]["span"] == s["apps"]["span"] == s["blocks"]["span"] == 7
    assert s["focus"]["totals"]["range"]["minutes"] == 50
    older = Api(app).stats(None, 2)["range"]
    assert (older["offset"], older["last"], older["has_older"]) == (2, back(14), False)
    assert Api(app).stats(None, 99)["range"]["offset"] == 2  # no further back than the data
    assert Api(app).stats(None, -1)["range"]["offset"] == 0
    year = Api(app).stats(365, 0)
    assert year["range"]["by"] == "week" and year["range"]["has_older"] is False
    assert Api(app).stats(14)["range"]["span"] == 7  # not one of the picker's: the default


def test_time_on_the_pc_outside_focus():
    pc = {day(0): (3600.0, 1500.0), day(1): (600.0, 0.0), day(8): (1200.0, 0.0), day(2): (100.0, 200.0)}
    s = focus_stats([ran(0, pomodoros=1)], None, TODAY, span=7, pc=pc)
    assert s["totals"]["today"]["outside"] == 35  # an hour on the PC, 25 min of it in a work phase
    assert s["totals"]["range"]["outside"] == 35 + 10  # focus over the total can't make it negative
    assert s["totals"]["previous"]["outside"] == 20
    assert [b["outside"] for b in s["bars"]][-3:] == [0, 10, 35]


def test_a_single_day():
    history = [ran(0, pomodoros=1), ran(1, "t2", "Review", 2)]
    s = focus_stats(history, None, TODAY, span=1)
    assert (s["first"], s["last"], s["by"], len(s["bars"])) == (day(0), day(0), "day", 1)
    assert s["totals"]["range"] == s["totals"]["today"]
    assert s["totals"]["previous"]["minutes"] == 50
    assert s["change"] == -0.5
    yesterday = focus_stats(history, None, TODAY, span=1, offset=1)
    assert yesterday["first"] == day(1) and yesterday["tasks"][0]["content"] == "Review"


def filtered_history():
    email = {"id": "email", "name": "Email", "detail": ""}
    return [
        {**ran(0, "", "Email", 2), "activity": email},
        {**ran(8, "", "Email", 1), "activity": email},  # the week before
        ran(1, "t1", "Write the tests", 3),
        completed(1, "t1"),
        ran(2, "t2", "Review", 1),
        completed(2, "t2"),
        ran(3, "", "Plan", 1),
        ran(4, "", "Plan", 0, "stopped"),  # no tomato
        {**ran(5, "", "Call · Zoom", 0), "kind": "call", "minutes": 30, "activity": email},
    ]


def test_filter_by_activity():
    pc = {day(0): (7200.0, 0.0)}
    s = focus_stats(filtered_history(), None, TODAY, span=7, pc=pc, only={"activity": "email"})
    assert s["filter"] == {"kind": "activity", "id": "email", "name": "Email"}
    assert s["totals"]["range"] == {
        "minutes": 50 + 30,  # the call counts too
        "pomodoros": 2,
        "sessions": 2,
        "tasks": 1,
        "completed": 0,
        "outside": 0,  # the PC time isn't split by activity
    }
    assert s["totals"]["previous"]["minutes"] == 25  # the range before is narrowed too
    assert sum(b["minutes"] for b in s["bars"]) == 80 and all(b["outside"] == 0 for b in s["bars"])
    assert [t["content"] for t in s["tasks"]] == ["Email"]  # the activity's sessions
    assert [g["id"] for g in s["activities"]] == ["email"]
    assert s["streak"] == focus_stats(filtered_history(), None, TODAY, span=7)["streak"]  # untouched


def test_filter_by_the_todoist_and_other_groups():
    s = focus_stats(filtered_history(), None, TODAY, span=7, only={"activity": "_task"})
    assert s["filter"] == {"kind": "activity", "id": "_task", "name": ""}
    assert [t["content"] for t in s["tasks"]] == ["Write the tests", "Review"]
    assert (s["totals"]["range"]["minutes"], s["totals"]["range"]["completed"]) == (100, 2)
    s = focus_stats(filtered_history(), None, TODAY, span=7, only={"activity": "_other"})
    assert [t["content"] for t in s["tasks"]] == ["Plan"]
    assert (s["totals"]["range"]["sessions"], s["totals"]["range"]["completed"]) == (1, 0)


def test_filter_by_task():
    history = [*filtered_history(), {**ran(6, "t1", "Write the tests", 1), "activity": {"id": "docs", "name": "Docs"}}]
    s = focus_stats(history, None, TODAY, span=7, only={"task": "t1"})
    assert s["filter"] == {"kind": "task", "id": "t1", "name": "Write the tests"}
    assert (s["totals"]["range"]["minutes"], s["totals"]["range"]["completed"]) == (100, 1)
    assert [t["key"] for t in s["tasks"]] == ["t1"]
    assert [(g["id"], g["minutes"]) for g in s["activities"]] == [("_task", 100)]  # where its time went
    named = focus_stats(history, None, TODAY, span=7, only={"task": "name:Plan"})
    assert named["filter"]["name"] == "Plan" and named["totals"]["range"]["minutes"] == 25
    # another range with nothing of it: zeros, not an error
    assert focus_stats(history, None, TODAY, span=7, offset=3, only={"task": "t1"})["totals"]["range"]["minutes"] == 0


def test_an_unknown_filter_is_refused(make_app):
    app = make_app({})
    app.history = filtered_history()
    api = Api(app)
    assert api.stats(7, 0, {"activity": "email"})["focus"]["filter"]["name"] == "Email"
    for bad in (
        {"activity": "nope"},
        {"task": "t9"},
        {"task": "name:Call · Zoom"},
        {"other": "x"},
        {},
        "email",
        {"task": ""},
    ):
        answer = api.stats(7, 0, bad)
        assert not answer["ok"] and answer["error"] == "That activity or task isn't in your stats any more.", bad
    assert api.stats(7, 0, None)["focus"]["filter"] is None


def test_a_session_stopped_before_its_first_tomato_counts():
    """Its minutes, the session, the task worked on and the streak; not a tomato."""
    history = [
        {
            "date": TODAY.isoformat(),
            "task_id": "t1",
            "content": "Write",
            "pomodoros": 0,
            "minutes": 12,
            "reason": "stopped",
        },
        {
            "date": TODAY.isoformat(),
            "task_id": "t2",
            "content": "Noise",
            "pomodoros": 0,
            "minutes": 0,
            "reason": "stopped",
        },
    ]
    s = focus_stats(history, None, TODAY)
    today = s["totals"]["today"]
    assert (today["minutes"], today["pomodoros"], today["sessions"], today["tasks"]) == (12, 0, 1, 1)
    assert [t["key"] for t in s["tasks"]] == ["t1"]
    assert s["streak"]["current"] == 1
