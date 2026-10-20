"""stats_common: the ranges, bars, totals and offsets the Stats tab's three sections share."""

import json
from datetime import date
from pathlib import Path

import pytest

from buzyist.stats_common import (
    DEFAULT_SPAN,
    SPANS,
    bar_size,
    buckets,
    change,
    clamp_offset,
    clamp_span,
    earliest_day,
    oldest_offset,
    sum_rows,
    top,
    totals,
    window,
)

CASES = json.loads((Path(__file__).parent / "stats_cases.json").read_text(encoding="utf-8"))


def day(iso: str) -> date:
    return date.fromisoformat(iso)


@pytest.mark.parametrize("case", CASES["window"], ids=lambda c: f"{c['today']}-{c['span']}-{c['offset']}")
def test_window(case):
    days = window(day(case["today"]), case["span"], case["offset"])
    assert len(days) == case["span"]
    assert (days[0], days[-1]) == (case["first"], case["last"])
    assert days == sorted(days) and len(set(days)) == len(days)


def test_ranges_follow_each_other():
    today = day("2026-10-14")
    for span in SPANS:
        newer, older = window(today, span, 0), window(today, span, 1)
        assert day(older[-1]).toordinal() + 1 == day(newer[0]).toordinal()


@pytest.mark.parametrize("case", CASES["buckets"], ids=lambda c: c["_"])
def test_buckets(case):
    bars = buckets(window(day(case["today"]), case["span"]), case["by"])
    assert [[b["day"], b["last"], b["days"]] for b in bars] == case["bars"]


def test_a_year_is_weeks():
    year = CASES["year"]
    assert bar_size(365) == "week" and all(bar_size(s) == "day" for s in SPANS if s < 365)
    bars = buckets(window(day(year["today"]), 365), "week")
    assert len(bars) == year["bars"]
    assert [bars[0]["day"], bars[0]["last"], bars[0]["days"]] == year["first"]
    assert [bars[-1]["day"], bars[-1]["last"], bars[-1]["days"]] == year["last"]
    assert sum(b["days"] for b in bars) == 365
    assert all(day(b["day"]).weekday() == 0 for b in bars[1:])


def test_sum_rows_adds_the_days_of_each_bar():
    bars = buckets(window(day("2026-10-14"), 7), "week")  # Thu-Sun, Mon-Wed
    rows = [
        {"day": "2026-10-07", "n": 100},  # before the range
        {"day": "2026-10-08", "n": 1},
        {"day": "2026-10-11", "n": 2},
        {"day": "2026-10-12", "n": 4},
        {"day": "2026-10-14", "n": 8},
        {"day": "2026-10-15", "n": 100},  # after it
    ]
    assert [b["n"] for b in sum_rows(list(reversed(rows)), bars, ["n"])] == [3, 12]
    assert [b["n"] for b in sum_rows([], bars, ["n"])] == [0, 0]


def test_totals_and_top():
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert totals(rows, ("a", "b")) == {"a": 4, "b": 6}
    assert totals([], ("a",)) == {"a": 0}
    items = [{"n": i} for i in range(15)]
    assert [i["n"] for i in top(items, lambda i: (-i["n"],))] == list(range(14, 4, -1))
    assert len(top(items, lambda i: (i["n"],), None)) == 15


@pytest.mark.parametrize("case", CASES["change"], ids=lambda c: f"{c['now']}-{c['before']}")
def test_change(case):
    assert change(case["now"], case["before"]) == case["out"]


@pytest.mark.parametrize("case", CASES["offsets"], ids=lambda c: f"{c['span']}-{c['earliest']}")
def test_oldest_offset(case):
    today = day(case["today"])
    assert oldest_offset(today, case["span"], case["earliest"]) == case["max"]
    if case["earliest"] and case["max"]:  # the oldest range holds the oldest day; one more back wouldn't
        assert window(today, case["span"], case["max"])[-1] >= case["earliest"]
        assert window(today, case["span"], case["max"] + 1)[-1] < case["earliest"]


def test_clamp_offset_and_span():
    today = day("2026-10-14")
    assert clamp_offset(5, today, 30, "2026-09-14") == 1
    assert clamp_offset(-2, today, 30, "2026-09-14") == 0
    assert clamp_offset("1", today, 30, "2026-09-14") == 1
    assert clamp_offset("x", today, 30, "2026-09-14") == 0
    assert clamp_offset(None, today, 30, None) == 0
    assert [clamp_span(s) for s in SPANS] == list(SPANS)
    assert clamp_span(14) == clamp_span("30") == clamp_span(None) == clamp_span(True) == DEFAULT_SPAN


def test_earliest_day():
    assert earliest_day(["2026-10-14", "2025-01-02", "2026-01-01"]) == "2025-01-02"
    assert earliest_day([]) is None
