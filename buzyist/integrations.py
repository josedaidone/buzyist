"""
The seams between the app and its two core integrations: where tasks come
from (Todoist) and what runs the timer (the BUSY Bar, or this PC's clock).
The session code reaches them only through what these protocols name, so a
part of them that another source lacks is a capability it checks first
(App.can). The registry is what Settings' Integrations tab is built from.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from busylib import types

# What a task source can do beyond listing and closing tasks.
SUBTASKS = "subtasks"  # tasks carry their open subtasks; open_children answers
LABELS = "labels"  # set_label adds and removes a label (focus_label)
COMMENTS = "comments"  # comment/comments: the log written when a session ends
CARRY_OVER = "carry_over"  # copy_to_tomorrow
DURATIONS = "durations"  # tasks carry `minutes`, which plan_session turns into tomatoes


@runtime_checkable
class SnapshotStore(Protocol):
    """
    Where Bar reads and writes the timer's snapshot: busylib's BusyBar client
    or the LocalClock that stands in for it (no bar, or the bar out of reach).
    A new bar call used inside Bar needs a LocalClock counterpart.
    """

    def busy_snapshot(self) -> types.BusySnapshot: ...

    def busy_snapshot_set(self, snap: types.BusySnapshot) -> None: ...

    def busy_profile(self, slot: str): ...


@runtime_checkable
class TimerSource(Protocol):
    """
    What the session engine (session.py, App's engine, go_standalone and
    state, app_sync.py) uses of App.bar. The BUSY Bar's own features (themes,
    put_theme/set_theme/card_theme, draw_picture, play_sound, apps,
    running_app/open_app/press_ok, power, run(device=True)) aren't part of
    it: bar_display.py, App.beep and Api use them only while use_busybar is on.
    """

    lock: threading.RLock
    via: str | None
    offline: bool

    @property
    def is_local(self) -> bool: ...

    @property
    def standalone(self) -> bool: ...

    def snapshot(self) -> types.BusySnapshot: ...

    def poll(self) -> types.BusySnapshot: ...

    def start(self, work_ms: int, rest_ms: int, cycles: int, autostart: bool) -> int: ...

    def start_open(self) -> int: ...

    def stop(self) -> None: ...

    def set_paused(self, paused: bool) -> None: ...

    def next_phase(self) -> None: ...

    def add_time(self, ms: int) -> int: ...

    def go_offline(self, since: int | None = None) -> bool: ...

    def reset(self) -> None: ...


@runtime_checkable
class TaskSource(Protocol):
    """What the app uses of App.todoist; `capabilities` says which optional parts it has."""

    capabilities: frozenset[str]

    def tasks(self, query: str, subtasks: bool = False) -> list[dict]: ...

    def close(self, task_id: str) -> None: ...

    def comment(self, task_id: str, text: str) -> None: ...

    def comments(self, task_id: str) -> list[str]: ...

    def set_label(self, task_id: str, label: str, present: bool) -> None: ...

    def open_children(self, task_id: str) -> list[dict]: ...

    def copy_to_tomorrow(self, task_id: str, note: str = "") -> dict | None: ...


@dataclass(frozen=True)
class Integration:
    id: str
    kind: str  # "tasks" (a TaskSource) or "timer" (a TimerSource)
    setting: str  # the switch in DEFAULTS that turns it on
    tab: str  # its Settings tab


INTEGRATIONS = (
    Integration("todoist", "tasks", "use_todoist", "todoist"),
    Integration("busybar", "timer", "use_busybar", "timer"),
)


def integrations_view() -> list[dict]:
    """The registry as get_settings() hands it to the page, in order."""
    return [{"id": i.id, "kind": i.kind, "setting": i.setting, "tab": i.tab} for i in INTEGRATIONS]
