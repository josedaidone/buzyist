"""
The seams to the core integrations (integrations.py): the protocols Bar,
LocalClock, busylib's BusyBar and Todoist fill, the registry get_settings
hands the page, and App.can.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from busylib import BusyBar

from buzyist import session
from buzyist.api import Api
from buzyist.bar import Bar, LocalClock
from buzyist.config import DEFAULTS
from buzyist.integrations import (
    CARRY_OVER,
    COMMENTS,
    DURATIONS,
    INTEGRATIONS,
    LABELS,
    SUBTASKS,
    SnapshotStore,
    TaskSource,
    TimerSource,
)
from buzyist.todoist import Todoist
from conftest import FakeTodoist

EVERY = {SUBTASKS, LABELS, COMMENTS, CARRY_OVER, DURATIONS}


def members(protocol) -> set[str]:
    """What a protocol names: its methods, properties and annotated attributes."""
    names = set(protocol.__annotations__)
    names |= {
        n for n, v in vars(protocol).items() if not n.startswith("_") and (callable(v) or isinstance(v, property))
    }
    return names


def test_the_bar_is_a_timer_source():
    bar = Bar({"use_busybar": False})
    assert isinstance(bar, TimerSource)


def test_the_local_clock_and_busylib_s_client_are_snapshot_stores():
    assert isinstance(LocalClock(), SnapshotStore)
    assert issubclass(BusyBar, SnapshotStore)  # no instance: it would reach for a bar


def test_todoist_and_the_fake_are_task_sources_with_every_capability():
    for source in (Todoist({}), FakeTodoist({})):
        assert isinstance(source, TaskSource)
        assert source.capabilities == EVERY


def test_every_integration_has_a_setting():
    for integration in INTEGRATIONS:
        assert integration.setting in DEFAULTS


def test_get_settings_carries_the_registry(app):
    assert Api(app).get_settings()["integrations"] == [
        {"id": "todoist", "kind": "tasks", "setting": "use_todoist", "tab": "todoist"},
        {"id": "busybar", "kind": "timer", "setting": "use_busybar", "tab": "timer"},
    ]


def test_can_needs_todoist_on(app):
    assert app.can(LABELS)
    with app.cfg.edit() as c:
        c["use_todoist"] = False
    assert not app.can(LABELS)


def test_a_source_without_labels_gets_no_label(app, deferred_threads):
    app.todoist.capabilities = frozenset(EVERY - {LABELS})
    app.task_list(tasks=[app.todoist.add_task("t1", "Write the tests")])
    app.start_task("t1")
    deferred_threads.run_pending()
    assert app.session["label_added"] is False
    assert "set_label" not in app.todoist.names()


def test_the_session_code_uses_only_what_timer_source_names():
    """Every self.bar.<name> in session.py is in TimerSource, so the protocol can't drift."""
    tree = ast.parse(inspect.getsource(session))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "bar"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    }
    assert used
    assert used <= members(TimerSource)


def test_the_app_uses_only_what_task_source_names():
    """Every self.todoist.<name> / _app.todoist.<name> in the package is in TaskSource."""
    used = set()
    for path in Path(session.__file__).parent.rglob("*.py"):
        used |= set(re.findall(r"(?:self|_app)\.todoist\.(\w+)", path.read_text(encoding="utf-8")))
    assert used
    assert used <= members(TaskSource)
