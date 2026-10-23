"""A thin client for Todoist's REST API (api/v1): tasks by filter query (with their subtasks), labels,
comments, close."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from functools import cache

from buzyist import i18n
from buzyist.constants import APP, TODOIST_API, AppError
from buzyist.i18n import app_error, english
from buzyist.plan import duration_minutes

log = logging.getLogger(APP)

DESCRIPTION_MAX = 2000  # characters of a task's description sent to the list
COMMENT_MAX = 2000  # characters of a comment sent to the list

# The comment finish() logs after a session (todoist.comment_bar/comment_local, every language and
# every version so far): a tomato, the multiplication sign, the count and "(". Not something a person wrote.
SESSION_LOG = re.compile(r"🍅 × \d+ \(")  # noqa: RUF001 - the multiplication sign is what the comment holds

TODOIST_COLORS = {
    "berry_red": "#b8256f",
    "red": "#db4035",
    "orange": "#ff9933",
    "yellow": "#fad000",
    "olive_green": "#afb83b",
    "lime_green": "#7ecc49",
    "green": "#299438",
    "mint_green": "#6accbc",
    "teal": "#158fad",
    "sky_blue": "#14aaf5",
    "light_blue": "#96c3eb",
    "blue": "#4073ff",
    "grape": "#884dff",
    "violet": "#af38eb",
    "lavender": "#eb96eb",
    "magenta": "#e05194",
    "salmon": "#ff8d85",
    "charcoal": "#808080",
    "grey": "#b8b8b8",
    "taupe": "#ccac93",
}


# ----------------------------------------------------------------- Todoist ---


class TodoistError(AppError):
    """Todoist answered with an HTTP error: its `status` and what it said (`detail`)."""

    status = 0
    detail = ""


class Todoist:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def call(self, method: str, path: str, params=None, body=None):
        token = self.cfg.get("todoist_token")
        if not token:
            raise app_error("errors.todoist.no_token")
        url = TODOIST_API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise app_error("errors.todoist.bad_token")
            detail = err.read().decode("utf-8", "replace")
            with contextlib.suppress(ValueError, AttributeError):
                detail = json.loads(detail).get("error") or detail
            detail = str(detail)[:200]
            answered = app_error("errors.todoist.answered", cls=TodoistError, status=err.code, detail=detail)
            answered.status, answered.detail = err.code, detail
            raise answered
        except OSError as err:
            raise app_error("errors.todoist.unreachable", error=getattr(err, "reason", err))
        return json.loads(raw) if raw else None

    def pages(self, path: str, params: dict) -> list[dict]:
        found, cursor = [], None
        while True:
            query = dict(params, limit=200)
            if cursor:
                query["cursor"] = cursor
            page = self.call("GET", path, query) or {}
            found += page.get("results", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return found

    def tasks(self, query: str, subtasks: bool = False) -> list[dict]:
        """The open tasks a filter query finds, sorted like the list shows them. With `subtasks`,
        each also carries its open subtasks at every depth (`subtasks`, in Todoist's order, each
        with its `depth` below the task), from one more query."""
        projects = {p["id"]: p for p in self.pages("/projects", {})}
        raw = [item for item in self.pages("/tasks/filter", {"query": query}) if is_open(item)]
        below = self.open_subtasks() if subtasks else []
        names = self.parent_names(raw, below)
        children: dict[str, list[dict]] = {}
        for item in below:
            children.setdefault(item.get("parent_id"), []).append(item)
        for kids in children.values():
            kids.sort(key=lambda item: item.get("child_order", 0))
        tasks = []
        for item in raw:
            task = self.shape(item, projects, names)
            if subtasks:
                task["subtasks"] = [
                    dict(self.shape(sub, projects, names), depth=depth)
                    for sub, depth in descendants(item["id"], children)
                ]
            tasks.append(task)
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        tasks.sort(key=lambda t: task_rank(t, now))
        return tasks

    @staticmethod
    def shape(item: dict, projects: dict, names: dict[str, str]) -> dict:
        """A Todoist task as the list (and a session) uses it."""
        day, clock = due_parts(item.get("due"))
        project = projects.get(item.get("project_id"), {})
        return {
            "id": item["id"],
            "content": plain_text(item.get("content", "")),
            "parent_id": item.get("parent_id") or "",
            "parent": names.get(item.get("parent_id"), ""),  # a subtask's parent name
            "description": plain_text(item.get("description", ""))[:DESCRIPTION_MAX],
            "priority": item.get("priority", 1),  # 4 is P1
            "labels": item.get("labels", []),
            "project": project.get("name", ""),
            "color": TODOIST_COLORS.get(project.get("color"), "#808080"),
            "inbox": bool(project.get("inbox_project")),
            "day": day,
            "time": clock,
            "overdue": bool(day and day < date.today().isoformat()),
            "order": item.get("day_order", 0),
            "minutes": duration_minutes(item.get("duration")),  # None: no duration, or one in days
            "url": f"https://app.todoist.com/app/task/{item['id']}",
        }

    def open_subtasks(self) -> list[dict]:
        """Every open subtask of the account (Todoist's `subtask` filter), or [] when that fails:
        the list then shows the tasks without their subtasks."""
        try:
            return [item for item in self.pages("/tasks/filter", {"query": "subtask"}) if is_open(item)]
        except AppError as err:
            log.warning("couldn't load the subtasks: %s", english(err))
            return []

    def open_children(self, task_id: str) -> list[dict]:
        """A task's open subtasks, one level down."""
        return [item for item in self.pages("/tasks", {"parent_id": task_id}) if is_open(item)]

    def parent_names(self, raw: list[dict], known: list[dict] = ()) -> dict[str, str]:
        """{parent id: its name} for the subtasks in `raw`. Parents neither the filter nor `known`
        brought are asked for in one call; a parent that is gone (completed, deleted) just isn't named."""
        names = {item["id"]: plain_text(item.get("content", "")) for item in [*raw, *known]}
        missing = {item.get("parent_id") for item in raw} - names.keys() - {None, ""}
        if missing:
            with contextlib.suppress(AppError):
                for item in self.pages("/tasks", {"ids": ",".join(sorted(missing))}):
                    names[item["id"]] = plain_text(item.get("content", ""))
        return names

    def set_label(self, task_id: str, label: str, present: bool) -> None:
        labels = list((self.call("GET", f"/tasks/{task_id}") or {}).get("labels", []))
        if (label in labels) == present:
            return
        labels = [*labels, label] if present else [x for x in labels if x != label]
        self.call("POST", f"/tasks/{task_id}", body={"labels": labels})

    def comment(self, task_id: str, text: str) -> None:
        self.call("POST", "/comments", body={"task_id": task_id, "content": text})

    def notes_since(self, sync_token: str) -> dict:
        """The comments on open tasks changed since `sync_token` ("*": all of them), from the Sync
        API in one request: {notes: [raw comments, deleted ones flagged], token, full}. (The REST
        tasks' `note_count` is always 0, so it can't tell which tasks have comments.)"""
        answer = self.call("POST", "/sync", body={"sync_token": sync_token, "resource_types": ["notes"]}) or {}
        return {
            "notes": [n for n in answer.get("notes", []) if isinstance(n, dict) and n.get("id")],
            "token": str(answer.get("sync_token") or sync_token),
            "full": bool(answer.get("full_sync")),
        }

    def comments(self, task_id: str) -> list[str]:
        """The text of every comment on a task, oldest first (raw, so Markdown is kept)."""
        if not task_id:
            return []
        return [c["content"] for c in self.pages("/comments", {"task_id": task_id}) if c.get("content")]

    def close(self, task_id: str) -> None:
        self.call("POST", f"/tasks/{task_id}/close")

    def copy_to_tomorrow(self, task_id: str, note: str = "") -> dict | None:
        """Create a followup copy of a task due tomorrow, copying its description, priority, labels,
        duration, comments and open subtasks (recursively). When `note` is given it is added as a
        comment on the original first, so the copy (which inherits every comment) carries the
        history of each carry-over. Returns the new top task."""
        item = self.call("GET", f"/tasks/{task_id}") or {}
        if note:
            self.comment(task_id, note)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return self._copy_task(item, due_date=tomorrow)

    def _copy_task(self, item: dict, parent_id: str = "", due_date: str = "") -> dict:
        """Create a copy of `item` (a raw Todoist task), then copy its comments and open subtasks
        under the copy. `parent_id` places it under an already-copied parent (a subtask inherits
        that parent's project); `due_date` overrides the due date (the top task, carried to tomorrow)."""
        body: dict = {"content": item.get("content", "")}
        for key in ("description", "priority", "labels"):
            if item.get(key):
                body[key] = item[key]
        duration = item.get("duration")
        if duration and duration.get("amount") and duration.get("unit"):
            body["duration"] = duration["amount"]
            body["duration_unit"] = duration["unit"]
        if parent_id:
            body["parent_id"] = parent_id  # a subtask inherits its parent's project and section
        else:
            for key in ("project_id", "section_id", "parent_id"):
                if item.get(key):
                    body[key] = item[key]
        body.update(_due_fields(item.get("due"), due_date))
        new = self.call("POST", "/tasks", body=body) or {}
        new_id = new.get("id")
        if new_id:
            for text in self.comments(item.get("id", "")):
                self.comment(new_id, text)
            for child in self.open_children(item.get("id", "")):
                self._copy_task(child, parent_id=new_id)
        return new


def is_open(item: dict) -> bool:
    return not item.get("checked") and not item.get("is_deleted")


def _due_fields(due: dict | None, override_date: str) -> dict:
    """The create-task due fields: `override_date` (YYYY-MM-DD) wins; otherwise keep the task's
    own due (its time if it has one), or nothing when it has no due date."""
    if override_date:
        return {"due_date": override_date}
    if not due:
        return {}
    if due.get("datetime"):
        return {"due_datetime": due["datetime"]}
    if due.get("date"):
        return {"due_date": due["date"]}
    return {}


def descendants(task_id: str, children: dict[str, list[dict]], depth: int = 1, seen=None):
    """(subtask, depth) for every subtask under `task_id`, depth first, in each level's order."""
    seen = set() if seen is None else seen
    for item in children.get(task_id, []):
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        yield item, depth
        yield from descendants(item["id"], children, depth + 1, seen)


def task_rank(task: dict, now: str) -> tuple:
    """Sort key for the task list. A task with a time still ahead of `now` ("YYYY-MM-DD HH:MM",
    local) waits at the end, earliest first, whatever its priority; once its time comes it sorts
    like the rest: priority, overdue, time, Todoist's order. Mirrored by taskRank in ui/main.js."""
    if task["time"] and f"{task['day']} {task['time']}" > now:
        return (1, task["day"], task["time"], task["order"])
    return (0, -task["priority"], not task["overdue"], task["time"] or "99", task["order"])


def due_parts(due):
    """(YYYY-MM-DD, HH:MM or None) in local time."""
    if not due:
        return None, None
    value = due.get("datetime") or due.get("date") or ""
    if "T" not in value:
        return value[:10] or None, None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo:
            moment = moment.astimezone()
        return moment.date().isoformat(), moment.strftime("%H:%M")
    except ValueError:
        return value[:10], value[11:16]


def shape_comment(item: dict) -> dict | None:
    """A raw comment as the list shows it, {id, text, at, file} (`at`: Todoist's posted_at, UTC;
    `file`: {name, url} of an attachment, or None), or None when nobody wrote it (`people_text`)."""
    if item.get("is_deleted"):
        return None
    text = people_text(str(item.get("content") or ""))[:COMMENT_MAX]
    attached = item.get("file_attachment")
    url = str(attached.get("file_url") or attached.get("url") or "") if isinstance(attached, dict) else ""
    file = {"name": str(attached.get("file_name") or attached.get("title") or url), "url": url} if url else None
    if not text and not file:
        return None
    return {"id": str(item.get("id", "")), "text": text, "at": str(item.get("posted_at") or ""), "file": file}


@cache
def carried_over_line() -> re.Pattern:
    """The line complete_task adds when it carries a task over (todoist.carried_over), in any
    shipped language, since the comment keeps the language it was written in. Whatever marks it
    ("> " now, an arrow in older notes) is taken too."""
    forms = {i18n.translate(lang, "todoist.carried_over", {"date": "\0"}) for lang in i18n.languages()}
    words = sorted({re.sub(r"^\W+", "", form) for form in forms})
    date_ = r"\d[\d/.-]*\d"  # todoist.carry_when: 2026-01-06, 06/01/2026
    return re.compile(r"\W*(?:" + "|".join(re.escape(w).replace("\0", date_) for w in words) + ")")


def people_text(content: str) -> str:
    """A comment as the list shows it: "" for buZyist's session log; a carry-over note without its
    "> Carried over to ..." line ("" when that was all); plain text."""
    if SESSION_LOG.match(content.strip()):
        return ""
    lines = [line for line in content.splitlines() if not carried_over_line().fullmatch(line.strip())]
    return plain_text("\n".join(lines))


def plain_text(content: str) -> str:
    """Todoist text can hold Markdown; keep the words."""
    content = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", content)
    content = re.sub(r"(\*\*|__|`|~~)", "", content)
    return content.strip()
