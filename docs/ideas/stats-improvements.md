# Stats tab: ranges, navigation, drill-down, better charts

Scope agreed: a range picker and stepping back through time, filtering the
Focus section by an activity or a task, a readable and keyboard-friendly
chart, and one shared helper for what `focus_stats.py`, `blocks.py` and
`app_time.py` each do on their own today. No new settings beyond the
remembered range. Out of scope: daily goal, time-of-day heatmap, partial
pomodoros, moving Stats out of Settings, sharing stats across PCs, export.

## Behaviour

- **Range picker** at the top of the Stats tab: 7 days / 30 days / 90 days /
  1 year. It drives all three sections (Focus, Time in apps, Websites
  blocked). The choice is a setting, `stats_span` (7 | 30 | 90 | 365, default
  30), saved straight away like `streak_skip_weekends`. It goes in
  config.json, not localStorage, because `clear_webview_cache` wipes the
  latter.
- **‹ › arrows** around a range label ("Mar 2 – Mar 31", `formatDate`).
  `offset` counts whole ranges back from today (0 = ending today). The ›
  arrow is disabled at 0. The ‹ arrow is disabled once the window starts
  before the oldest data any section holds (`earliest`, below), so you can
  step back as far as the 400 days of app and block data, or the oldest
  history entry. Changing the range resets `offset` to 0.
- **Tiles** (each section): with offset 0, *Today*, *This range* and
  *Previous range*; with offset > 0, *This range*, *Previous range* and
  *Daily average*, because "Today" isn't in view. *This range* shows the
  change against the previous range ("+12% vs previous 30 days", shown only
  when the previous range has data). The tile details stay as now
  (pomodoros · sessions, tasks · completed; during focus; focus · over a
  limit).
- **Chart**: one bar per day for 7/30/90 days, one per ISO week (Monday
  first; the first and last weeks can be partial, and their tooltip names
  the days they cover) for 1 year.
