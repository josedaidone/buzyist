"""
Task comments in the list: shape_comment keeps what people wrote (not buZyist's session
logs, not its carried-over line), and App.task_comments keeps every comment up to date with
one Sync API request (Todoist.notes_since) and hands out those of the tasks asked for.
"""

import pytest

from buzyist.constants import AppError
from buzyist.i18n import translate
from buzyist.todoist import Todoist, people_text, shape_comment


def test_the_session_log_is_not_a_person_s_comment():
    for lang in ("en", "pt-BR"):
        for key in ("todoist.comment_bar", "todoist.comment_local"):
            log = translate(lang, key, {"n": 3, "minutes": 75, "when": "2026-01-05 10:00"})
            assert people_text(log) == ""


def test_a_comment_that_starts_with_a_tomato_is_kept():
    assert people_text("🍅 sauce recipe in the doc") == "🍅 sauce recipe in the doc"


@pytest.mark.parametrize("lang", ["en", "pt-BR"])
def test_the_carried_over_line_is_cut(lang):
    line = translate(lang, "todoist.carried_over", {"date": "2026-01-06"})
    assert people_text(line) == ""
    assert people_text(f"pick up where I left off\n\n{line}") == "pick up where I left off"


def test_an_older_carried_over_line_with_an_arrow_is_cut():
    assert people_text("\u27a1\ufe0f Carried over to 2026-10-22") == ""
    assert people_text("Carried over to the team") == "Carried over to the team"  # not a date


def test_markdown_becomes_plain_text():
    assert people_text("see **this** [doc](https://example.com)") == "see this doc"


def test_comments_are_shaped_for_the_list():
    raw = [
        {"id": "c1", "content": "Ask Ana first", "posted_at": "2026-01-05T10:00:00.000000Z"},
        {"id": "c2", "content": "🍅 × 2 (50 min focus), ended 2026-01-05 11:00"},  # noqa: RUF001 - the session log's sign
        {"id": "c3", "content": "gone", "is_deleted": True},
        {
            "id": "c4",
            "content": "",
            "posted_at": "2026-01-05T12:00:00Z",
            "file_attachment": {"file_name": "plan.pdf", "file_url": "https://files.todoist.com/plan.pdf"},
        },
    ]
    assert [shape_comment(c) for c in raw] == [
        {"id": "c1", "text": "Ask Ana first", "at": "2026-01-05T10:00:00.000000Z", "file": None},
        None,
        None,
        {
            "id": "c4",
            "text": "",
            "at": "2026-01-05T12:00:00Z",
            "file": {"name": "plan.pdf", "url": "https://files.todoist.com/plan.pdf"},
        },
    ]


def test_notes_since_asks_the_sync_api():
    todoist = Todoist({"todoist_token": "x"})
    sent = []

    def call(method, path, params=None, body=None):
        sent.append((method, path, body))
        return {"notes": [{"id": "c1"}, {"no": "id"}], "sync_token": "abc", "full_sync": True}

    todoist.call = call
    assert todoist.notes_since("*") == {"notes": [{"id": "c1"}], "token": "abc", "full": True}
    assert sent == [("POST", "/sync", {"sync_token": "*", "resource_types": ["notes"]})]


def note(id, task, text, at="2026-01-05T10:00:00Z", **more):
    return {"id": id, "item_id": task, "content": text, "posted_at": at, **more}


def comment(id, text, at="2026-01-05T10:00:00Z"):
    return {"id": id, "text": text, "at": at, "file": None}


def test_the_tasks_asked_for_get_what_people_wrote_oldest_first(make_app):
    app = make_app()
    app.todoist.syncs.append(
        {
            "notes": [
                note("c2", "t1", "then this", at="2026-01-06T10:00:00Z"),
                note("c1", "t1", "first this"),
                note("c3", "t1", "🍅 × 1 (25 min focus), ended 2026-01-05 11:00"),  # noqa: RUF001 - the session log's sign
                note("c4", "t2", "another task"),
            ],
            "token": "s1",
            "full": True,
        }
    )
    assert app.task_comments(["t1", "t9"]) == {
        "comments": {"t1": [comment("c1", "first this"), comment("c2", "then this", "2026-01-06T10:00:00Z")]}
    }


def test_later_calls_ask_only_for_what_changed(make_app):
    app = make_app()
    app.todoist.syncs += [
        {"notes": [note("c1", "t1", "old"), note("c2", "t1", "bye")], "token": "s1", "full": True},
        {"notes": [note("c1", "t1", "edited"), note("c2", "t1", "bye", is_deleted=True)], "token": "s2", "full": False},
    ]
    app.task_comments(["t1"])
    assert app.task_comments(["t1"]) == {"comments": {"t1": [comment("c1", "edited")]}}
    assert [args for name, args in app.todoist.calls if name == "notes_since"] == [("*",), ("s1",)]


def test_a_full_sync_starts_over(make_app):
    app = make_app()
    app.todoist.syncs += [
        {"notes": [note("c1", "t1", "gone since")], "token": "s1", "full": True},
        {"notes": [note("c2", "t1", "still here")], "token": "s2", "full": True},
    ]
    app.task_comments(["t1"])
    assert app.task_comments(["t1"]) == {"comments": {"t1": [comment("c2", "still here")]}}


def test_another_account_starts_over(make_app):
    app = make_app()
    app.todoist.syncs.append({"notes": [note("c1", "t1", "mine")], "token": "s1", "full": True})
    app.task_comments(["t1"])
    with app.cfg.edit() as c:
        c["todoist_token"] = "someone-else"  # noqa: S105 - a fake token, for a second account
    assert app.task_comments(["t1"]) == {"comments": {}}
    assert [args for name, args in app.todoist.calls if name == "notes_since"][-1] == ("*",)


def test_a_failed_refresh_answers_what_was_known(make_app):
    app = make_app()
    app.todoist.syncs.append({"notes": [note("c1", "t1", "hi")], "token": "s1", "full": True})
    app.task_comments(["t1"])
    app.todoist.errors["notes_since"] = AppError("boom")
    assert app.task_comments(["t1"]) == {"comments": {"t1": [comment("c1", "hi")]}}


def test_a_first_sync_that_fails_is_an_error(make_app):
    app = make_app()
    app.todoist.errors["notes_since"] = AppError("boom")
    with pytest.raises(AppError):
        app.task_comments(["t1"])
