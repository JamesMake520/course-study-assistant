import copy
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from course_assistant import ai
from course_assistant.engine import Engine
from course_assistant.server import LocalServer
from course_assistant.storage import Store, today
from course_assistant.vault import Vault


class FakeBrowser:
    def __init__(self):
        self.posts = 0
        self.fail_after_send = False
        self.data = {"browser": {"connected": False, "running": False},
                     "course": {"name": "测试课程", "lessons": []},
                     "qa": {"known": True, "date": today(), "questions": 0, "answers": 0, "records": []}}

    def status(self):
        return copy.deepcopy(self.data)

    def perform(self, action, payload):
        if action == "open":
            self.data["browser"] = {"connected": True, "mode": payload["mode"], "course_url": payload["url"], "qa_url": payload["qa_url"]}
        if action == "publish":
            self.posts += 1
            if self.fail_after_send:
                raise TimeoutError("response lost")
            return {"published": {"verified": True, "url": "https://example.invalid/record/1"}, **self.status()}
        return self.status()

    def close(self):
        pass


@pytest.fixture
def engine(tmp_path):
    e = Engine(tmp_path, FakeBrowser())
    e.demo_url = "http://127.0.0.1:12345/demo/course"
    return e


def draft(e):
    result = e.action("draft_create", {"kind": "question", "title": "理解缓存", "body": "缓存容量与地址位数之间有什么关系？"})
    return e.store.draft(result["id"], e.scope)


def publish(e, d):
    return e.action("draft_publish", {"id": d["id"], "reviewed": True, "expected_revision": d["revision"]})


def test_first_configuration_before_open(engine):
    engine.action("settings", {"settings": {"mode": "zhihuishu", "course_url": "https://study.zhihuishu.com/course"}})
    assert engine.settings["mode"] == "zhihuishu"


def test_scope_change_cannot_use_previous_browser(engine):
    engine.action("browser_open", {})
    engine.action("settings", {"settings": {"mode": "zhihuishu", "course_url": "https://study.zhihuishu.com/course"}})
    d = draft(engine)
    with pytest.raises(ValueError, match="不一致"):
        publish(engine, d)
    assert engine.browser.posts == 0
    assert engine.state()["qa"]["known"] is False


def test_revision_prevents_unreviewed_publication(engine):
    engine.action("browser_open", {})
    d = draft(engine)
    engine.action("draft_save", {"id": d["id"], "title": "改变", "body": "另一个窗口修改后的内容。"})
    with pytest.raises(ValueError, match="其他页面修改"):
        publish(engine, d)
    assert engine.browser.posts == 0
    assert engine.store.draft(d["id"], engine.scope)["status"] == "draft"


def test_lost_response_never_retries_and_survives_restart(engine):
    engine.action("browser_open", {})
    engine.browser.fail_after_send = True
    d = draft(engine)
    with pytest.raises(ValueError, match="待确认"):
        publish(engine, d)
    assert engine.browser.posts == 1
    with pytest.raises(ValueError, match="重复发布"):
        publish(engine, d)
    reopened = Store(engine.store.path)
    assert reopened.draft(d["id"], engine.scope)["status"] == "uncertain"
    assert engine.browser.posts == 1


def test_unknown_or_full_daily_count_blocks(engine):
    engine.action("browser_open", {})
    d = draft(engine)
    engine.browser.data["qa"]["known"] = False
    with pytest.raises(ValueError, match="尚未核实"):
        publish(engine, d)
    engine.browser.data["qa"].update(known=True, questions=3)
    with pytest.raises(ValueError, match="达到目标"):
        publish(engine, d)
    assert engine.browser.posts == 0


def test_crash_during_submit_reopens_as_uncertain(engine):
    d = draft(engine)
    engine.store.claim(d["id"], engine.scope, d["revision"])
    restored = Store(engine.store.path)
    assert restored.draft(d["id"], engine.scope)["status"] == "uncertain"


def test_old_day_requires_new_review(engine):
    engine.action("browser_open", {})
    d = draft(engine)
    with engine.store.connect() as db:
        db.execute("UPDATE drafts SET date='2000-01-01' WHERE id=?", (d["id"],))
    with pytest.raises(ValueError, match="日期"):
        publish(engine, d)
    assert engine.browser.posts == 0


def test_second_click_and_duplicate_content_blocked(engine):
    engine.action("browser_open", {})
    d = draft(engine)
    publish(engine, d)
    with pytest.raises(ValueError):
        publish(engine, d)
    duplicate = draft(engine)
    with pytest.raises(ValueError, match="相同内容"):
        publish(engine, duplicate)
    assert engine.browser.posts == 1


def test_secret_controls_are_rejected_without_echo(tmp_path):
    secret = "sentinel_secret_left\nsentinel_secret_right"
    with pytest.raises(ValueError) as e:
        Vault(tmp_path / "key.dpapi").save(secret)
    assert "sentinel" not in str(e.value)
    with pytest.raises(ValueError) as e:
        ai.complete({"ai_base_url": "http://127.0.0.1:1", "ai_model": "test", "ai_protocol": "responses"}, secret, "test")
    assert "sentinel" not in str(e.value)


def test_windows_vault_is_encrypted(tmp_path):
    import os
    if os.name != "nt":
        pytest.skip("Windows DPAPI")
    v = Vault(tmp_path / "secret.dpapi")
    v.save("test-only-secret-value")
    assert b"test-only-secret-value" not in v.path.read_bytes()
    assert v.read() == "test-only-secret-value"


@pytest.fixture
def server(engine):
    s = LocalServer(engine)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def request(s, path, data=None, headers=None):
    h = headers or {}
    if data is not None:
        h = {"Content-Type": "application/json", **h}
    req = urllib.request.Request(s.origin + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def test_local_http_rejects_csrf_and_rebinding(server):
    for h in ({"Origin": "https://evil.invalid"}, {"Host": "evil.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
        with pytest.raises(urllib.error.HTTPError) as e:
            request(server, "/api/state", headers=h)
        assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        request(server, "/api/action", {"action": "draft_create", "body": "test"})
    assert e.value.code == 403
    state = request(server, "/api/state")
    assert request(server, "/api/action", {"action": "draft_create", "body": "本地保存"}, {"X-CSRF-Token": state["csrf"]})["ok"]


def test_busy_exit_is_rejected(server):
    server.engine.lock.acquire()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            request(server, "/api/action", {"action": "app_exit"}, {"X-CSRF-Token": server.csrf})
        assert e.value.code == 400
        assert not server.engine.stopping.is_set()
    finally:
        server.engine.lock.release()


def test_ai_protocol_and_redirect_safety():
    seen = []
    class Mock(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if self.path.startswith("/redirect"):
                self.send_response(307)
                self.send_header("Location", "/stolen")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}).encode())
    s = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    settings = {"ai_base_url": f"http://127.0.0.1:{s.server_port}", "ai_model": "mock-model", "ai_protocol": "responses"}
    try:
        assert ai.complete(settings, "test-only-secret", "hello", test=True) == "OK"
        assert seen[0][1]["store"] is False
        assert seen[0][0] == "/responses"
        settings["ai_base_url"] += "/redirect"
        with pytest.raises(ValueError):
            ai.complete(settings, "test-only-secret", "hello")
        assert len(seen) == 2
    finally:
        s.shutdown()
        s.server_close()
