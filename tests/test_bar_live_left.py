"""
Bar._read: firmware that counts a running timer's time left down in its
snapshot (VeryBUSY r12, API 27.7) while keeping the stamp, against the
stock firmware whose snapshot holds the time left at its stamp.
"""

from busylib import types
from busylib.features import timer_state

from buzyist.bar import LOCAL_SETTINGS, Bar

WORK, REST = 25 * 60_000, 5 * 60_000


def interval(stamp: int, left: int = WORK, index: int = 0, paused: bool = False) -> types.BusySnapshot:
    return types.BusySnapshot(
        snapshot=types.BusySnapshotInterval(
            type="INTERVAL",
            card_id="card-busy",
            current_interval=index,
            current_interval_time_total_ms=WORK if index % 2 == 0 else REST,
            current_interval_time_left_ms=left,
            is_paused=paused,
            interval_settings=types.BusySnapshotIntervalSettings(
                type="INTERVAL",
                interval_work_ms=WORK,
                interval_rest_ms=REST,
                interval_work_cycles_count=4,
                is_autostart_enabled=True,
            ),
            busy_bar_settings=LOCAL_SETTINGS,
        ),
        snapshot_timestamp_ms=stamp,
    )


class StockBar:
    """Keeps the snapshot it was given: its time left is the one at the stamp."""

    def __init__(self, snap):
        self.snap = snap
        self.reads = 0

    def busy_snapshot(self):
        self.reads += 1
        return self.snap

    def busy_snapshot_set(self, snap):
        if snap.snapshot_timestamp_ms > self.snap.snapshot_timestamp_ms:
            self.snap = snap


class LiveBar(StockBar):
    """Answers with the time left now (whole seconds) and the interval it is in, under the same stamp."""

    def __init__(self, snap, clock):
        super().__init__(snap)
        self.clock = clock

    def busy_snapshot(self):
        self.reads += 1
        variant = self.snap.snapshot
        if variant.is_paused:
            return self.snap
        state = timer_state(self.snap, now_ms=self.clock.ms)
        left = -(-state.time_left_ms // 1000) * 1000
        return self.snap.model_copy(
            update={
                "snapshot": variant.model_copy(
                    update={"current_interval": state.interval, "current_interval_time_left_ms": left}
                )
            }
        )


def new_bar() -> Bar:
    return Bar({"use_busybar": True, "busybar_ip": "10.0.0.9", "busybar_pin": "", "usb_fallback": False})


def test_a_live_count_is_read_as_the_time_left_now(clock):
    device = LiveBar(interval(clock.ms), clock)
    bar = new_bar()
    clock.advance(seconds=600)
    state = timer_state(bar._read(device))
    assert bar.live_left is True
    assert device.reads == 2  # it looked twice to find out
    assert abs(state.time_left_ms - 15 * 60_000) <= 1000  # not 5 minutes: the 10 aren't taken off twice
    # Known now: one read each time, and the time keeps going at the bar's pace.
    clock.advance(seconds=60)
    assert abs(timer_state(bar._read(device)).time_left_ms - 14 * 60_000) <= 1000
    assert device.reads == 3


def test_a_stock_snapshot_is_left_as_it_is(clock):
    device = StockBar(interval(clock.ms))
    bar = new_bar()
    clock.advance(seconds=600)
    snap = bar._read(device)
    assert bar.live_left is False
    assert snap == device.snap
    assert timer_state(snap).time_left_ms == 15 * 60_000 - 1500  # the second look took 1.5 s


def test_a_fresh_stamp_needs_no_second_look(clock):
    device = LiveBar(interval(clock.ms), clock)
    bar = new_bar()
    clock.advance(seconds=1)
    bar._read(device)
    assert device.reads == 1 and bar.live_left is None
    clock.advance(seconds=2)  # the next poll under the same stamp tells
    assert abs(timer_state(bar._read(device)).time_left_ms - (WORK - 3000)) <= 1000
    assert bar.live_left is True


def test_a_live_count_into_the_break(clock):
    device = LiveBar(interval(clock.ms), clock)
    bar = new_bar()
    clock.advance(minutes=27)
    state = timer_state(bar._read(device))
    assert state.phase == "rest" and state.interval == 1
    assert abs(state.time_left_ms - 3 * 60_000) <= 1000


def test_a_paused_timer_is_not_touched(clock):
    device = LiveBar(interval(clock.ms - 600_000, left=60_000, paused=True), clock)
    bar = new_bar()
    bar.live_left = True
    assert bar._read(device) == device.snap


def test_pausing_a_live_bar_freezes_what_it_shows(clock, monkeypatch):
    device = LiveBar(interval(clock.ms), clock)
    bar = new_bar()
    monkeypatch.setattr(bar, "run", lambda fn, device_=None, **kw: fn(device))
    clock.advance(seconds=600)
    bar.set_paused(True)
    written = device.snap.snapshot
    assert written.is_paused
    assert abs(written.current_interval_time_left_ms - 15 * 60_000) <= 1000


def test_reset_forgets_what_it_learned(clock):
    device = LiveBar(interval(clock.ms), clock)
    bar = new_bar()
    clock.advance(seconds=600)
    bar._read(device)
    bar.reset()
    assert bar.live_left is None
