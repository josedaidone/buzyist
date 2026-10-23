"""
"Complete & carry over": complete_task(followup=True) copies the task to tomorrow
(Todoist.copy_to_tomorrow) before closing it, adding a comment on the original to
record the carry-over; Todoist builds the copy due the next day, keeping the task's
project, priority, labels, duration, comments and open subtasks (recursively).
"""

from datetime import date, timedelta

from buzyist.todoist import Todoist


def test_followup_copies_to_tomorrow_then_closes(make_app):
    app = make_app()
    app.todoist.add_task("t1", "Write the tests")
    app.task_list()
    result = app.complete_task("t1", followup=True)
    assert result == {"followup": {"content": "Write the tests"}}
    assert app.todoist.followups == ["t1"]
    assert app.todoist.closed == ["t1"]
    # the carry-over note is recorded as a comment on the task being completed
    assert any(tid == "t1" and "Carried over" in text for tid, text in app.todoist.comments)
    # the copy happens before the close
    assert app.todoist.names().index("copy_to_tomorrow") < app.todoist.names().index("close")


def test_the_optional_note_is_merged_into_the_carry_over_comment(make_app):
    app = make_app()
    app.todoist.add_task("t1", "Write the tests")
    app.task_list()
    app.complete_task("t1", followup=True, comment="  pick up where I left off  ")
    # one comment: the user's note (trimmed) above the quoted carry-over line
    note = next(text for tid, text in app.todoist.comments if tid == "t1")
    assert note.startswith("pick up where I left off\n\n> Carried over to ")
    # it is the carry-over copy's note, not a second comment call
    assert "comment" not in app.todoist.names()
    assert app.todoist.names().index("copy_to_tomorrow") < app.todoist.names().index("close")


def test_a_blank_note_leaves_only_the_carry_over_line(make_app):
    app = make_app()
    app.todoist.add_task("t1", "Write the tests")
    app.task_list()
    app.complete_task("t1", followup=True, comment="   ")
    note = next(text for tid, text in app.todoist.comments if tid == "t1")
    assert note.startswith("> Carried over to ")
    assert "comment" not in app.todoist.names()


def test_without_followup_nothing_is_copied(make_app):
    app = make_app()
    app.todoist.add_task("t1", "Write the tests")
    app.task_list()
    assert app.complete_task("t1") == {}
    assert app.todoist.followups == []
    assert app.todoist.closed == ["t1"]


def fake_client(tasks, comments, children):
    """A real Todoist whose HTTP is faked: GET a task, POST a task/comment, and `pages`
    for comments and subtasks. Records created tasks and posted comments."""
    created, posted, counter = [], [], [0]

    def call(method, path, params=None, body=None):
        if method == "GET" and path.startswith("/tasks/"):
            return tasks[path.rsplit("/", 1)[-1]]
        if method == "POST" and path == "/tasks":
            counter[0] += 1
            new_id = f"new{counter[0]}"
            created.append((new_id, body))
            return {"id": new_id, "content": body["content"]}
        if method == "POST" and path == "/comments":
            posted.append((body["task_id"], body["content"]))
            return {}
        raise AssertionError((method, path))

    def pages(path, params):
        if path == "/comments":
            return [{"content": c} for c in comments.get(params["task_id"], [])]
        if path == "/tasks":  # open_children(parent_id)
            return list(children.get(params["parent_id"], []))
        raise AssertionError((path, params))

    todoist = Todoist({"todoist_token": "x"})
    todoist.call, todoist.pages = call, pages
    return todoist, created, posted


def test_copy_builds_a_due_tomorrow_task_with_everything():
    tasks = {
        "t1": {
            "id": "t1",
            "content": "Parent",
            "description": "details",
            "priority": 3,
            "project_id": "p1",
            "section_id": "s1",
            "labels": ["focus"],
            "duration": {"amount": 50, "unit": "minute"},
            "due": None,
        },
    }
    todoist, created, posted = fake_client(tasks, {"t1": ["c1", "c2"]}, {})
    new = todoist.copy_to_tomorrow("t1", "Carried over")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert new["id"] == "new1"
    # the note is added to the original, then the copy carries every comment
    assert posted[0] == ("t1", "Carried over")
    assert [(t, c) for t, c in posted if t == "new1"] == [("new1", "c1"), ("new1", "c2")]
    assert created == [
        (
            "new1",
            {
                "content": "Parent",
                "description": "details",
                "priority": 3,
                "labels": ["focus"],
                "duration": 50,
                "duration_unit": "minute",
                "project_id": "p1",
                "section_id": "s1",
                "due_date": tomorrow,
            },
        )
    ]


def test_copy_recreates_subtasks_recursively():
    tasks = {
        "t1": {"id": "t1", "content": "Parent", "due": None},
        "a": {"id": "a", "content": "Child", "due": {"date": "2020-01-01"}},
        "b": {"id": "b", "content": "Grandchild", "due": {"datetime": "2020-01-02T09:00:00Z"}},
    }
    children = {"t1": [tasks["a"]], "a": [tasks["b"]], "b": []}
    comments = {"t1": [], "a": ["note a"], "b": []}
    todoist, created, posted = fake_client(tasks, comments, children)
    todoist.copy_to_tomorrow("t1")
    bodies = {body["content"]: body for _, body in created}
    assert set(bodies) == {"Parent", "Child", "Grandchild"}
    # the subtasks hang under the freshly made parents and keep their own due dates
    assert "parent_id" not in bodies["Parent"]
    assert bodies["Child"]["parent_id"] == "new1"  # under the copied parent
    assert bodies["Child"]["due_date"] == "2020-01-01"
    assert bodies["Grandchild"]["parent_id"] == "new2"  # under the copied child
    assert bodies["Grandchild"]["due_datetime"] == "2020-01-02T09:00:00Z"
    assert ("new2", "note a") in posted  # the child's comment went to its copy
