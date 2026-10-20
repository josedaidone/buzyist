"""
What the Stats tab's three sections (focus_stats.py, blocks.py, app_time.py)
share: the days a range covers, those days grouped into bars, totals, the
change from one range to the one before, and how far back the arrows go.

A range is `span` days ending `offset` whole ranges before `today`: offset 0
ends today, offset 1 is the `span` days before those, and so on. Days are
ISO strings ("2026-10-14"), the keys every stats file uses.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta

SPANS = (7, 30, 90, 365)  # the range picker's choices
DEFAULT_SPAN = 30
WEEKLY_FROM = 365  # a range this long gets one bar per week


def clamp_span(span) -> int:
    """`span` when it is one of SPANS, else DEFAULT_SPAN."""
    return span if span in SPANS and not isinstance(span, bool) else DEFAULT_SPAN


def window(today: date, span: int, offset: int = 0) -> list[str]:
    """The `span` days of the range `offset` ranges back, oldest first."""
    last = today - timedelta(days=span * offset)
    return [(last - timedelta(days=i)).isoformat() for i in range(span - 1, -1, -1)]


def bar_size(span: int) -> str:
    """One bar per "week" for a range long enough to need it, else per "day"."""
    return "week" if span >= WEEKLY_FROM else "day"


def buckets(days: list[str], by: str = "day") -> list[dict]:
    """
    The bars a chart of `days` has: `{day, last, days}`, its first and last
    day and how many days it covers. By "week", consecutive days of one ISO
    week (Monday first) share a bar, so the first and last can be partial.
    """
    bars: list[dict] = []
    for d in days:
        if not bars or by == "day" or date.fromisoformat(d).weekday() == 0:
            bars.append({"day": d, "last": d, "days": 1})
        else:
            bars[-1]["last"] = d
            bars[-1]["days"] += 1
    return bars


def sum_rows(rows: list[dict], bars: list[dict], keys: Iterable[str]) -> list[dict]:
    """
    `bars` (from buckets) with the sum of each key over the per-day `rows`
    ({"day": ..., key: n}) that fall in them.
    """
    keys = tuple(keys)
    out = [{**b, **dict.fromkeys(keys, 0)} for b in bars]
    i = 0
    for row in sorted(rows, key=lambda r: r["day"]):
        while i < len(out) and row["day"] > out[i]["last"]:
            i += 1
        if i == len(out):
            break
        if row["day"] >= out[i]["day"]:
            for k in keys:
                out[i][k] += row[k]
    return out


def totals(rows: Iterable[dict], keys: Iterable[str]) -> dict:
    """The sum of each key over `rows`."""
    rows, keys = list(rows), tuple(keys)
    return {k: sum(r[k] for r in rows) for k in keys}


def change(now: float, before: float) -> float | None:
    """How much `now` grew on `before`, as a share (0.12 for +12%); None when there was nothing before."""
    if before <= 0:
        return None
    return round((now - before) / before, 4)


def top(items: Iterable[dict], key: Callable[[dict], tuple], n: int | None = 10) -> list[dict]:
    """The first `n` of `items` in `key` order (all of them with n None)."""
    ranked = sorted(items, key=key)
    return ranked if n is None else ranked[:n]


def oldest_offset(today: date, span: int, earliest: str | None) -> int:
    """
    The furthest range back that still holds a day on or after `earliest`
    (the oldest day with data); 0 with no data, or none before this range.
    """
    if not earliest:
        return 0
    back = (today - date.fromisoformat(earliest)).days
    return max(0, back // span)


def clamp_offset(offset, today: date, span: int, earliest: str | None) -> int:
    """`offset` kept between 0 (ending today) and oldest_offset."""
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    return min(max(offset, 0), oldest_offset(today, span, earliest))


def earliest_day(days: Iterable[str]) -> str | None:
    """The oldest of `days`, or None when there is none."""
    return min(days, default=None)


def range_info(today: date, span: int, offset: int) -> dict:
    """What every section's answer starts with: `{span, offset, by, first, last}`."""
    days = window(today, span, offset)
    return {"span": span, "offset": offset, "by": bar_size(span), "first": days[0], "last": days[-1]}


def summary(
    total: Callable[[list[str]], dict],
    today: date,
    span: int,
    offset: int,
    main: Callable[[dict], float],
) -> dict:
    """
    A section's tiles: `totals` = {range, previous (the range before it),
    average (per day of the range), today (only when the range ends
    today)}, each what `total(days)` gives for those days, and `change`,
    how much `main(totals)` grew on the range before (None with nothing
    before).
    """
    days = window(today, span, offset)
    now, before = total(days), total(window(today, span, offset + 1))
    totals_ = {"range": now, "previous": before, "average": {k: round(v / span, 1) for k, v in now.items()}}
    if offset == 0:
        totals_["today"] = total(days[-1:])
    return {"totals": totals_, "change": change(main(now), main(before))}
