"""The app language (buzyist/i18n.py): the catalogs, t() and the language setting."""

import json
import re
from pathlib import Path

import pytest

from buzyist import constants, i18n, paths
from buzyist.api import Api
from buzyist.config import DEFAULTS, Config

CASES = json.loads((Path(__file__).parent / "i18n_cases.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture
def catalogs_dir(tmp_path, monkeypatch):
    """Point the catalogs at a folder of the test's own, write_catalogs(files) fills it."""
    folder = tmp_path / "i18n"
    folder.mkdir()
    monkeypatch.setattr(paths, "I18N_DIR", folder)
    i18n.raw_catalog.cache_clear()
    i18n.catalog.cache_clear()

    def write_catalogs(files: dict):
        for lang, tree in files.items():
            (folder / f"{lang}.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        i18n.raw_catalog.cache_clear()
        i18n.catalog.cache_clear()

    yield write_catalogs
    monkeypatch.undo()
    i18n.raw_catalog.cache_clear()
    i18n.catalog.cache_clear()


# ------------------------------------------------- the shared cases ---


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: f"{c['lang']}-{c['key']}-{c.get('values', '')}")
def test_t_on_the_test_catalogs(case, catalogs_dir):
    """The same cases run against t() in ui/i18n.js (tests/js/i18n.test.mjs)."""
    catalogs_dir(CASES["catalogs"])
    assert i18n.translate(case["lang"], case["key"], case.get("values")) == case["out"]


@pytest.mark.parametrize("case", CASES["shipped"], ids=lambda c: f"{c['lang']}-{c['key']}-{c.get('values', '')}")
def test_t_on_the_shipped_catalogs(case):
    assert i18n.translate(case["lang"], case["key"], case.get("values")) == case["out"]


# ------------------------------------------------------------ parity ---


def placeholders(entry) -> set[str]:
    texts = entry.values() if isinstance(entry, dict) else [entry]
    return {name for text in texts for name in PLACEHOLDER.findall(text)}


def catalog_problems(lang: str, tree: dict, english_tree: dict) -> list[str]:
    """What keeps a catalog file from matching English's: keys, placeholders, plural forms."""
    problems = []
    flat, english = i18n.flatten(tree), i18n.flatten(english_tree)
    problems += [f"{lang}: missing {key}" for key in sorted(english.keys() - flat.keys())]
    problems += [f"{lang}: extra {key}" for key in sorted(flat.keys() - english.keys())]
    needed = set(i18n.plural_rule(lang)[0])
    for key in sorted(flat.keys() & english.keys()):
        mine, theirs = flat[key], english[key]
        if not isinstance(mine, str | dict):
            problems.append(f"{lang}: {key} is neither a text nor a plural entry")
            continue
        if isinstance(mine, dict) != isinstance(theirs, dict):
            problems.append(
                f"{lang}: {key} is {'a plural' if isinstance(mine, dict) else 'not a plural'} unlike English"
            )
            continue
        if isinstance(mine, dict) and set(mine) != needed:
            problems.append(f"{lang}: {key} has the forms {sorted(mine)}, the language needs {sorted(needed)}")
        if placeholders(mine) != placeholders(theirs):
            problems.append(f"{lang}: {key} has {sorted(placeholders(mine))}, English {sorted(placeholders(theirs))}")
    return problems


def shipped() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(paths.I18N_DIR.glob("*.json"))}


def test_english_and_portuguese_are_shipped():
    assert list(i18n.languages()) == ["en", "pt-BR"]


@pytest.mark.parametrize("lang", sorted(shipped()))
def test_every_catalog_matches_english(lang):
    files = shipped()
    assert i18n.plural_rule(lang) is i18n.PLURALS[lang.split("-")[0]], f"add {lang}'s plural rule to PLURALS"
    assert catalog_problems(lang, files[lang], files["en"]) == []


def test_every_value_is_a_text_or_a_plural_entry():
    for lang, tree in shipped().items():
        for key, value in i18n.flatten(tree).items():
            assert isinstance(value, str) or i18n.is_plural(value), f"{lang}: {key}"


@pytest.mark.parametrize(
    ("tree", "problem"),
    [
        ({"a": {"x": "X"}}, "pt-BR: missing a.y"),
        ({"a": {"x": "X", "y": {"one": "{n} y", "other": "{n} ys"}, "z": "Z"}}, "pt-BR: extra a.z"),
        ({"a": {"x": "{name}", "y": {"one": "{n} y", "other": "{n} ys"}}}, "pt-BR: a.x has ['name'], English []"),
        (
            {"a": {"x": "X", "y": {"other": "{n} ys"}}},
            "pt-BR: a.y has the forms ['other'], the language needs ['one', 'other']",
        ),
        ({"a": {"x": "X", "y": "{n} ys"}}, "pt-BR: a.y is not a plural unlike English"),
        ({"a": {"x": "X", "y": {"one": "y", "other": "ys"}}}, "pt-BR: a.y has [], English ['n']"),
    ],
)
def test_the_parity_check_catches(tree, problem):
    english = {"a": {"x": "X", "y": {"one": "{n} y", "other": "{n} ys"}}}
    assert catalog_problems("pt-BR", english, english) == []
    assert problem in catalog_problems("pt-BR", tree, english)


# ---------------------------------------------- language at call time ---


def test_without_an_app_t_is_english():
    assert i18n.language() == "en"
    assert i18n.t("summary.title_stopped") == "Session stopped"


def test_t_follows_the_setting(make_app):
    app = make_app({"language": "pt-BR"})
    assert i18n.t("summary.result", n=2, duration="50 min") == "2 pomodoros · 50 min de foco"
    app.save_settings({"language": "en"})
    assert i18n.t("summary.result", n=2, duration="50 min") == "2 pomodoros · 50 min of focus"


def test_a_missing_portuguese_key_shows_english(make_app, catalogs_dir):
    catalogs_dir(
        {
            "en": {"language": {"name": "English"}, "a": {"b": "In English"}},
            "pt-BR": {"language": {"name": "Português (Brasil)"}},
        }
    )
    make_app({"language": "pt-BR"})
    assert i18n.language() == "pt-BR"
    assert i18n.t("a.b") == "In English"


# ---------------------------------------------------- the setting ---


def test_new_installs_and_existing_configs_start_in_english(make_app):
    assert DEFAULTS["language"] == "en"
    assert make_app(fresh=True).cfg["language"] == "en"
    assert Config.load({"work_minutes": 30})["language"] == "en"


def test_an_unknown_language_is_refused_on_save(app):
    with pytest.raises(constants.AppError, match=r"Pick a language from the list."):
        app.save_settings({"language": "fr"})
    assert app.cfg["language"] == "en"


def test_the_refusal_is_in_the_app_language(make_app):
    app = make_app({"language": "pt-BR"})
    with pytest.raises(constants.AppError, match=r"Escolha um idioma da lista."):
        app.save_settings({"language": "xx"})


def test_an_unknown_language_falls_back_to_english_on_load(caplog):
    assert Config.load({"language": "klingon"})["language"] == "en"
    assert "config: language was invalid" in caplog.text


def test_settings_list_each_language_in_its_own_name(app):
    assert Api(app).get_settings()["languages"] == [
        {"code": "en", "name": "English"},
        {"code": "pt-BR", "name": "Português (Brasil)"},
    ]


def test_the_pages_get_the_active_catalog_and_english(app):
    api = Api(app)
    assert api.i18n()["lang"] == "en"
    app.save_settings({"language": "pt-BR"})
    got = api.i18n()
    assert got["lang"] == "pt-BR"
    assert got["catalog"]["summary"]["title_done"] == "Pronto. Respire um pouco."
    assert got["english"]["summary"]["title_done"] == "Done. Take a breather."


def test_a_new_language_reloads_the_windows(app, monkeypatch):
    reloads = []
    monkeypatch.setattr(app, "reload_windows", lambda: reloads.append(app.cfg["language"]))
    app.save_settings({"language": "en", "work_minutes": 30})
    assert reloads == []  # nothing to reload for
    app.save_settings({"language": "pt-BR"})
    assert reloads == ["pt-BR"]
