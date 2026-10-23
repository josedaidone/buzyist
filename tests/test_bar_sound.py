"""The reminders' chime on the BUSY Bar (bar_sound, Bar.play_sound, App.beep)."""

from __future__ import annotations

import array
from types import SimpleNamespace

import pytest
from busylib import exceptions

from buzyist import bar as bar_mod
from buzyist import bar_sound
from buzyist.api import Api
from buzyist.config import DEFAULTS

BAR_CONFIG = {"use_busybar": True, "busybar_ip": "10.0.0.9", "busybar_pin": "", "usb_fallback": False}


# ------------------------------------------------------------------ sound ---


def test_the_chime_is_the_one_picked_in_the_lab():
    assert bar_sound.CHIME == {
        "pattern": "beeps",
        "shape": "square",
        "freq": 1568,
        "beep_ms": 260,
        "gap_ms": 60,
        "beeps": 3,
        "gain": 0.3,
        "rate": 44100,
    }


def test_the_chime_is_raw_mono_16_bit_pcm():
    sound = bar_sound.chime()
    samples = array.array("h", sound)
    assert len(sound) == 2 * 3 * (int(44100 * 0.26) + int(44100 * 0.06))  # 3 beeps and their silences
    assert max(map(abs, samples)) == int(0.3 * 0.8 * 32767)  # the gain, on a square's 0.8
    assert samples[0] == 0  # faded in: no click
    assert bar_sound.chime() is sound  # made once


def test_a_tone_too_long_is_refused():
    with pytest.raises(ValueError, match="longer than"):
        bar_sound.tone({"beeps": 40, "beep_ms": 300, "gap_ms": 0})


def test_a_sound_is_named_after_its_content():
    one, other = bar_sound.tone({"freq": 1000}), bar_sound.tone({"freq": 1200})
    assert bar_sound.sound_name(one) == bar_sound.sound_name(bytes(one))
    assert bar_sound.sound_name(one) != bar_sound.sound_name(other)
    assert bar_sound.sound_name(one).endswith(".snd")


def test_the_setting_is_on_by_default():
    assert DEFAULTS["bar_sound"] is True


# ---------------------------------------------------------------- the bar ---


class Speaker:
    """
    A bar's speaker and storage. Like firmware 27.5 it answers OK to playing
    a file it doesn't have, and plays nothing (`silent`).
    """

    def __init__(self):
        self.files: dict[str, bytes | None] = {}
        self.played: list[tuple[str, str]] = []
        self.silent = 0  # plays of a file it doesn't have
        self.uploads = 0
        self.down = False
        self.broken = False  # answers 500 to a play
        self.client = SimpleNamespace(close=lambda: None)

    def __call__(self, addr, token=None, **kwargs):
        return self

    def _check(self):
        if self.down:
            raise exceptions.BusyBarRequestError("timed out")

    def audio_play(self, *, path=None, stock_path=None, application_name=None):
        self._check()
        assert stock_path is None and application_name == bar_mod.DRAW_APP
        if self.broken:
            raise exceptions.BusyBarAPIError("Failed", status_code=500, method="POST", path="/api/audio/play")
        if f"{bar_mod.DRAW_DIR}/{path}" not in self.files:
            self.silent += 1
            return
        self.played.append((path, application_name))

    def storage_list(self, path):
        self._check()
        if path not in self.files:
            raise exceptions.BusyBarAPIError("missing", status_code=404, method="GET", path="/api/storage/list")
        names = [p[len(path) + 1 :] for p in self.files if p.startswith(path + "/")]
        return SimpleNamespace(list=[SimpleNamespace(name=n, type="file") for n in names])

    def storage_mkdir(self, path):
        self._check()
        if path in self.files:
            raise exceptions.BusyBarAPIError("exists", status_code=400, method="POST", path="/api/storage/mkdir")
        self.files[path] = None

    def _request(self, method, path, params=None, data=None):
        self._check()
        assert (method, path) == ("POST", "/api/storage/write")
        self.files[params["path"]] = data
        self.uploads += 1


