"""
The activities offered by the "What are you working on?" field (a session
with no Todoist task): `{id, name, icon}` each, edited in Settings, General.
The id stays when an activity is renamed, so the Stats tab keeps adding up
under it; the history also keeps the name it had, for one since removed.

An activity can also change what a work phase on it blocks (`apps`,
`sites`), on top of the global settings: see app_blocking (blocker.py) and
site_blocking (site_rules.py).
"""

from __future__ import annotations

import copy
import re

from buzyist.bar_screens import clean_activity_screen
from buzyist.blocker import MAX_BLOCKED_APPS, PROTECTED_EXES, app_key
from buzyist.constants import AppError
from buzyist.i18n import ENGLISH, app_error, languages, t, translate
from buzyist.site_rules import clean_blocked_sites

# The icons an activity can have: `#a-<icon>` symbols in ui/main.html, and
# ACTIVITY_ICONS in ui/main.js (a test checks they match).
ACTIVITY_ICONS = (
    "mail",
    "chat",
    "phone",
    "code",
    "book",
    "pen",
    "people",
    "paint",
    "bulb",
    "wrench",
    "chart",
    "dot",
)
MAX_ACTIVITIES = 12
MAX_ACTIVITY_NAME = 30  # mirrored by maxlength in ui/main.js
MAX_DETAIL = 80  # the free field's maxlength, also the session name's limit

# The defaults with their English names; default_activities() gives them in
# a language (defaults.activities.<id> in the catalogs), when they're created.
DEFAULT_ACTIVITIES = [
    {
        "id": "email",
        "name": "Email",
        "icon": "mail",
        "apps": {"state": "inherit", "allow": ["OUTLOOK.EXE", "olk.exe"], "block": []},
        "sites": {
            "state": "inherit",
            "mode": "",
            "allow": ["mail.google.com", "outlook.office.com", "outlook.office365.com", "outlook.live.com"],
            "block": [],
        },
    },
    {
        "id": "messages",
        "name": "Messages",
        "icon": "chat",
        "apps": {
            "state": "inherit",
            "allow": ["WhatsApp.Root.exe", "WhatsApp.exe", "Slack.exe", "ms-teams.exe", "Teams.exe"],
            "block": [],
        },
        "sites": {
            "state": "inherit",
            "mode": "",
            "allow": ["web.whatsapp.com", "slack.com", "teams.microsoft.com", "teams.live.com"],
            "block": [],
        },
    },
    {
        "id": "call",
        "name": "Call",
        "icon": "phone",
        "apps": {"state": "off", "allow": [], "block": []},
        "sites": {"state": "off", "mode": "", "allow": [], "block": []},
    },
    {"id": "programming", "name": "Programming", "icon": "code"},
    {"id": "reading", "name": "Reading", "icon": "book"},
]



def default_activities(lang: str | None = None) -> list[dict]:
    """
    The default activities named in `lang` (English when None or not shipped),
    with the same fixed ids: for a config that has none yet. Activities once
    saved keep the names they were given.
    """
    out = copy.deepcopy(DEFAULT_ACTIVITIES)
    for item in out:
        item["name"] = translate(str(lang or ENGLISH), f"defaults.activities.{item['id']}")
    return out


_ID = re.compile(r"[a-z0-9_-]{1,24}")

# What an activity does with the global switch (block_apps, block_sites):
# follow it, block nothing, or block even when it's off. The activity's
# lists apply both when it inherits and when it's on.
BLOCK_STATES = ("inherit", "off", "on")
SITE_MODES = ("", "block", "allow")  # "": the global site_mode


