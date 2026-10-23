"""
The app the BUSY Bar opens once a session is over (`bar_end_app`, Settings,
BUSY Bar; off by default). Only VeryBUSY (custom firmware) can do it: its
`POST /api/apps/launch` starts an app, `GET /api/apps/running` says what runs,
and stock firmware answers 404 to both.

Why: when a timer ends, a bar whose mode switch is on Apps goes back to the
Apps list (`AppsMenu`), even when an app ran before the timer. So once our
session's done screen has gone (BarScreens), the app is opened and started.

Found on a real bar (VeryBUSY r12, API 27.7): every app opens on its Start
screen (installed apps and the built-in Clock alike; the launch can't pass
their skip-the-menu flag), so starting it is an OK press. The same press on a
running app does something else (Nixie Clock changes its colour), so OK is
pressed only on an app just opened.

The setting is "<runtime>:<id>", the two query values of the launch:
`system:clock` (a built-in, by appid), `fap:/ext/apps/misc/Nixie.fap` (an
installed app, by its path), `js:app.busy.weather`, `python:<id>`.
"""

from __future__ import annotations

from buzyist.i18n import app_error

RUNTIMES = ("system", "fap", "js", "python")
APPS_MENU = "AppsMenu"  # what /api/apps/running names while the bar shows the Apps list
TIMER_APP = "Busy"  # the timer's own app: on show while a timer ends ("Well done!")
END_APP_WAIT_S = 120  # how long after the session the bar may take to get back to the Apps list
END_APP_POLL_S = 3  # how often it is looked at meanwhile
LAUNCH_SETTLE_S = 1.5  # from the launch to the OK press (the app is loading)
MAX_ID = 255  # the firmware's longest id


def parse_end_app(value: str) -> tuple[str, str] | None:
    """(runtime, id) of a `bar_end_app` value; None when it is "" (nothing to open)."""
    if not value:
        return None
    runtime, _, app_id = value.partition(":")
    return runtime, app_id


def clean_end_app(value, tidy: bool = False) -> str:
    """A `bar_end_app` value checked; "" is off. `tidy` (start-up) turns a broken one off."""
    value = (value.strip() if isinstance(value, str) else value) or ""
    if value == "":
        return ""
    if _is_end_app(value):
        return value
    if tidy:
        return ""
    raise app_error("errors.bar_end_app")


def _is_end_app(value) -> bool:
    if not isinstance(value, str):
        return False
    runtime, _, app_id = value.partition(":")
    if runtime not in RUNTIMES or not app_id or len(app_id) > MAX_ID or not app_id.isprintable():
        return False
    if ".." in app_id.split("/"):
        return False
    return runtime != "fap" or (app_id.startswith("/ext/apps/") and app_id.endswith(".fap"))


def app_label(value: str) -> str:
    """A name for a `bar_end_app` value the bar didn't list: the file name, else the id."""
    parsed = parse_end_app(value)
    if not parsed:
        return ""
    runtime, app_id = parsed
    if runtime == "fap":
        return app_id.rsplit("/", 1)[-1].removesuffix(".fap")
    return app_id[0].upper() + app_id[1:] if runtime == "system" else app_id
