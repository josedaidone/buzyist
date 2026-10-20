"""
Export report (the Stats drawer): App.save_report saves the page ui/report.js
builds where the Save dialog says, and the texts the drawer and the report
name are in the catalogs.
"""

from __future__ import annotations

import json
import re

import pytest

from buzyist import i18n, paths
from buzyist.api import Api
from buzyist.app import report_file_name
from buzyist.constants import AppError

PAGE = "<!doctype html><title>Stats</title><p>Focus · 1h 15m</p>"


class FakeWindow:
    """A main window whose Save dialog answers `picked` and records how it was asked."""

    def __init__(self, picked):
        self.picked, self.asked = picked, []

    def create_file_dialog(self, dialog_type, directory="", allow_multiple=False, save_filename="", file_types=()):
        self.asked.append({"type": dialog_type, "name": save_filename, "types": file_types})
        return self.picked


def test_saves_the_page_and_opens_it(app, side_effects, tmp_path):
    target = tmp_path / "report.html"
    app.main = FakeWindow((str(target),))
    out = app.save_report(PAGE, "buzyist-stats-2026-10-08_2026-10-14.html")
    assert out == {"path": str(target)}
    assert target.read_text(encoding="utf-8") == PAGE
    assert side_effects.started == [str(target)]
    assert app.main.asked[0]["name"] == "buzyist-stats-2026-10-08_2026-10-14.html"
    assert app.main.asked[0]["types"] == ("HTML (*.html;*.htm)",)


def test_adds_html_to_a_name_without_it(app, side_effects, tmp_path):
    app.main = FakeWindow(str(tmp_path / "week"))  # a plain string, as some dialogs answer
    out = app.save_report(PAGE, "x.html")
    assert out["path"] == str(tmp_path / "week.html")
    assert (tmp_path / "week.html").exists()


@pytest.mark.parametrize("picked", [None, (), ""])
def test_cancelled(app, side_effects, picked):
    app.main = FakeWindow(picked)
    assert app.save_report(PAGE, "x.html") == {"path": None}
    assert side_effects.started == []


def test_refuses_an_empty_page_or_no_window(app, side_effects):
    app.main = FakeWindow(None)
    with pytest.raises(AppError):
        app.save_report("  ", "x.html")
    app.main = None
    with pytest.raises(AppError):
        app.save_report(PAGE, "x.html")


def test_write_failure_is_an_app_error(app, side_effects, tmp_path):
    app.main = FakeWindow((str(tmp_path / "missing" / "report.html"),))
    out = Api(app).save_report(PAGE, "x.html")
    assert out["ok"] is False and out["error"]
    assert side_effects.started == []


@pytest.mark.parametrize(
    ("name", "out"),
    [
        ("buzyist-stats-2026-10-14.html", "buzyist-stats-2026-10-14.html"),
        ("a b/c\\d:e.HTM", "a-b-c-d-e.html"),
        ("", "buzyist-stats.html"),
        (None, "buzyist-stats.html"),
        ("..", "buzyist-stats.html"),
    ],
)
def test_report_file_name(name, out):
    assert report_file_name(name) == out


def test_report_assets(app):
    out = Api(app).report_assets()
    assert out["ok"] and out["logo"].lstrip().startswith("<svg") and out["version"]


def test_the_keys_the_report_and_the_drawer_name_exist():
    english = i18n.flatten(json.loads((paths.I18N_DIR / "en.json").read_text(encoding="utf-8")))
    plurals = {k.rsplit(".", 1)[0] for k in english}
    known = set(english) | plurals
    script = (paths.UI / "report.js").read_text(encoding="utf-8")
    page = (paths.UI / "main.html").read_text(encoding="utf-8")
    keys = set(re.findall(r"""\bt\(\s*"([\w.]+)"\s*[,)]""", script))
    keys |= set(re.findall(r"""\["[fl]", "([\w.]+)"\]""", script))  # the legends' keys
    keys |= set(re.findall(r'data-i18n(?:-[\w-]+)?="([\w.]+)"', page))
    assert {"stats.report.export", "errors.report.empty", "main.header.stats"} <= keys
    assert sorted(keys - known) == []