def clean_activities(value, tidy: bool = False) -> list[dict]:
    """
    The activity list as saved: each `{id, name, icon}` with a valid id and
    icon, a name of 1 to MAX_ACTIVITY_NAME characters, no two ids or names
    alike, at most MAX_ACTIVITIES. AppError with Settings' message, or with
    `tidy` (config.json at start-up) unusable entries are dropped instead.
    """
    if not isinstance(value, list):
        raise app_error("errors.activities.not_list")
    out, ids, names = [], set(), set()
    for item in value:
        try:
            if not isinstance(item, dict):
                raise app_error("errors.activities.missing_name")
            name = " ".join(str(item.get("name") or "").split())
            if not name:
                raise app_error("errors.activities.no_name")
            if len(name) > MAX_ACTIVITY_NAME:
                raise app_error("errors.activities.name_long", max=MAX_ACTIVITY_NAME)
            if name.casefold() in names:
                raise app_error("errors.activities.duplicate", name=name)
            ident = str(item.get("id") or "")
            if not _ID.fullmatch(ident) or ident in ids:
                raise app_error("errors.activities.bad_id", name=name)
            icon = item.get("icon") if item.get("icon") in ACTIVITY_ICONS else "dot"
        except AppError:
            if tidy:
                continue
            raise
        entry = {"id": ident, "name": name, "icon": icon}
        for key, clean in (("apps", clean_app_rules), ("sites", clean_site_rules)):
            if item.get(key) is None:
                continue
            try:
                entry[key] = clean(item[key])
            except AppError as e:
                if not tidy:
                    raise app_error("errors.named", name=name, error=e) from None
                # A broken rule set at start-up: the activity stays, on the global settings.
        try:
            screen = clean_activity_screen(item.get("bar"))
        except AppError as e:
            if not tidy:
                raise app_error("errors.named", name=name, error=e) from None
            screen = {}  # on the global focus screen
        if screen:
            entry["bar"] = screen
        ids.add(ident)
        names.add(name.casefold())
        out.append(entry)
    if len(out) > MAX_ACTIVITIES:
        if not tidy:
            raise app_error("errors.activities.too_many", max=MAX_ACTIVITIES)
        out = out[:MAX_ACTIVITIES]
    return out


def _clean_exes(values, field: str) -> list[str]:
    if not isinstance(values, list):
        raise app_error("errors.apps.list")
    out: list[str] = []
    for raw in values:
        exe = raw.strip() if isinstance(raw, str) else ""
        if not exe:
            raise app_error("errors.apps.type_name")
        if "/" in exe or "\\" in exe:
            raise app_error("errors.apps.just_program")
        if field == "block" and app_key(exe) in PROTECTED_EXES:
            raise app_error("errors.apps.protected", exe=exe)
        if app_key(exe) not in {app_key(e) for e in out}:
            out.append(exe)
    if len(out) > MAX_BLOCKED_APPS:
        key = "errors.activities.allowed_apps_max" if field == "allow" else "errors.activities.blocked_apps_max"
        raise app_error(key, max=MAX_BLOCKED_APPS)
    return out


def clean_app_rules(value) -> dict:
    """
        What a work phase on an activity does with apps: `{state, allow, block}`,
        program names taken off and added to the global list (see
    blocker.app_blocking).
        AppError with Settings' message if it's wrong.
    """
    if not isinstance(value, dict):
        raise app_error("errors.activities.apps_missing")
    state = value.get("state", "inherit")
    if state not in BLOCK_STATES:
        raise app_error("errors.activities.apps_state")
    return {
        "state": state,
        "allow": _clean_exes(value.get("allow", []), "allow"),
        "block": _clean_exes(value.get("block", []), "block"),
    }


def clean_site_rules(value) -> dict:
    """
        What a work phase on an activity does with websites: `{state, mode,
        allow, block}`, patterns as in blocked_sites (see
    site_rules.site_blocking).
        AppError with Settings' message if it's wrong.
    """
    if not isinstance(value, dict):
        raise app_error("errors.activities.sites_missing")
    state = value.get("state", "inherit")
    if state not in BLOCK_STATES:
        raise app_error("errors.activities.sites_state")
    mode = value.get("mode") or ""
    if mode not in SITE_MODES:
        raise app_error("errors.sites.mode")
    return {
        "state": state,
        "mode": mode,
        "allow": clean_blocked_sites(value.get("allow", []), "allowed"),
        "block": clean_blocked_sites(value.get("block", []), "blocked"),
    }


def session_name(activity: dict | None, detail: str) -> str:
    """
    What a free session is called: "Email · Q4 budget", "Email", the detail,
    or "Focus session" in the app language (kept as written in the history).
    """
    detail = " ".join((detail or "").split())
    if activity:
        return (f"{activity['name']} · {detail}" if detail else activity["name"])[:MAX_DETAIL]
    return detail[:MAX_DETAIL] or t("defaults.session_name")


def call_name(apps: str) -> str:
    """A call's name when its activity is gone: "Call · Zoom", in the app language."""
    return t("defaults.call_name", apps=apps)[:MAX_DETAIL]


def is_unnamed(name: str) -> bool:
    """`name` is the name of an unnamed session ("Focus session") in any shipped language."""
    return name in {translate(code, "defaults.session_name") for code in languages()}
