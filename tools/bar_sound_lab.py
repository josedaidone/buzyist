"""
A page to try sounds on the BUSY Bar by hand: the bar's own sounds, copies of
them, tones made here, repeats, volume, and the timer states they play in.

    .venv\\Scripts\\python.exe tools\\bar_sound_lab.py          (opens the page)
    .venv\\Scripts\\python.exe tools\\bar_sound_lab.py --port 47699 --no-browser

It reads the bar's address and PIN from buZyist's config.json and talks to
the bar through buzyist.bar.Bar (Wi-Fi, then USB), so the PIN stays in this
process: the page only talks to 127.0.0.1. Nothing is saved on the PC; files
uploaded to the bar go under /ext/user_assets/<app name>/ and can be deleted
from the page. Tones are made by buzyist.bar_sound.tone, the code the app's
reminder chime (bar_sound.CHIME) comes from, so a spec that sounds right
here can become the chime as it is.

The timer buttons write real snapshots: Start and Stop replace whatever runs
on the bar (a buZyist session there then ends as "stopped"); Pause and Resume
are the same edits buZyist makes, which it follows.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import logging
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busylib import exceptions, types  # noqa: E402  (after the sys.path line)
from busylib.features import timer_state  # noqa: E402

from buzyist import paths  # noqa: E402
from buzyist.bar import Bar  # noqa: E402
from buzyist.bar_sound import tone  # noqa: E402
from buzyist.secrets_dpapi import unprotect_secrets  # noqa: E402
from buzyist.storage import read_json  # noqa: E402

PAGE = Path(__file__).with_name("bar_sound_lab.html")
APPS_ASSETS = "/ext/apps_assets"
USER_ASSETS = "/ext/user_assets"
DEFAULT_APP = "buzyist"

# busylib logs every refused request; listing folders without sounds/ makes plenty.
logging.getLogger("busylib").setLevel(logging.CRITICAL)


def load_bar() -> Bar:
    cfg = read_json(paths.CONFIG_PATH, {}) or {}
    unprotect_secrets(cfg)
    if not cfg.get("busybar_ip") and not cfg.get("usb_fallback"):
        sys.exit("No bar address in buZyist's settings (and USB is off).")
    return Bar(
        {
            "use_busybar": True,
            "busybar_ip": cfg.get("busybar_ip", ""),
            "busybar_pin": cfg.get("busybar_pin", ""),
            "usb_fallback": cfg.get("usb_fallback", True),
        }
    )


BAR = load_bar()


def clean_app(name) -> str:
    name = str(name or DEFAULT_APP).strip()
    if not name or not all(c.isalnum() or c in "_-" for c in name):
        raise ValueError("app name: letters, digits, _ and - only")
    return name


def clean_file(name) -> str:
    name = str(name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("file name: no folders")
    return name if name.endswith(".snd") else name + ".snd"


def api_error(err: exceptions.BusyBarAPIError) -> dict:
    return {"ok": False, "status": err.status_code, "error": str(err).split(" | ")[0]}


# ----------------------------------------------------------------- timer ---


def timer_info(snap: types.BusySnapshot) -> dict:
    variant = snap.snapshot
    kind = getattr(variant, "type", type(variant).__name__)
    info = {"kind": str(kind), "stamp": snap.snapshot_timestamp_ms}
    if isinstance(variant, types.BusySnapshotNotStarted):
        info["state"] = "idle"
        return info
    st = timer_state(snap)
    info.update(
        paused=bool(st.is_paused),
        finished=bool(st.is_finished),
        interval=st.interval,
        left_ms=st.time_left_ms,
        theme=variant.busy_bar_settings.theme,
    )
    if st.is_finished:
        info["state"] = "finished"
    elif st.is_paused:
        info["state"] = "paused"
    elif st.interval is not None:
        info["state"] = "rest" if st.interval % 2 else "work"
    else:
        info["state"] = "running"
    return info


def snapshot() -> dict:
    return timer_info(BAR.snapshot())


def session_file() -> dict | None:
    """buZyist's running session, if it has one (the timer buttons would end it)."""
    data = read_json(paths.SESSION_PATH, None)
    if not isinstance(data, dict) or not data.get("stamp"):
        return None
    task = data.get("task") or {}
    return {"stamp": data["stamp"], "task": task.get("content") or "", "call": bool(data.get("call"))}


# ----------------------------------------------------------------- sound ---


def play(source: str, file: str, app: str) -> dict:
    """One play request, with the timer read before and after."""
    before = snapshot()

    def go(bar):
        kwargs = {"stock_path": file} if source == "stock" else {"path": file}
        try:
            result = bar.audio_play(**kwargs, application_name=app)
            return {"ok": True, "result": result.result}
        except exceptions.BusyBarAPIError as err:
            return api_error(err)

    started = time.perf_counter()
    answer = BAR.run(go)
    answer["ms"] = round((time.perf_counter() - started) * 1000)
    answer["before"] = before
    answer["after"] = snapshot()
    answer["via"] = BAR.via
    return answer


def stop_audio() -> dict:
    def go(bar):
        try:
            return {"ok": True, "result": bar.audio_stop().result}
        except exceptions.BusyBarAPIError as err:
            return api_error(err)

    return BAR.run(go)