@pytest.fixture
def speaker(monkeypatch) -> Speaker:
    bar = Speaker()
    monkeypatch.setattr(bar_mod, "BusyBar", bar)
    return bar


def test_the_sound_is_uploaded_once_then_played(speaker):
    bar = bar_mod.Bar(BAR_CONFIG)
    sound = bar_sound.chime()
    name = bar_sound.sound_name(sound)
    bar.play_sound(sound)
    assert speaker.files[f"{bar_mod.DRAW_DIR}/{name}"] == sound
    bar.play_sound(sound)
    assert speaker.uploads == 1
    assert speaker.played == [(name, bar_mod.DRAW_APP)] * 2
    assert speaker.silent == 0


def test_a_sound_the_bar_lost_is_sent_again(speaker):
    bar = bar_mod.Bar(BAR_CONFIG)
    bar.play_sound(bar_sound.chime())
    speaker.files.clear()  # a reset bar
    bar.play_sound(bar_sound.chime())
    assert speaker.uploads == 2 and len(speaker.played) == 2
    assert speaker.silent == 0


def test_another_refusal_is_an_error(speaker):
    speaker.broken = True
    with pytest.raises(bar_mod.AppError):
        bar_mod.Bar(BAR_CONFIG).play_sound(bar_sound.chime())
    assert speaker.played == []


# ---------------------------------------------------------------- the app ---


@pytest.fixture
def ring(make_app, deferred_threads, side_effects, speaker):
    """`ring(cfg)`: an app's beep() run to its end; returns the app."""

    def go(cfg=None, **setup):
        app = make_app({**BAR_CONFIG, "use_todoist": False, **(cfg or {})})
        for key, value in setup.items():
            setattr(app, key, value)
        app.beep()
        deferred_threads.run_pending()
        return app

    return go


def test_a_reminder_rings_the_bar_and_not_the_pc(ring, speaker, side_effects):
    ring()
    assert len(speaker.played) == 1
    assert side_effects.beeps == []


@pytest.mark.parametrize("cfg", [{"bar_sound": False}, {"use_busybar": False}])
def test_off_or_without_a_bar_the_pc_beeps(ring, speaker, side_effects, cfg):
    ring(cfg)
    assert speaker.played == []
    assert [b[0] for b in side_effects.beeps] == ["Beep"] * 3


def test_a_bar_out_of_reach_lets_the_pc_beep(ring, speaker, side_effects):
    speaker.down = True
    ring()
    assert [b[0] for b in side_effects.beeps] == ["Beep"] * 3


def test_an_offline_bar_is_not_tried(make_app, deferred_threads, speaker, side_effects):
    app = make_app({**BAR_CONFIG, "use_todoist": False})
    app.bar.offline = True
    app.beep()
    deferred_threads.run_pending()
    assert speaker.played == [] and speaker.uploads == 0
    assert len(side_effects.beeps) == 3


def test_nothing_rings_during_a_call(make_app, deferred_threads, speaker, side_effects):
    app = make_app({**BAR_CONFIG, "use_todoist": False})
    app.session = {"call": {"apps": ["Teams"]}}
    app.beep()
    assert deferred_threads.run_pending() == 0
    assert speaker.played == [] and side_effects.beeps == []


def test_the_play_button_rings_even_with_the_setting_off(make_app, speaker):
    app = make_app({**BAR_CONFIG, "use_todoist": False, "bar_sound": False})
    assert Api(app).play_bar_sound() == {"ok": True}
    assert len(speaker.played) == 1


def test_the_play_button_says_when_there_is_no_bar(make_app, speaker):
    result = Api(make_app({"use_busybar": False})).play_bar_sound()
    assert result["ok"] is False and result["error"]
    speaker.down = True
    result = Api(make_app({**BAR_CONFIG, "use_todoist": False})).play_bar_sound()
    assert result["ok"] is False and result["error"]