- **Streak** doesn't depend on the range: it always runs up to today.
- **Running session** counts only when the range includes today (offset 0).
- **Lists** (activities, top tasks, apps, websites, blocked sites) cover the
  selected range. The headings lose their fixed "last 30 days" ("Most
  focused tasks"); the range label at the top says which period it is. App
  and site rows show *range total · % during focus* plus *today* when the
  offset is 0, instead of today/week.
- **Drill-down (Focus only)**: clicking an activity row or a top-task row
  (a button, so Enter works too) filters the Focus tiles, chart and the
  other list to it. A chip above the tiles ("Email ×", "Task: Write report
  ×") clears it. With an activity filter the top-tasks list shows that
  activity's sessions; with a task filter the activity list shows where
  that task's time went. The filter clears when the range changes, not
  when stepping with the arrows. It isn't saved.

## Python

### New `buzyist/stats_common.py`

Leaf module, standard library only, placed after `blocks.py` in the
CLAUDE.md module table.

- `SPANS = (7, 30, 90, 365)`
- `window(today, span, offset) -> list[str]`: the ISO days of the range,
  oldest first. `previous_window(...)` is `window(today, span, offset + 1)`.
- `buckets(days, by) -> list[{"day": first, "last": last, "days": n}]` with
  `by = "week" if span == 365 else "day"`; `sum_rows(rows, buckets, keys)`
  adds per-day rows into them.
- `totals(rows, keys)`, `change(now, before)` (None when `before` is 0),
  `top(items, key, n)`.
- `clamp_span(span)`, `clamp_offset(offset, today, span, earliest)`.

### The three modules

Each `stats()` / `focus_stats()` takes `today`, `span`, `offset` (and Focus
also takes `filter`), built on `stats_common`. **No `date.today()` inside**;
`App` passes it, the same way `focus_stats` already gets it. Each returns:

```
{span, offset, by, first, last, earliest,
 bars: [...],                       # bucketed rows; tooltip data per bar
 totals: {today?, range, previous, average},
 change: {minutes|focus+other|focus+limit: float|None},
 ...lists}
```

- `focus_stats`: `filter = {"activity": id} | {"task": key} | None`,
  applied to `sessions` before everything except the streak. Task rows gain
  `key` (`_task_key`) so the UI can send it back. `earliest` is the oldest
  history date.
- `BlockLog.stats` / `AppTime.stats`: `earliest` is the oldest day held.
  App rows get `range`/`focus` (and `today` when offset is 0) in place of
  `today`/`week`/`span`.

### Api and App

- One `Api.stats(span=None, offset=0, filter=None)` → `App.stats(...)`,
  returning `{range: {span, offset, first, last, has_older}, focus, apps,
  blocks}`. It replaces `focus_stats`, `block_stats` and `app_time_stats`
  (only `ui/main.js` calls them; drop them in the same change). `span=None`
  takes `stats_span`. `has_older` uses the oldest `earliest` of the three.
  The `apps` block keeps `on`/`extension`/`sites` as `app_time_stats` builds
  them today.
- `stats_span`: add it to `DEFAULTS` and `Config` (`Literal[7, 30, 90,
  365]`). No migration needed (it's a new key with a default).

## UI (`ui/main.html`, `ui/main.js`, `ui/style.css`)

- Header row at the top of `#p_stats`: a segmented control (four
  `button`s with `aria-pressed`), ‹ label ›, and the filter chip.
  `loadStats()` keeps `{offset, filter}` in memory and makes one
  `api.stats(...)` call.
- `statTiles(s)` builds the tiles from `totals` and `change`, as described
  above.
- `barChart` rewrite (still plain DOM):
  - y-axis: `niceTicks(max, 3)` (1/2/5 × 10ⁿ steps, minutes rounded to
    15/30/60 when `duration` formats them), gridlines as absolutely
    positioned rules, labels on the left in place of `.stat-max`.
  - x-axis: a label under the first bar, under every Monday (7/30 days),
    every month start (90 days / 1 year) and "Today" for the last bar at
    offset 0. Labels that would overlap are skipped.
  - keyboard: the chart is one tab stop (`tabindex=0`, `role="group"`,
    `aria-label` = the summary it has now). ←/→ move the active bar,
    Home/End jump, and the active bar shows the same tooltip as hover. An
    `aria-live="polite"` element repeats the tooltip's text.
  - week bars: the tooltip says "Mar 3 – Mar 9" plus the totals.
- Activity and task rows become `button.stat-site` (the same look plus a
  focus ring) when they can filter.
- i18n (`en.json` and `pt-BR.json`, `stats.*`): `range.7|30|90|365`,
  `range.label` (`{first} – {last}`), `range.older`/`range.newer`
  (aria-labels), `this_range`, `previous_range`, `daily_average`, `change`
  (`{change} vs previous {n} days`, as a plural), `filter.activity`,
  `filter.task`, `filter.clear`, `chart.week_tip`, `chart.keys_hint`. Drop
  "last 30 days" from `stats.focus.by_activity`, `top_tasks`,
  `apps.most_apps`, `most_sites` and `blocks.most`. Update `stats.*.chart`
  to name the range rather than `{n}` days.

## Tests

- `tests/stats_cases.json`, shared by Python and JS (the `plan_cases.json`
  pattern):
  - `window`/`buckets`, including year → weeks with partial ends, and
    offsets across a month and a year boundary.
  - `niceTicks` (JS only, but the expected values live in the same file).
  - `change` (0 → None, negative changes, rounding).
- `tests/test_stats_common.py` (the cases above plus `clamp_offset`) and
  `tests/js/stats.test.mjs` (`loadUiFunction("niceTicks")` and a JS
  `weekBuckets` if the chart labels need one; otherwise Python buckets and
  JS only labels).
- `tests/test_focus_stats.py`:
  - span/offset: the right window; the streak unchanged by the offset; the
    running session only at offset 0.
  - the filter by activity, `_task`, `_other` and a task key.
  - previous-range totals.
- `tests/test_blocks.py`, `tests/test_app_limits.py`: `stats(today=…,
  span, offset)` on the `clock` fixture; `earliest`; row fields.
- `tests/test_config.py` covers `stats_span` automatically (DEFAULTS ↔
  Config). Also add an invalid-value case.
- Api: one test for `Api.stats` through `make_app`: the default span from
  the config, `has_older`, and an unknown filter refused with an `AppError`.
- `tests/test_i18n_python.py` / `test_i18n.py` catch missing keys.

## Status

- PR 1 (stats_common, today/span/offset): done.
- PR 2 (`Api.stats`, `stats_span`, picker, arrows, tiles and change): done. A year's bars are
  already weekly, and their tooltip names the week ("Mon, Oct 12 – Today"). PR 3 still owns
  the axes, the keyboard and `niceTicks`.

- Added after PR 2, on request: a 1-day period (`SPANS` now starts at 1; the picker starts on 7
  days) with two tiles and no charts, and the time on the PC outside focus in the Focus section
  (`pc_time.json`, `pc_time_on`, on by default).

- PR 3 (chart): done. Gridlines (`niceTicks`), dates under the bars (`axisMarks`, then dropped
  when they'd touch), keyboard and live region; 90 days stays daily with 1px gaps (`.dense`).
  Also: totals of 100h or more show whole hours ("503h") and long numbers are smaller, so a tile's
  number stays on one line in pt-BR.

## Order of work (one PR each, each green on ruff + pytest + node tests)

1. **`stats_common` + refactor, no visible change**: move the three modules
   onto it, add the `today/span/offset` parameters (defaults reproducing
   today's output) and the cases file. Existing tests stay as they are and
   prove nothing changed.
2. **`Api.stats` + `stats_span` + range picker + arrows + tiles/change**:
   the new payload, the UI header and the i18n keys; the old Api methods
   removed.
3. **Chart rewrite**: axes, week bars, keyboard, live region, `niceTicks`
   tests.
4. **Drill-down**: the `filter` in `focus_stats`, clickable rows, the chip.

Update CLAUDE.md as each part lands: `stats_common.py` in the module table,
the Focus stats paragraph (range/offset/filter, `Api.stats`), and the
mentions of "last 30 days".

## Open points to check while building

- History is capped at 5,000 entries (`HISTORY_KEEP`). A heavy user may
  have less than a year of it, so `earliest` comes from the data, not the
  span, and the 1-year view just starts emptier.
- A 90-day chart is 90 bars in the Settings dialog's width: check that
  columns still get ≥ 2 px. If not, bucket 90 days by week too.
- `AppTime.stats` caps the apps at `MAX_APPS` (30). Check that a year of
  data at 400 days keeps the call fast (it should: one dict per day).
