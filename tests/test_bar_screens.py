"""
The bar's front screen (bar_screens.py, bar_display.py): the settings and
their checks, the pictures, and BarScreens on a fake bar that keeps its
snapshot, its files and its drawing like the real one (firmware 27.5).
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from busylib import exceptions, types
from busylib.features import timer_state
from busylib.features.timer import THEMES_PATH
from PIL import Image

from buzyist import bar as bar_mod
from buzyist import bar_display, bar_screens
from buzyist.activities import clean_activities
from buzyist.api import Api
from buzyist.bar_display import THEME_SLOTS, render
from buzyist.bar_screens import DEFAULT_BAR_SCREENS, clean_bar_screens, fill_text
from buzyist.constants import AppError
from conftest import stock_firmware

ROOT = Path(__file__).resolve().parent.parent
BAR_CONFIG = {"use_busybar": True, "busybar_ip": "10.0.0.9", "busybar_pin": "", "usb_fallback": False}


# ---------------------------------------------------------------- settings ---


def test_the_defaults_are_clean_and_a_missing_screen_gets_its_default():
    assert clean_bar_screens(DEFAULT_BAR_SCREENS) == DEFAULT_BAR_SCREENS
    assert clean_bar_screens({"focus": {"mode": "card"}})["call"] == DEFAULT_BAR_SCREENS["call"]


@pytest.mark.parametrize(
    ("screen", "value", "message"),
    [
        ("focus", {"mode": "off"}, "Pick what the bar shows for the focus screen."),
        ("done", {"mode": "theme", "theme": "flow"}, "Pick what the bar shows for the end of session screen."),
        ("call", {"mode": "theme", "theme": ""}, "Pick one of the bar's themes for the call screen."),
        ("call", {"mode": "theme", "theme": "buzyist_a"}, "Pick one of the bar's themes for the call screen."),
        ("call", {"mode": "theme", "theme": "../x"}, "Pick one of the bar's themes for the call screen."),
        ("focus", {"mode": "text", "text": "  "}, "Type what the bar shows for the focus screen."),
        ("focus", {"mode": "text", "text": "x" * 61}, "Keep the focus screen's text to 60 characters or fewer."),
    ],
)
def test_a_wrong_screen_says_what_to_fix(screen, value, message):
    with pytest.raises(AppError, match=re.escape(message)):
        clean_bar_screens({**DEFAULT_BAR_SCREENS, screen: value})


def test_at_start_up_a_broken_screen_falls_back_to_its_default():
    cleaned = clean_bar_screens({"focus": {"mode": "nope"}, "call": "x"}, tidy=True)
    assert cleaned == DEFAULT_BAR_SCREENS
    assert clean_bar_screens(None, tidy=True) == DEFAULT_BAR_SCREENS


def test_text_and_colour_are_tidied():
    cleaned = clean_bar_screens({"focus": {"mode": "text", "text": "  Deep   {task} ", "color": "pink"}})
    assert cleaned["focus"] == {"mode": "text", "theme": "", "text": "Deep {task}", "color": "tomate"}


def test_an_activity_may_have_its_own_focus_screen():
    base = {"id": "meet", "name": "Meeting", "icon": "people"}
    (kept,) = clean_activities([{**base, "bar": {"mode": "theme", "theme": "meeting"}}])
    assert kept["bar"] == {"mode": "theme", "theme": "meeting", "text": "", "color": "tomate"}
    (plain,) = clean_activities([{**base, "bar": {}}])
    assert "bar" not in plain
    with pytest.raises(AppError, match="Meeting: Type what the bar shows"):
        clean_activities([{**base, "bar": {"mode": "text", "text": ""}}])
    (tidied,) = clean_activities([{**base, "bar": {"mode": "text", "text": ""}}], tidy=True)
    assert "bar" not in tidied


def test_settings_save_and_load_the_screens(app):
    screens = {**DEFAULT_BAR_SCREENS, "call": {"mode": "text", "theme": "", "text": "CALL {apps}", "color": "molho"}}
    app.save_settings({"bar_screens": screens})
    assert app.cfg["bar_screens"]["call"]["text"] == "CALL {apps}"
    with pytest.raises(AppError, match="call screen"):
        app.save_settings({"bar_screens": {**screens, "call": {"mode": "theme", "theme": ""}}})


def test_fill_text_fills_what_it_knows_and_keeps_the_rest():
    values = {"task": "Write  the plan", "round": 2, "rounds": 4, "activity": ""}
    assert fill_text("{task} {round}/{rounds}", values) == "Write the plan 2/4"
    assert fill_text("{activity} · {task}", values) == "· Write the plan"
    assert fill_text("{nope} {task}", values) == "{nope} Write the plan"


def test_the_colours_match_the_page():
    script = (ROOT / "ui" / "main.js").read_text(encoding="utf-8")
    block = re.search(r"const BAR_COLORS = \{(.*?)\};", script, re.S).group(1)
    page = dict(re.findall(r'(\w+): \["(#[0-9A-F]{6})", "#[0-9A-F]{6}"\]', block))
    assert page == {k: v[0] for k, v in bar_screens.SCREEN_COLORS.items()}
    assert f"MAX_SCREEN_TEXT = {bar_screens.MAX_SCREEN_TEXT};" in script


# ---------------------------------------------------------------- pictures ---


def test_a_picture_is_the_front_screen_in_the_screen_colour():
    image = render("CALL", "molho", 12)
    assert image.size == (72, 16) and image.mode == "RGB"
    assert image.getpixel((0, 0)) == (0xA8, 0x2A, 0x12)
    colours = {image.getpixel((x, y)) for x in range(72) for y in range(16)}
    assert colours == {(0xA8, 0x2A, 0x12), (255, 255, 255)}  # no anti-aliasing


def test_the_minutes_sit_in_the_right_corner():
    with_minutes, without = render("Go", "black", 18), render("Go", "black")
    right = lambda image: {image.getpixel((x, y)) for x in range(62, 72) for y in range(16)}  # noqa: E731
    assert (255, 255, 255) in right(with_minutes)
    assert right(without) == {(0, 0, 0)}
    assert bar_display.corner_text(18) == ("18", "min")
    assert bar_display.corner_text(125) == ("2h", "05")


def test_text_the_fonts_can_not_draw_is_left_out():
    assert bar_display.bar_text("Réunion d'équipe 🎉") == "Reunion d'equipe"
    assert render("🎉", "tomate").getcolors() == [(72 * 16, (0xD9, 0x36, 0x1B))]


def test_a_long_text_takes_two_lines_and_is_cut():
    _, small = bar_display._fonts()
    first, second = bar_display._wrap(small, "Review the partner contract draft before Friday", 55)
    assert first and second.endswith("…")
    assert bar_display._width(small, first) <= 55 and bar_display._width(small, second) <= 55
    word, rest = bar_display._wrap(small, "Supercalifragilisticexpialidocious", 55)
    assert word and "Supercalifragilisticexpialidocious".startswith(word) and rest.endswith("…")


def test_the_preview_is_a_png_of_the_picture(app):
    result = Api(app).bar_preview("{task}", "folha", 5)
    assert result["ok"]
    data = result["url"].removeprefix("data:image/png;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(data)))
    assert image.size == (72, 16)


# ------------------------------------------------------------- on the bar ---


class Device:
    """
    One BUSY Bar behind every route: its snapshot (only a newer one is
    taken), its files, and the drawing on its screen (refused while a timer
    runs, as the firmware does). Like the real one it keeps every picture
    it has read by its path, so a different picture under a path it has
    shown already fails the test (`shown`).
    """

    def __init__(self):
        self.snap = types.BusySnapshot(
            snapshot=types.BusySnapshotNotStarted(type="NOT_STARTED", busy_bar_settings=bar_mod.LOCAL_SETTINGS),
            snapshot_timestamp_ms=0,
        )
        self.files: dict[str, bytes | None] = {f"{THEMES_PATH}/on_call": None, f"{THEMES_PATH}/flow": None}
        self.drawing: dict | None = None
        self.shown: dict[str, bytes] = {}  # what the bar has read, by path: never re-read
        self.writes = 0  # snapshot writes
        self.down = False
        # VeryBUSY's apps (bar_apps.py): what runs, what was opened, the keys pressed.
        self.verybusy = True  # stock firmware answers 404 to the apps calls
        self.showing = {"name": "AppsMenu", "runtime": "", "app_id": ""}
        self.opened: list[tuple[str, str]] = []
        self.keys: list[str] = []
        self.files |= {"/ext/apps": None, "/ext/apps/misc": None, "/ext/apps/misc/Nixie.fap": b"fap"}

    def __call__(self, addr, token=None, **kwargs):
        return Client(self)

    def running(self) -> bool:
        return not isinstance(self.snap.snapshot, types.BusySnapshotNotStarted)

    @property
    def theme(self) -> str:
        return self.snap.snapshot.busy_bar_settings.theme

    def show(self, path: str) -> Image.Image:
        """The picture at `path` as the bar shows it: the first one it read there."""
        data = self.shown.setdefault(path, self.files[path])
        assert data == self.files[path], f"the bar would still show the old picture at {path}"
        return Image.open(io.BytesIO(data))

    def picture(self, slot: str) -> Image.Image:
        theme = json.loads(self.files[f"{THEMES_PATH}/{slot}/theme.json"])
        return self.show(theme["bg_path"])

    def drawn(self) -> Image.Image:
        (element,) = self.drawing["elements"]
        assert element["type"] == "image" and element["timeout"] > 0
        return self.show(f"{bar_mod.DRAW_DIR}/{element['path']}")


class Client:
    def __init__(self, device: Device):
        self.device = device
        self.client = SimpleNamespace(close=lambda: None)

    def _check(self):
        if self.device.down:
            raise exceptions.BusyBarRequestError("timed out")

    def busy_snapshot(self):
        self._check()
        return self.device.snap

    def busy_snapshot_set(self, snap):
        self._check()
        if snap.snapshot_timestamp_ms > self.device.snap.snapshot_timestamp_ms:
            self.device.snap = snap
            self.device.writes += 1

    def busy_profile(self, slot):
        self._check()
        return SimpleNamespace(id="card-busy", busy_bar_settings=bar_mod.LOCAL_SETTINGS)

    def storage_list(self, path):
        self._check()
        if path not in self.device.files and path != THEMES_PATH:
            raise exceptions.BusyBarAPIError("missing", status_code=404, method="GET", path="/api/storage/list")
        names = {p[len(path) + 1 :].split("/")[0] for p in self.device.files if p.startswith(path + "/")}
        kind = lambda n: "dir" if self.device.files.get(f"{path}/{n}") is None else "file"  # noqa: E731
        return SimpleNamespace(list=[SimpleNamespace(name=n, type=kind(n)) for n in sorted(names)])

    def storage_mkdir(self, path):
        self._check()
        if path in self.device.files:
            raise exceptions.BusyBarAPIError("exists", status_code=400, method="POST", path="/api/storage/mkdir")
        self.device.files[path] = None

    def storage_write(self, path, data):
        self._check()
        self.device.files[path] = data

    def _request(self, method, path, params=None, data=None):
        self._check()
        if path.startswith("/api/apps/") or path == "/api/input":
            return self._apps(method, path, params or {})
        assert (method, path) == ("POST", "/api/storage/write")
        Image.open(io.BytesIO(data)).verify()  # a real PNG, sent as it is
        self.storage_write(params["path"], data)

    def _apps(self, method, path, params):
        device = self.device
        if not device.verybusy:
            raise exceptions.BusyBarAPIError("Not found", status_code=404, method=method, path=path)
        if (method, path) == ("GET", "/api/apps/running"):
            return dict(device.showing)
        if (method, path) == ("GET", "/api/apps/system"):
            names = [("busy", "Busy", "apps"), ("clock", "Clock", "apps"), ("about", "About", "settings")]
            return {"list": [{"appid": a, "name": n, "group": g, "blocked": False} for a, n, g in names]}
        if (method, path) == ("POST", "/api/apps/launch"):
            device.opened.append((params["runtime"], params["id"]))
            app_id = "" if params["runtime"] == "system" else params["id"]
            device.showing = {"name": params["id"].title(), "runtime": params["runtime"], "app_id": app_id}
            return {"result": "OK"}
        if (method, path) == ("POST", "/api/input"):
            device.keys.append(params["key"])
            return {"result": "OK"}
        raise AssertionError(f"{method} {path}")

    def storage_read(self, path):
        self._check()
        return self.device.files[path]

    def storage_remove(self, path):
        self._check()
        if path not in self.device.files:
            raise exceptions.BusyBarAPIError("missing", status_code=404, method="DELETE", path="/api/storage/remove")
        del self.device.files[path]

    def display_draw(self, drawing):
        self._check()
        if self.device.running():
            raise exceptions.BusyBarAPIError("Not drawn due to low priority", status_code=409, method="POST", path="/d")
        assert drawing["application_name"] == bar_mod.DRAW_APP
        self.device.drawing = drawing
        self.device.drawn()  # its picture is there

    def display_clear(self, application_name=None):
        self._check()
        self.device.drawing = None


@pytest.fixture
def device(monkeypatch) -> Device:
    bar = Device()
    monkeypatch.setattr(bar_mod, "BusyBar", bar)
    stock_firmware(monkeypatch)
    return bar


@pytest.fixture
def run(make_app, clock, deferred_threads, device):
    """`run(cfg)`: an app on the fake bar with a `step(**advance)` that polls and then lets BarScreens look."""

    def build(cfg=None):
        app = make_app({**BAR_CONFIG, "use_todoist": False, **(cfg or {})})

        def step(**advance):
            clock.advance(**advance)
            app.engine_step()
            deferred_threads.run_pending()
            app.bar_screens.step()
            app.engine_step()  # reads back what it changed
            return app

        app.step = step
        return app

    return build


def start(app, deferred_threads, name="Write the plan", activity=""):
    app.start_task(None, name, activity)
    deferred_threads.run_pending()
    return app.step()


def test_a_text_screen_puts_its_picture_on_the_running_timer(run, device, deferred_threads):
    app = start(run(), deferred_threads)
    assert device.theme == THEME_SLOTS[0]
    assert json.loads(device.files[f"{THEMES_PATH}/{THEME_SLOTS[0]}/theme.json"])["bg_path"].endswith(".png")
    assert device.picture(THEME_SLOTS[0]).tobytes() == render("Write the plan", "tomate", 25).tobytes()
    assert timer_state(device.snap).time_left_ms == app.timer_view()["left_ms"]  # the timer didn't move
    writes = device.writes
    app.step(seconds=20)
    assert device.writes == writes  # nothing changed: nothing written


def test_the_minutes_change_through_the_other_slot(run, device, deferred_threads):
    app = start(run(), deferred_threads)
    app.step(minutes=1)
    assert device.theme == THEME_SLOTS[1]
    assert device.picture(THEME_SLOTS[1]).tobytes() == render("Write the plan", "tomate", 24).tobytes()
    assert device.picture(THEME_SLOTS[0]).tobytes() == render("Write the plan", "tomate", 25).tobytes()
    app.step(minutes=1)
    assert device.theme == THEME_SLOTS[0]
    left = timer_state(device.snap).time_left_ms
    assert 22 * 60000 < left <= 23 * 60000  # still counting down from the start


def test_the_wake_up_lands_just_after_the_minute_turns(run, device, deferred_threads):
    app = start(run(), deferred_threads)
    app.step(seconds=15)
    assert app.bar_screens.step() == pytest.approx(45.3, abs=0.1)


# {round}/{rounds}: what texts saved before the pomodoro wording said (OLD_PLACEHOLDERS).
@pytest.mark.parametrize("text", ["{pomodoro}/{pomodoros}", "{round}/{rounds}"])
def test_the_break_gets_the_next_pomodoro_ready(run, device, deferred_threads, text):
    app = start(
        run({"bar_screens": {**DEFAULT_BAR_SCREENS, "focus": {"mode": "text", "text": text}}}),
        deferred_threads,
    )
    app.step(minutes=25, seconds=10)
    assert app.timer_view()["phase"] == "rest"
    slot = device.theme
    assert device.picture(slot).tobytes() == render("2/4", "tomate", 25).tobytes()


def test_a_theme_screen_and_the_card(run, device, deferred_threads):
    app = start(
        run({"bar_screens": {**DEFAULT_BAR_SCREENS, "focus": {"mode": "theme", "theme": "flow"}}}), deferred_threads
    )
    assert device.theme == "flow"
    app.save_settings({"bar_screens": {**DEFAULT_BAR_SCREENS, "focus": {"mode": "card"}}})
    app.step()
    assert device.theme == bar_mod.LOCAL_SETTINGS.theme
    assert not any(slot in path for path in device.files for slot in THEME_SLOTS)


def test_a_theme_the_bar_does_not_have_falls_back_to_the_card(run, device, deferred_threads):
    del device.files[f"{THEMES_PATH}/flow"]
    start(run({"bar_screens": {**DEFAULT_BAR_SCREENS, "focus": {"mode": "theme", "theme": "flow"}}}), deferred_threads)
    assert device.theme == bar_mod.LOCAL_SETTINGS.theme


def test_an_activity_replaces_the_focus_screen(run, device, deferred_threads):
    activities = [{"id": "meet", "name": "Meeting", "icon": "people", "bar": {"mode": "theme", "theme": "on_call"}}]
    start(run({"activities": activities}), deferred_threads, name="", activity="meet")
    assert device.theme == "on_call"


def test_a_call_shows_the_call_screen_with_its_minutes(run, device, deferred_threads, clock):
    screens = {**DEFAULT_BAR_SCREENS, "call": {"mode": "text", "text": "CALL", "color": "molho"}}
    app = run({"bar_screens": screens})
    assert app.start_call([{"key": "zoom.exe", "name": "Zoom"}], clock.ms - 3 * 60000)
    app.step()
    assert isinstance(device.snap.snapshot, types.BusySnapshotInfinite)
    assert device.picture(device.theme).tobytes() == render("CALL", "molho", 3).tobytes()
    app.step(minutes=1)
    assert device.picture(device.theme).tobytes() == render("CALL", "molho", 4).tobytes()
    assert app.session["call"]  # still the same call


@pytest.mark.parametrize("text", ["{pomodoros} done", "{tomatoes} done"])
def test_the_done_screen_counts_the_pomodoros(run, device, deferred_threads, text):
    done = {"mode": "text", "text": text, "color": "folha"}
    app = start(run({"cycles": 1, "bar_screens": {**DEFAULT_BAR_SCREENS, "done": done}}), deferred_threads)
    app.step(minutes=25, seconds=10)
    deferred_threads.run_pending()
    app.step()
    assert app.ended["done"] == 1
    assert device.drawn().tobytes() == render("1 done", "folha").tobytes()


def test_the_done_screen_is_drawn_until_the_summary_closes(run, device, deferred_threads):
    app = start(run({"cycles": 1}), deferred_threads)
    app.control("stop")
    deferred_threads.run_pending()
    app.step()
    assert app.ended and not device.running()
    assert device.drawn().tobytes() == render("DONE", "folha").tobytes()
    Api(app).dismiss_ended()
    app.step()
    assert device.drawing is None
    assert not [p for p in device.files if p.startswith(bar_mod.DRAW_DIR + "/")]  # its picture went too


def test_a_timer_started_on_the_bar_itself_is_left_alone(run, device):
    app = run()
    app.bar.start(25 * 60000, 5 * 60000, 2, False)
    app.step()
    assert device.theme == bar_mod.LOCAL_SETTINGS.theme


def test_off_or_on_this_pc_nothing_is_put_on_the_bar(run, make_app, device, deferred_threads):
    start(run({"bar_screens_on": False}), deferred_threads)
    assert device.theme == bar_mod.LOCAL_SETTINGS.theme
    local = make_app({"use_todoist": False})
    local.start_task(None, "x")
    deferred_threads.run_pending()
    assert local.bar_screens.wanted() is None
    assert local.bar_screens.step() == bar_display.IDLE_WAIT_S


def test_a_picture_the_bar_did_not_get_is_sent_again(run, device, deferred_threads, clock):
    app = start(run(), deferred_threads)
    device.down = True
    clock.advance(minutes=1)
    with pytest.raises(AppError):
        app.bar_screens.step()
    device.down = False
    app.step()
    assert device.picture(device.theme).tobytes() == render("Write the plan", "tomate", 24).tobytes()


def test_a_picture_shown_again_after_others_is_the_right_one(run, device, deferred_threads):
    """The bar never re-reads a path: the same picture comes back under the same name, a new one under a new name."""
    app = start(run(), deferred_threads)
    first = json.loads(device.files[f"{THEMES_PATH}/{device.theme}/theme.json"])["bg_path"]
    for _ in range(3):
        app.step(minutes=1)
        device.picture(device.theme)  # Device.show fails on a stale picture
    assert json.loads(device.files[f"{THEMES_PATH}/{device.theme}/theme.json"])["bg_path"] != first
    folders = {p.rsplit("/", 1)[0] for p in device.files if p.endswith(".png")}
    pngs = [p for p in device.files if p.endswith(".png")]
    assert len(pngs) == len(folders) == 2  # one picture per slot: the old ones are deleted


def test_after_a_restart_an_old_picture_is_not_shown(run, device, deferred_threads):
    """Pictures from an earlier run (the bar still holds them) never get new content under their path."""
    app = start(run(), deferred_threads)
    app.bar_screens = bar_display.BarScreens(app)  # a new run: it knows nothing of what's on the bar
    app.save_settings(
        {"bar_screens": {**DEFAULT_BAR_SCREENS, "focus": {"mode": "text", "text": "Other", "color": "tomate"}}}
    )
    app.step(minutes=1)
    assert device.picture(device.theme).tobytes() == render("Other", "tomate", 24).tobytes()


# ------------------------------------------------------- after a session ---

NIXIE = "fap:/ext/apps/misc/Nixie.fap"


def stopped(app, deferred_threads):
    """Stop the running session and let it finish; returns the app."""
    app.control("stop")
    deferred_threads.run_pending()
    return app.step()


def test_the_app_opens_and_starts_once_the_done_screen_has_gone(run, device, deferred_threads):
    app = stopped(start(run({"bar_end_app": NIXIE}), deferred_threads), deferred_threads)
    assert device.drawing is not None and device.opened == []  # the done screen first
    Api(app).dismiss_ended()
    app.step()
    assert device.drawing is None
    assert device.opened == [("fap", "/ext/apps/misc/Nixie.fap")]
    assert device.keys == ["ok"]  # from its Start screen to the clock
    app.step(minutes=1)
    assert device.keys == ["ok"]  # once


def test_with_no_done_screen_it_opens_at_once(run, device, deferred_threads):
    screens = {**DEFAULT_BAR_SCREENS, "done": {"mode": "off"}}
    stopped(start(run({"bar_end_app": "system:clock", "bar_screens": screens}), deferred_threads), deferred_threads)
    assert device.opened == [("system", "clock")] and device.keys == ["ok"]


def test_it_waits_while_the_bar_shows_the_timer_then_gives_up_elsewhere(run, device, deferred_threads):
    screens = {**DEFAULT_BAR_SCREENS, "done": {"mode": "off"}}
    app = run({"bar_end_app": NIXIE, "bar_screens": screens})
    device.showing = {"name": "Busy", "runtime": "", "app_id": ""}  # the timer's "Well done!"
    stopped(start(app, deferred_threads), deferred_threads)
    assert device.opened == []
    device.showing = {"name": "", "runtime": "", "app_id": ""}  # a moment with no app, as on a real bar
    app.step(seconds=5)
    assert device.opened == []
    device.showing = {"name": "AppsMenu", "runtime": "", "app_id": ""}
    app.step(seconds=5)
    assert device.opened == [("fap", "/ext/apps/misc/Nixie.fap")]


@pytest.mark.parametrize("showing", ["Clock", "Software Power Off"])
def test_a_bar_not_on_the_apps_list_is_left_alone(run, device, deferred_threads, showing):
    screens = {**DEFAULT_BAR_SCREENS, "done": {"mode": "off"}}
    device.showing = {"name": showing, "runtime": "", "app_id": ""}
    app = stopped(start(run({"bar_end_app": NIXIE, "bar_screens": screens}), deferred_threads), deferred_threads)
    device.showing = {"name": "AppsMenu", "runtime": "", "app_id": ""}
    app.step(seconds=5)
    assert device.opened == [] and device.keys == []


def test_a_new_session_or_no_setting_opens_nothing(run, device, deferred_threads):
    app = stopped(start(run(), deferred_threads), deferred_threads)  # bar_end_app off
    Api(app).dismiss_ended()
    app.step()
    assert device.opened == []
    app.save_settings({"bar_end_app": NIXIE})
    app = stopped(start(app, deferred_threads), deferred_threads)
    start(app, deferred_threads, name="Next one")  # straight into another session
    app.step(minutes=1)
    assert device.opened == []


def test_stock_firmware_is_asked_once(run, device, deferred_threads):
    screens = {**DEFAULT_BAR_SCREENS, "done": {"mode": "off"}}
    device.verybusy = False
    app = stopped(start(run({"bar_end_app": NIXIE, "bar_screens": screens}), deferred_threads), deferred_threads)
    app.step(seconds=5)
    assert device.opened == [] and not app.bar_screens._end_app


def test_the_picker_lists_the_bars_apps(run, device):
    weather = "/ext/user_assets/app.busy.weather"
    device.files |= {
        "/ext/user_assets": None,
        "/ext/user_assets/buzyist": None,  # ours: app data, not an app
        weather: None,
        f"{weather}/appmeta": None,
        f"{weather}/appmeta/manifest.json": json.dumps({"id": "app.busy.weather", "name": "Weather"}).encode(),
        f"{weather}/scripts": None,
        f"{weather}/scripts/main.js": b"",
    }
    app = run()
    apps = Api(app).bar_apps()
    assert apps["ok"]
    assert apps["apps"] == [
        {"value": "system:clock", "name": "Clock"},
        {"value": NIXIE, "name": "Nixie"},
        {"value": "js:app.busy.weather", "name": "Weather"},
    ]
    device.verybusy = False
    assert "VeryBUSY" in Api(app).bar_apps()["error"]


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        (NIXIE, True),
        ("system:clock", True),
        ("js:app.busy.weather", True),
        ("clock", False),
        ("fap:Nixie.fap", False),
        ("fap:/ext/apps/../x.fap", False),
        ("shell:rm", False),
        (3, False),
    ],
)
def test_the_setting_is_checked(app, value, ok):
    if ok:
        app.save_settings({"bar_end_app": value})
        assert app.cfg["bar_end_app"] == value
    else:
        with pytest.raises(AppError, match="Pick one of the bar's apps"):
            app.save_settings({"bar_end_app": value})
