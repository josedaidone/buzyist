"""
Home Assistant (home_assistant.py): what buZyist is doing, POSTed to the
webhook busybar-ha registers, when it changes and every heartbeat.

The link's `send` is replaced by a recorder (``sent``); post_json itself is
tried against a webhook on loopback.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from buzyist import constants
from buzyist.api import Api
from buzyist.constants import AppError
from buzyist.home_assistant import HomeAssistantLink, clean_ha_url, post_json, same

URL = "http://127.0.0.1:8123/api/webhook/abc123"
ZOOM = {"key": "zoom", "name": "Zoom"}


@pytest.fixture
def sent():
    """The payloads the link sent, in order; set ``sent.fail`` to an exception to make sends fail."""

    class Sent(list):
        fail: Exception | None = None

    return Sent()


@pytest.fixture
def ha(make_app, clock, deferred_threads, sent):
    """``ha(cfg=None)``: an app with Home Assistant on, its link sending to ``sent``, and ``app.step(seconds)``."""

    def send(url, payload, timeout=None):
        if sent.fail:
            raise sent.fail
        sent.append(payload)
        return {"ok": True, "name": "Desk PC"}

    def build(cfg=None):
        app = make_app({"use_todoist": False, "use_home_assistant": True, "ha_webhook_url": URL, **(cfg or {})})
        app.notify = lambda *a, **k: None
        app.home_assistant = HomeAssistantLink(app, send=send)

        def step(seconds=1):
            clock.advance(seconds=seconds)
            app.engine_step()
            deferred_threads.run_pending()
            app.home_assistant.step()
            return app

        app.step = step
        return app

    return build


def start(app, deferred_threads, name="Inbox", activity="email"):
    app.start_task(None, name, activity)
    deferred_threads.run_pending()
    app.step(0)


# ---------------------------------------------------------------- payload


def test_idle_goes_once_then_with_the_heartbeat(ha, sent):
    app = ha()
    app.step(0)
    assert [p["state"] for p in sent] == ["idle"]
    assert sent[0]["v"] == constants.HA_PROTOCOL and sent[0]["device"] == constants.DEVICE_NAME
    assert sent[0]["session"] is None and sent[0]["focus"] is False
    app.step(constants.HA_HEARTBEAT_S - 2)
    assert len(sent) == 1
    app.step(2)
    assert len(sent) == 2


def test_a_session_on_an_activity(ha, sent, deferred_threads, clock):
    app = ha()
    start(app, deferred_threads)
    last = sent[-1]
    assert last["state"] == "focus" and last["focus"] is True
    session = last["session"]
    assert session["kind"] == "pomodoro" and session["source"] == "here"
    assert session["activity"]["id"] == "email" and session["task"].startswith(session["activity"]["name"])
    assert (session["round"], session["rounds"]) == (1, app.cfg["cycles"])
    assert session["phase_minutes"] == app.cfg["work_minutes"]
    assert session["ends_ms"] == clock.ms + app.cfg["work_minutes"] * 60000
    count = len(sent)
    app.step(5)
    app.step(5)
    assert len(sent) == count  # the time left goes down; the end doesn't move


def test_pause_break_and_stop(ha, sent, deferred_threads):
    app = ha({"work_minutes": 5, "rest_minutes": 5, "cycles": 2})
    start(app, deferred_threads)
    app.control("pause")
    app.step()
    assert sent[-1]["state"] == "paused" and sent[-1]["focus"] is False
    assert sent[-1]["session"]["ends_at"] is None
    app.control("resume")
    app.step()
    assert sent[-1]["state"] == "focus"
    app.step(5 * 60)
    assert sent[-1]["state"] == "break" and sent[-1]["session"]["phase"] == "rest"
    app.control("stop")
    app.step()
    assert sent[-1]["state"] == "idle" and sent[-1]["session"] is None
    ended = sent[-1]["ended"]
    assert ended["reason"] == "stopped" and ended["kind"] == "pomodoro" and ended["pomodoros"] == 1
    assert ended["activity"]["id"] == "email" and ended["at"]
    assert sent[-1]["today"]["pomodoros"] == 1


def test_a_call(ha, sent, clock):
    app = ha({"call_detect": True})
    assert app.start_call([ZOOM], clock.ms)
    app.step()
    last = sent[-1]
    assert last["state"] == "call" and last["focus"] is True
    assert last["session"]["kind"] == "call" and last["session"]["apps"] == ["Zoom"]
    assert last["session"]["ends_at"] is None and last["session"]["started_at"]
    app.end_call()
    app.step()
    assert sent[-1]["state"] == "idle" and sent[-1]["ended"]["kind"] == "call"


def test_nothing_goes_while_off_or_without_a_url(ha, sent):
    app = ha({"use_home_assistant": False})
    app.step(0)
    app = ha({"ha_webhook_url": ""})
    app.step(0)
    assert sent == []
    assert app.home_assistant.view() == {"on": False, "last_ok_ms": 0, "error": ""}


def test_a_failed_send_waits_before_trying_again(ha, sent, clock):
    app = ha()
    sent.fail = AppError("down")
    app.step(0)
    assert app.home_assistant.view()["error"] == "down"
    sent.fail = None
    app.step(constants.HA_RETRY_S - 1)
    assert sent == []
    app.step(1)
    assert len(sent) == 1
    assert app.home_assistant.view() == {"on": True, "last_ok_ms": clock.ms, "error": ""}


def test_a_new_url_gets_everything_again(ha, sent):
    app = ha()
    app.step(0)
    app.save_settings({"ha_webhook_url": URL + "x"})
    app.step(0)
    assert len(sent) == 2


def test_quit_says_goodbye(ha, sent):
    app = ha()
    app.home_assistant.close()
    assert sent[-1]["closed"] is True


def test_payloads_that_say_the_same():
    a = {"state": "focus", "session": {"task": "x", "ends_at": "a", "ends_ms": 100_000}}
    assert same(a, {"state": "focus", "session": {"task": "x", "ends_at": "b", "ends_ms": 103_000}})
    assert not same(a, {"state": "focus", "session": {"task": "x", "ends_at": "b", "ends_ms": 106_000}})
    assert not same(a, {"state": "focus", "session": {"task": "x", "ends_at": None, "ends_ms": None}})
    assert not same(a, {"state": "break", "session": a["session"]})
    assert same({"state": "idle", "session": None}, {"state": "idle", "session": None})
    assert not same(a, None)


# ------------------------------------------------------------- settings


@pytest.mark.parametrize("url", ["", "  ", URL, "https://abc.ui.nabu.casa/api/webhook/x"])
def test_urls_kept(url):
    assert clean_ha_url(url) == url.strip()


@pytest.mark.parametrize("url", ["homeassistant.local/api/webhook/x", "ftp://ha/x", "http://", "http://h/" + "x" * 500])
def test_urls_refused(url):
    with pytest.raises(AppError):
        clean_ha_url(url)


def test_the_url_is_a_secret(ha):
    app = ha()
    settings = Api(app).get_settings()
    assert settings["ha_webhook_url"] == "" and settings["has_ha_webhook_url"]
    with pytest.raises(AppError):
        app.save_settings({"ha_webhook_url": "not a url"})
    app.save_settings({"ha_webhook_url": ""})  # empty keeps it
    assert app.cfg["ha_webhook_url"] == URL
    app.save_settings({"clear_ha_webhook_url": True})
    assert app.cfg["ha_webhook_url"] == ""


def test_send_now_takes_the_typed_url_or_the_saved_one(ha, sent):
    api = Api(ha())
    assert api.test_home_assistant("") == {"ok": True, "state": "idle", "name": "Desk PC"}
    assert api.test_home_assistant("nope")["ok"] is False
    assert len(sent) == 1


def test_the_state_carries_the_status_line(ha):
    assert ha().state()["home_assistant"] == {"on": True, "last_ok_ms": 0, "error": ""}


# ------------------------------------------------------------- the wire


@pytest.fixture
def webhook():
    """A webhook on loopback: ``webhook(status, body)`` sets its answer; ``.got`` holds what it received."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            server.got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(server.status)
            self.end_headers()
            self.wfile.write(server.body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.got, server.status, server.body = [], 200, b'{"ok": true, "name": "Desk PC"}'
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}/api/webhook/abc"
    yield server
    server.shutdown()
    server.server_close()


def test_post_json(webhook):
    assert post_json(webhook.url, {"state": "idle"}) == {"ok": True, "name": "Desk PC"}
    assert webhook.got == [{"state": "idle"}]


def test_an_unknown_webhook_answers_empty(webhook):
    webhook.body = b""  # what Home Assistant answers for a webhook nobody registered
    with pytest.raises(AppError, match="doesn't know"):
        post_json(webhook.url, {})


def test_a_refusal(webhook):
    webhook.status = 405
    with pytest.raises(AppError, match="405"):
        post_json(webhook.url, {})


def test_nobody_there():
    with pytest.raises(AppError, match="Can't reach"):
        post_json("http://127.0.0.1:9/api/webhook/x", {}, timeout=1)
