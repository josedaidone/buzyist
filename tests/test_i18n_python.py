"""
What Python says in the app language (#27): errors, toasts, the tray, and the
text written once (the Todoist comment, an unnamed session's name, the default
activities' names), which keeps the language it was written in.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from buzyist import activities, i18n, paths
from buzyist.config import Config
from buzyist.constants import AppError
from buzyist.i18n import app_error, english
from buzyist.tray import tray_title

PT = {"language": "pt-BR"}


def finish_one(app, clock, deferred_threads, task_id=None, name=""):
    """Start a 1-pomodoro session and run it to its end (local clock)."""
    if task_id is not None:
        app.task_list(tasks=[app.todoist.add_task(task_id, name)])
    app.start_task(task_id, name)
    deferred_threads.run_pending()
    app.engine_step()
    clock.advance(minutes=25, seconds=1)
    app.engine_step()
    deferred_threads.run_pending()


def toasts_of(app) -> list:
    toasts = []
    app.notify = lambda title, message, quiet=False: toasts.append((title, message))
    return toasts


# ---------------------------------------------------------- written once ---


def test_a_session_finished_in_portuguese_comments_in_portuguese(make_app, clock, deferred_threads):
    app = make_app({**PT, "cycles": 1, "work_minutes": 25})
    toasts = toasts_of(app)
    finish_one(app, clock, deferred_threads, "t1", "Escrever os testes")
    ((task_id, text),) = app.todoist.comments
    assert task_id == "t1"
    assert text == f"🍅 × 1 (25 min de foco), encerrada em {clock.now():%d/%m/%Y %H:%M}"  # noqa: RUF001
    assert toasts[-1] == ("Que foco!", "Sessão concluída: 1 pomodoro em Escrever os testes.")


def test_the_comment_takes_the_language_of_the_moment_it_is_written(make_app, clock, deferred_threads):
    app = make_app({**PT, "cycles": 1, "work_minutes": 25})
    app.task_list(tasks=[app.todoist.add_task("t1", "Write the tests")])
    app.start_task("t1", "")
    deferred_threads.run_pending()
    app.save_settings({"language": "en"})  # switched while the session runs
    deferred_threads.run_pending()
    clock.advance(minutes=25, seconds=1)
    app.engine_step()
    deferred_threads.run_pending()
    assert app.todoist.comments[0][1].startswith("🍅 × 1 (25 min focus), ended ")  # noqa: RUF001


def test_an_unnamed_session_in_portuguese_is_a_sessao_de_foco(make_app, clock, deferred_threads):
    app = make_app({**PT, "use_todoist": False, "cycles": 1})
    finish_one(app, clock, deferred_threads)
    assert app.history[-1]["content"] == "Sessão de foco"
    assert app.recent_free() == []  # the fallback name isn't offered back, in any language
    app.save_settings({"language": "en"})
    assert app.history[-1]["content"] == "Sessão de foco"  # never rewritten
    assert json.loads(paths.HISTORY_PATH.read_text(encoding="utf-8"))[-1]["content"] == "Sessão de foco"


def test_a_fresh_portuguese_config_gets_portuguese_default_activities(make_app):
    app = make_app(PT)  # no activities saved yet
    assert [(a["id"], a["name"]) for a in app.cfg["activities"]] == [
        ("email", "E-mail"),
        ("messages", "Mensagens"),
        ("call", "Chamada"),
        ("programming", "Programação"),
        ("reading", "Leitura"),
    ]
    english_ones = activities.DEFAULT_ACTIVITIES
    assert [a["id"] for a in app.cfg["activities"]] == [a["id"] for a in english_ones]
    assert [{k: v for k, v in a.items() if k != "name"} for a in app.cfg["activities"]] == [
        {k: v for k, v in a.items() if k != "name"} for a in english_ones
    ]


def test_saved_activities_keep_their_names_whatever_the_language(make_app):
    app = make_app({"activities": activities.DEFAULT_ACTIVITIES})
    app.save_settings({"language": "pt-BR"})
    assert [a["name"] for a in app.cfg["activities"]] == ["Email", "Messages", "Call", "Programming", "Reading"]
    assert make_app(PT | {"activities": activities.DEFAULT_ACTIVITIES}).cfg["activities"][0]["name"] == "Email"


def test_the_english_catalog_names_the_defaults_as_the_code_does():
    assert activities.default_activities("en") == activities.DEFAULT_ACTIVITIES
    assert activities.default_activities(None) == activities.DEFAULT_ACTIVITIES
    assert activities.default_activities("xx") == activities.DEFAULT_ACTIVITIES


# ------------------------------------------------------------- the tray ---


class FakeTray(SimpleNamespace):
    """What the app touches on a pystray.Icon."""

    def update_menu(self):
        self.updates += 1


def menu_texts(menu) -> list[str]:
    return [item.text for item in menu.items if item.text]


def test_changing_the_language_rebuilds_the_tray_at_once(make_app, deferred_threads):
    app = make_app()
    app.tray = FakeTray(menu=app.tray_menu(), icon=None, title="", updates=0)
    old = app.tray.menu
    assert "Quit" in menu_texts(old)
    app.save_settings({"language": "pt-BR"})
    assert app.tray.menu is not old
    texts = menu_texts(app.tray.menu)
    assert texts[0] == "Abrir o buZyist"
    assert "Sair" in texts and "Nenhuma tarefa em foco" in texts
    assert app.tray.title == "buZyist · escolha uma tarefa"


def test_the_tray_tooltip_says_what_runs_in_the_app_language(make_app):
    make_app(PT)
    view = {"phase": "work", "paused": False, "minutes": 12, "round": 2, "rounds": 4}
    assert tray_title(view, "Relatório") == "Foco · faltam 12 min · pomodoro 2/4\nRelatório"
    assert tray_title(dict(view, paused=True), None) == "Pausado · faltam 12 min · pomodoro 2/4"
    assert tray_title(dict(view, phase="rest"), None) == "Intervalo · faltam 12 min · pomodoro 2/4"
    assert tray_title(dict(view, phase="call"), None) == "Em chamada · 12 min"


# --------------------------------------------------------- errors, logs ---


def test_errors_are_in_the_app_language_and_english_for_the_log(make_app):
    app = make_app(PT)
    with pytest.raises(AppError) as caught:
        app.save_filter(None, " ", "today")
    assert str(caught.value) == "Dê um nome ao filtro."
    assert caught.value.english == "Give the filter a name."
    with pytest.raises(AppError, match="^Um pomodoro dura de"):
        app.save_settings({"work_minutes": 0})


def test_settings_field_errors_follow_the_language_of_the_moment(monkeypatch):
    monkeypatch.setitem(i18n._source, "config", {"language": "pt-BR"})
    with pytest.raises(AppError, match="^Um intervalo dura de"):
        Config.check({"rest_minutes": 0})
    with pytest.raises(AppError, match="^“nada” não é um site"):
        Config.check({"blocked_sites": ["nada"]})
    monkeypatch.setitem(i18n._source, "config", {"language": "en"})
    with pytest.raises(AppError, match="^Breaks run"):
        Config.check({"rest_minutes": 0})


def test_a_nested_error_keeps_english_for_the_log(monkeypatch):
    monkeypatch.setitem(i18n._source, "config", {"language": "pt-BR"})
    inner = app_error("errors.apps.type_name")
    outer = app_error("errors.named", name="E-mail", error=inner)
    assert str(outer) == "E-mail: Digite o nome do programa, como Slack.exe."
    assert outer.english == "E-mail: Type the program name, like Slack.exe."
    assert english(outer) == outer.english
    assert english(ValueError("plain")) == "plain"


def test_the_log_stays_english_in_portuguese(make_app, caplog):
    app = make_app(PT)
    app.tasks["s1"] = {"id": "s1", "content": "Parte 1", "parent_id": "p1", "parent": "Relatório"}
    app.todoist.errors["open_children"] = app_error("errors.todoist.unreachable", error="timeout")
    with caplog.at_level(logging.WARNING, logger="Busyist"):
        assert app.complete_task("s1", offer_parent=True) == {}
    assert "couldn't check the parent's other subtasks: Can't reach Todoist (timeout)." in caplog.text
    assert "Não foi possível" not in caplog.text


def test_toasts_in_portuguese(make_app, clock, deferred_threads):
    app = make_app({**PT, "use_todoist": False, "cycles": 2, "work_minutes": 25, "rest_minutes": 5})
    toasts = toasts_of(app)
    app.start_task(None, "Relatório")
    app.engine_step()
    clock.advance(minutes=25, seconds=1)
    app.engine_step()
    assert toasts[-1] == ("Mandou bem! Hora do intervalo", "Pomodoro 1 de 2 concluído. Aproveite os 5 min de intervalo.")


# ----------------------------------------------- every key is in the catalog ---

# Keys made at run time from a prefix and a known set of endings.
DYNAMIC = {
    "defaults.activities.": [a["id"] for a in activities.DEFAULT_ACTIVITIES],
    "errors.bar_screens.name_": ["focus", "call", "done"],
    "notify.limit.this_": ["minute", "hour", "day", "week"],
    "notify.limit.stretch_": ["minute", "hour", "day", "week"],
    "notify.weekdays.": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "tray.title_": ["idle", "call", "focus", "break", "paused"],
}
AREAS = ("errors", "notify", "tray", "defaults", "todoist")


def test_every_key_the_python_code_uses_is_in_the_catalogs():
    source = "\n".join(p.read_text(encoding="utf-8") for p in Path(activities.__file__).parent.rglob("*.py"))
    keys = set(re.findall(r'"((?:%s)\.[a-z_.]+)"' % "|".join(AREAS), source))
    for found in re.findall(r'f"((?:%s)\.[a-z_.]+)\{' % "|".join(AREAS), source):
        assert found in DYNAMIC, found
        keys |= {found + end for end in DYNAMIC[found]}
    keys = {k for k in keys if not k.endswith(".")}
    assert len(keys) > 150
    for code in ("en", "pt-BR"):
        missing = sorted(k for k in keys if k not in i18n.catalog(code))
        assert not missing, (code, missing)
