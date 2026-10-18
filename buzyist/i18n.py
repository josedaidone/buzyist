"""
The app language: the catalogs in ui/i18n/ (one <code>.json per language,
shared with the pages) and t(), which looks a key up in the active language,
then in English, then gives the key itself.

A catalog nests its keys by area ({"summary": {"title_done": ...}} is the
key "summary.title_done"); keys starting with "_" are notes. A value is a
text with named placeholders ("{n} min"), or a plural entry: one text per
plural category the language needs ({"one": ..., "other": ...}), picked by
the count in `n`. ui/i18n.js is the pages' copy of this contract, and
tests/i18n_cases.json checks that the two agree.

The language is read from the settings at call time: App hands its
ConfigStore to use_config(); before that (and in tests without an App) the
text is English.
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache

from buzyist import paths
from buzyist.constants import APP, AppError

log = logging.getLogger(APP)

ENGLISH = "en"
# CLDR plural categories, and for each base language the ones its texts need
# with the rule that picks one (the integer and decimal rules of CLDR, which
# Intl.PluralRules applies in the pages). A new language adds its line here.
CATEGORIES = ("zero", "one", "two", "few", "many", "other")
PLURALS = {
    "en": (("one", "other"), lambda n: "one" if n == 1 else "other"),
    "pt": (("one", "other"), lambda n: "one" if 0 <= abs(n) < 2 else "other"),  # i = 0..1
}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")

_source = {"config": None}  # App's ConfigStore, where "language" is read (use_config)


def use_config(cfg) -> None:
    """Read the app language from `cfg` (anything with .get) from now on."""
    _source["config"] = cfg


def is_plural(value) -> bool:
    """A plural entry: an object of texts keyed by plural categories, "other" among them."""
    return (
        isinstance(value, dict)
        and "other" in value
        and all(k in CATEGORIES and isinstance(v, str) for k, v in value.items())
    )


def flatten(tree: dict, prefix: str = "") -> dict:
    """A catalog file's nested groups as {"area.key": text or plural entry}."""
    out = {}
    for key, value in tree.items():
        if key.startswith("_"):
            continue
        name = prefix + key
        if isinstance(value, dict) and not is_plural(value):
            out |= flatten(value, name + ".")
        else:
            out[name] = value
    return out


@cache
def raw_catalog(lang: str) -> dict:
    """A catalog file as it is (what the pages get); {} when there is none."""
    path = paths.I18N_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.exception("i18n: can't read %s", path)
        return {}
    return data if isinstance(data, dict) else {}


@cache
def catalog(lang: str) -> dict:
    """A language's keys, flattened."""
    return flatten(raw_catalog(lang))


def languages() -> dict[str, str]:
    """The languages shipped, code -> its name in that language, English first."""
    codes = sorted(p.stem for p in paths.I18N_DIR.glob("*.json"))
    codes.sort(key=lambda code: code != ENGLISH)
    return {code: str(catalog(code).get("language.name") or code) for code in codes}


def language() -> str:
    """The app language now: the setting, English when it isn't a shipped one."""
    cfg = _source["config"]
    lang = str((cfg.get("language") if cfg is not None else None) or ENGLISH)
    return lang if lang == ENGLISH or catalog(lang) else ENGLISH


def plural_rule(lang: str):
    """(categories, rule) of a language, by its base ("pt-BR" -> "pt"); English's for an unknown one."""
    return PLURALS.get(lang.split("-", maxsplit=1)[0].lower(), PLURALS[ENGLISH])


def _pick(entry, lang: str, values: dict) -> str | None:
    if isinstance(entry, str):
        return entry
    if not is_plural(entry):
        return None
    n = values.get("n")
    category = plural_rule(lang)[1](n) if isinstance(n, int | float) and not isinstance(n, bool) else "other"
    return entry.get(category) or entry["other"]


def translate(lang: str, key: str, values: dict | None = None) -> str:
    """`key`'s text in `lang`, else in English, else the key; placeholders filled from `values`."""
    values = values or {}
    text = _pick(catalog(lang).get(key), lang, values)
    if text is None:
        text = _pick(catalog(ENGLISH).get(key), ENGLISH, values)
    if text is None:
        return key
    return _PLACEHOLDER.sub(lambda m: str(values[m[1]]) if m[1] in values else m[0], text)


def t(key: str, /, **values) -> str:
    """`key` in the app language: t("summary.result", n=3, duration="1h 15m")."""
    return translate(language(), key, values)


def app_error(key: str, /, cls=AppError, **values) -> AppError:
    """
    An AppError (or `cls`) saying `key` in the app language, and in English
    as its `english`, for the log (values that are AppErrors give theirs).
    """
    english = {k: getattr(v, "english", v) if isinstance(v, AppError) else v for k, v in values.items()}
    return cls(t(key, **values), translate(ENGLISH, key, english))


def english(err: BaseException) -> str:
    """What an exception says, in English when it is an AppError: for log lines."""
    return getattr(err, "english", None) or str(err)
