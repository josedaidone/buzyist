"""The Chrome extension's own catalogs (extension/i18n/<code>.json, #28): the same keys and placeholders as English."""

import json
from pathlib import Path

import pytest
from test_i18n import catalog_problems

from buzyist import extension, i18n, paths


def extension_catalogs() -> dict[str, dict]:
    folder = paths.EXTENSION_SRC / "i18n"
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))}


def test_the_extension_speaks_every_app_language():
    """buZyist sends the app language; the extension has a catalog for each one it can be."""
    assert sorted(extension_catalogs()) == sorted(i18n.languages())


@pytest.mark.parametrize("lang", sorted(extension_catalogs()))
def test_every_extension_catalog_matches_english(lang):
    files = extension_catalogs()
    assert catalog_problems(lang, files[lang], files["en"]) == []


def test_every_extension_value_is_a_text_or_a_plural_entry():
    for lang, tree in extension_catalogs().items():
        for key, value in i18n.flatten(tree).items():
            assert isinstance(value, str) or i18n.is_plural(value), f"{lang}: {key}"


def test_the_extension_catalogs_ship_with_it(tmp_path, monkeypatch):
    """The spec's datas take the whole extension folder, and sync_extension copies its subfolders."""
    spec = (Path(__file__).parent.parent / "packaging" / "busyist.spec").read_text(encoding="utf-8")
    assert '(str(ROOT / "extension"), "extension")' in spec
    monkeypatch.setattr(paths, "EXTENSION_DIR", tmp_path / "chrome-extension")
    extension.sync_extension()
    copied = sorted(p.name for p in (tmp_path / "chrome-extension" / "i18n").glob("*.json"))
    assert copied == [f"{lang}.json" for lang in sorted(extension_catalogs())]
    assert (tmp_path / "chrome-extension" / "i18n.js").is_file()
