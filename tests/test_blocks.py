"""BlockLog (blocks.json): the websites the extension blocked, counted for the Stats tab and the popup."""

import json
from datetime import timedelta

import pytest

from buzyist import paths
from buzyist.blocks import BlockLog

pytestmark = pytest.mark.usefixtures("clock")


def block(clock, n=0, kind="focus", host="youtube.com", ago_ms=0):
    return {"id": f"block{n:04d}", "at": clock.ms - ago_ms, "kind": kind, "host": host}


def test_counts_per_day_kind_and_site_and_saves(clock):
    log = BlockLog(paths.BLOCKS_PATH)
    events = [block(clock, 1), block(clock, 2), block(clock, 3, "limit", "WWW.Reddit.com")]
    assert log.add(events) == 3
    day = clock.now().date().isoformat()
    assert log.days == {day: {"focus": {"youtube.com": 2}, "limit": {"reddit.com": 1}}}
    assert json.loads(paths.BLOCKS_PATH.read_text("utf-8")) == {"days": log.days}
    assert BlockLog(paths.BLOCKS_PATH).days == log.days  # read back on the next start
    assert log.today() == {"day": day, "focus": 2, "limit": 1}


def test_a_report_sent_twice_counts_once(clock):
    log = BlockLog(paths.BLOCKS_PATH)
    assert log.add([block(clock, 1)]) == 1
    assert log.add([block(clock, 1), block(clock, 2)]) == 1
    assert log.today()["focus"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "app"},  # not a website block
        {"host": ""},
        {"host": "<script>"},
        {"id": "x"},  # too short to be ours
        {"at": "soon"},
        {"ago_ms": 3 * 86_400_000 + 1},  # older than 3 days
        {"ago_ms": -120_000},  # in the future
    ],
)
def test_malformed_reports_are_skipped(clock, bad):
    event = block(clock, ago_ms=bad.pop("ago_ms", 0))
    event.update(bad)
    log = BlockLog(paths.BLOCKS_PATH)
    assert log.add([event, "junk", None]) == 0
    assert log.days == {}
    assert not paths.BLOCKS_PATH.exists()  # nothing counted, nothing written


def test_not_a_list_counts_nothing():
    assert BlockLog(paths.BLOCKS_PATH).add({"id": "block0001"}) == 0


def test_an_unreadable_file_starts_over(clock):
    paths.BLOCKS_PATH.write_text('{"days": {"2026-01-05": {"focus": {"a.com": "many"}}}}', "utf-8")
    assert BlockLog(paths.BLOCKS_PATH).days == {}


def test_stats_cover_the_last_30_days(clock):
    log = BlockLog(paths.BLOCKS_PATH)
    day = 86_400_000
    log.add(
        [
            block(clock, 1),  # today
            block(clock, 2, "limit", "reddit.com", ago_ms=2 * day),
            block(clock, 3, "limit", "reddit.com", ago_ms=2 * day + 1),
            block(clock, 4, ago_ms=2 * day + 2),
        ]
    )
    clock.advance(days=10)  # the oldest of them is now 12 days back
    log.add([block(clock, 5, host="news.ycombinator.com")])
    s = log.stats(clock.now().date(), span=30)
    assert s["span"] == 30 and len(s["bars"]) == 30
    today = clock.now().date().isoformat()
    assert s["bars"][-1] == {"day": today, "last": today, "days": 1, "focus": 1, "limit": 0}
    assert s["totals"] == {
        "today": {"focus": 1, "limit": 0},
        "range": {"focus": 3, "limit": 2},
        "previous": {"focus": 0, "limit": 0},
        "average": {"focus": 0.1, "limit": 0.1},
    }
    assert s["change"] is None
    assert log.earliest() == (clock.now().date() - timedelta(days=12)).isoformat()
    assert s["sites"] == [
        {"host": "reddit.com", "focus": 0, "limit": 2},
        {"host": "youtube.com", "focus": 2, "limit": 0},
        {"host": "news.ycombinator.com", "focus": 1, "limit": 0},
    ]


def test_days_older_than_a_year_are_dropped(clock):
    log = BlockLog(paths.BLOCKS_PATH)
    log.add([block(clock, 1)])
    clock.advance(days=401)
    log.add([block(clock, 2)])
    assert list(log.days) == [clock.now().date().isoformat()]


def test_usage_posts_carry_blocks_and_answers_say_todays(clock, app, focus_server):
    r = focus_server.post("/usage", {"usage": {}, "blocks": [block(clock, 1), block(clock, 2, "limit")]})
    assert r.status == 200
    assert r.json["blocks_today"] == {"day": clock.now().date().isoformat(), "focus": 1, "limit": 1}
    assert app.blocks.today()["focus"] == 1


def test_stats_of_an_earlier_range(clock):
    log = BlockLog(paths.BLOCKS_PATH)
    day = 86_400_000
    log.add([block(clock, 1, ago_ms=2 * day), block(clock, 2, "limit", "reddit.com", ago_ms=2 * day + 1)])
    clock.advance(days=8)  # both are now 10 days back: in the week before the last one
    log.add([block(clock, 3)])
    today = clock.now().date()
    s = log.stats(today, span=7, offset=1)
    assert len(s["bars"]) == 7 and s["last"] == (today - timedelta(days=7)).isoformat()
    assert s["totals"]["range"] == {"focus": 1, "limit": 1} and "today" not in s["totals"]
    assert [x["host"] for x in s["sites"]] == ["reddit.com", "youtube.com"]
    now = log.stats(today, span=7)
    assert now["totals"]["range"] == {"focus": 1, "limit": 0}
    assert now["totals"]["previous"] == {"focus": 1, "limit": 1}
    assert now["change"] == -0.5
    assert log.stats(today, span=365)["bars"][-1]["day"] <= today.isoformat()