def volume(value=None) -> dict:
    def go(bar):
        if value is not None:
            bar.audio_volume_set(float(value))
        return {"ok": True, "volume": bar.audio_volume().volume}

    return BAR.run(go)


def listing(path: str) -> list:
    def go(bar):
        try:
            return bar.storage_list(path).list
        except exceptions.BusyBarAPIError:
            return []

    return BAR.run(go)


def sounds(app: str) -> dict:
    """Every .snd under the apps' assets and under our user folder."""
    found = []
    for entry in listing(APPS_ASSETS):
        if entry.type != "dir":
            continue
        for item in listing(f"{APPS_ASSETS}/{entry.name}/sounds"):
            if item.type == "file" and item.name.endswith(".snd"):
                found.append(
                    {
                        "full": f"{APPS_ASSETS}/{entry.name}/sounds/{item.name}",
                        "stock": f"{entry.name}/sounds/{item.name}",
                        "folder": entry.name,
                        "name": item.name,
                        "size": item.size,
                    }
                )
    own = [
        {"full": f"{USER_ASSETS}/{app}/{item.name}", "name": item.name, "size": item.size}
        for item in listing(f"{USER_ASSETS}/{app}")
        if item.type == "file"
    ]
    return {"ok": True, "stock": found, "own": own}


def read_file(full: str) -> bytes:
    if not (full.startswith(APPS_ASSETS + "/") or full.startswith(USER_ASSETS + "/")) or ".." in full:
        raise ValueError("only files under /ext/apps_assets or /ext/user_assets")
    return BAR.run(lambda bar: bar.storage_read(full))


def write_file(app: str, name: str, data: bytes) -> str:
    full = f"{USER_ASSETS}/{app}/{name}"

    def go(bar):
        # Raw, like buZyist's pictures: storage_write may convert what it sends.
        bar._request("POST", "/api/storage/write", params={"path": full}, data=data)

    BAR.run(go)
    return full


def delete_file(full: str) -> dict:
    if not full.startswith(USER_ASSETS + "/") or ".." in full:
        raise ValueError("only files under /ext/user_assets can be deleted here")

    def go(bar):
        try:
            return {"ok": True, "result": bar.storage_remove(full).result}
        except exceptions.BusyBarAPIError as err:
            return api_error(err)

    return BAR.run(go)


def timer(action: str, work_s: int = 60, rest_s: int = 30) -> dict:
    if action == "start":
        BAR.start(int(work_s) * 1000, int(rest_s) * 1000, 2, True)
    elif action == "pause":
        BAR.set_paused(True)
    elif action == "resume":
        BAR.set_paused(False)
    elif action == "stop":
        BAR.stop()
    else:
        raise ValueError("unknown timer action")
    return {"ok": True, "timer": snapshot()}


# ---------------------------------------------------------------- server ---


def _state(body: dict, app: str) -> dict:
    return {"ok": True, "timer": snapshot(), "volume": volume()["volume"], "via": BAR.via, "session": session_file()}


def _play(body: dict, app: str) -> dict:
    source = body.get("source")
    if source not in {"stock", "path"}:
        raise ValueError("source: stock or path")
    return play(source, str(body.get("file") or ""), app)


def _copy(body: dict, app: str) -> dict:
    src = str(body.get("full") or "")
    name = clean_file(body.get("name") or src.rsplit("/", 1)[-1])
    return {"ok": True, "full": write_file(app, name, read_file(src)), "path": name}


def _read(body: dict, app: str) -> dict:
    data = read_file(str(body.get("full") or ""))
    return {"ok": True, "size": len(data), "data": base64.b64encode(data).decode("ascii")}


def _tone(body: dict, app: str) -> dict:
    data = tone(body)
    name = clean_file(body.get("name") or "lab_tone")
    return {"ok": True, "full": write_file(app, name, data), "path": name, "size": len(data)}


ACTIONS = {
    "state": _state,
    "sounds": lambda body, app: sounds(app),
    "play": _play,
    "stop_audio": lambda body, app: stop_audio(),
    "volume": lambda body, app: volume(body.get("volume")),
    "copy": _copy,
    "read": _read,
    "tone": _tone,
    "delete": lambda body, app: delete_file(str(body.get("full") or "")),
    "timer": lambda body, app: timer(str(body.get("action")), body.get("work_s", 60), body.get("rest_s", 30)),
}


def handle(action: str, body: dict) -> dict:
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    return ACTIONS[action](body, clean_app(body.get("app")))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet: the page has its own log
        pass

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def do_GET(self):
        if not self._local():
            return self._send(403, b"forbidden", "text/plain")
        if self.path in {"/", "/index.html"}:
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        # Same-origin only (the page sends this header; a web page can't without a preflight).
        if not self._local() or self.headers.get("X-Lab") != "1" or not self.path.startswith("/api/"):
            return self._send(403, b"forbidden", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            answer = handle(self.path[len("/api/") :], body)
        except Exception as err:
            answer = {"ok": False, "error": f"{type(err).__name__}: {err}"}
        self._send(200, json.dumps(answer).encode(), "application/json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=47699)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Bar sound lab on {url} (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


if __name__ == "__main__":
    main()
