---
name: ship
description: Finish a change in the buZyist repo and release it - checks, docs, version bump, commit, push, PR, CI, merge, "Build and release", cleanup. Use when Jose says "commit", "ship it", "new version", "release", "open a PR", "you know the drill", or asks to wrap up work on a branch. Without an explicit "release"/"new version"/"merge", stop after opening the PR.
---

# Ship a buZyist change

The repo's rules are in `CLAUDE.md` (read its **Release** bullets and `docs/RELEASING.md` if
anything here seems off). Merging a PR that bumps `__version__` into `main` publishes that version
to every installed copy within about 6 hours, so never merge untested work, and never bump or merge
unless Jose asked for a release ("new version", "release", "merge", "you know the drill").

Run everything from the repo root with the venv: `.\.venv\Scripts\python.exe` (bash:
`.venv/Scripts/python.exe`).

## 1. Before committing

1. **Branch**: work is on a feature branch, never `main`. If on `main`, create one
   (`git checkout -b <short-name>`). `git fetch origin` and check `git log HEAD..origin/main`: if
   `main` moved, merge it in and rerun the checks.
2. **Scratch files**: delete anything temporary (`.scratch/`, probe scripts) so ruff and the commit
   don't pick it up. Never commit `config.json`, `session.json`, `history.json` (gitignored
   personal data).
3. **Line endings**: the repo is LF. Python's `Path.write_text` on Windows writes CRLF. Check with
   `git diff --stat`: a file showing thousands of changed lines was rewritten with CRLF. Write
   with `write_bytes(... .encode())` or the edit tool.
4. **Checks** (all must pass; CI runs the same):
   ```bash
   .venv/Scripts/python.exe -m pytest -q
   .venv/Scripts/python.exe -m ruff check .
   .venv/Scripts/python.exe -m ruff format --check .
   for f in extension/*.js ui/*.js; do node --check "$f" || exit 1; done
   node --test "tests/js/**/*.test.mjs"
   ```
   The full pytest run takes about 4 minutes.
5. **What the change must carry**:
   - every new text people read in both `ui/i18n/en.json` and `ui/i18n/pt-BR.json` (same keys and
     placeholders; `GLOSSARY.md` terms); extension texts in `extension/i18n/`.
   - `extension/` changed → bump `version` in `extension/manifest.json` (CI fails the PR otherwise).
   - a new setting → `DEFAULTS` and a `Config` field in `buzyist/config.py`; a changed shape → a
     `MIGRATIONS` entry plus a fixture in `tests/data/configs/`.
   - a new runtime file outside `ui/` → `datas` in `packaging/busyist.spec`.
   - `CLAUDE.md`: a new module goes in the module table; a new feature gets its paragraph
     (what, where, what was found on real hardware, its tests).
   - `README.md` "Using it": a bullet when people will notice the feature.

## 2. Version (only for a release)

Find the latest tag: `git ls-remote --tags origin "v*"` (or `gh release view --json tagName`).
New feature → bump minor (2.11.0 → 2.12.0), fix only → patch (2.12.0 → 2.12.1). The new version
must be newer than the latest tag and not already tagged.

Edit only the line `__version__ = "x.y.z"` in `busyist.py` (keep the exact format: build.ps1, the
spec, Inno and CI read it by regex).

## 3. Commits

- The change itself first: a headline saying what it does for people, then a body explaining why
  and anything learned (hardware behaviour, firmware quirks). Write the message to a file and use
  `git commit -F <file>` (process substitution `<(...)` fails in this shell).
- The version bump as its own commit: `buZyist x.y.z: <one-line summary>`, containing only
  `busyist.py`.

## 4. Push and PR

```bash
git push -u origin <branch>
gh pr create --base main --head <branch> --title "x.y.z: <summary>" --body "<body>"
```

Body style (see `gh pr view 38`, `gh pr view 40`): a few `- ` bullets in plain words about what
changed for people (settings path in bold, e.g. **After a session** (Settings → BUSY Bar, off by
default)), what was tried on real hardware, and last `- Version x.y.z (merging publishes it).`

Without a release request, stop here and give Jose the PR link.

## 5. Merge and release

```bash
gh pr checks <n> --watch --interval 30      # wait for green; fix and push if red
gh pr merge <n> --merge                      # a merge commit, as for earlier PRs
gh run list --workflow release.yml --limit 1 # the "Build and release" run on main
gh run watch <run-id> --interval 30 --exit-status
```

The run has jobs `version`, `test`, `build`, `publish` (about 6 minutes). If it fails, read the
failed step (`gh run view <id> --log-failed`); no tag is created on failure, so a fix PR with the
same version releases it. If `version` says "already released", the bump was missing.

Then check the release:

```bash
gh release view --json tagName,assets -q '.tagName, (.assets[].name)'
```

It must be `vx.y.z` with `Buzyist-Setup-x.y.z.exe`, `Busyist-Setup-x.y.z.exe` (for copies from
before the rename), `Buzyist-x.y.z-portable.zip` and `SHA256SUMS.txt`; the in-app updater needs
those names.

## 6. Clean up

```bash
git checkout main && git pull
git branch -d <branch> && git push origin --delete <branch>
```

Tell Jose: the release link, what's in it, and that his copy runs from source, so he needs to
restart it (installed copies update themselves within about 6 hours).
