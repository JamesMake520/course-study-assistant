from __future__ import annotations
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

STATIC = Path(__file__).parent / "static"


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, engine, port=0):
        self.engine = engine
        self.csrf = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "CourseAssistant"

    def log_message(self, *args):
        pass

    def allowed(self):
        # Reject DNS rebinding and cross-site access before returning app state.
        expected = f"127.0.0.1:{self.server.server_port}"
        return (self.headers.get("Host") == expected
                and self.headers.get("Origin", self.server.origin) == self.server.origin
                and self.headers.get("Sec-Fetch-Site", "none") in {"none", "same-origin"})

    def send_data(self, code, data, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode()
        if isinstance(data, str):
            data = data.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        if not self.allowed():
            return self.send_data(403, {"error": "只接受本机同源访问。"})
        route = unquote(urlsplit(self.path).path)
        if route == "/api/state":
            return self.send_data(200, self.server.engine.state() | {"csrf": self.server.csrf})
        if route == "/health":
            return self.send_data(200, {"ok": True})
        if route == "/":
            path = STATIC / "index.html"
        elif route == "/demo/course":
            path = STATIC / "demo.html"
        elif route.startswith("/static/"):
            path = (STATIC / route[len("/static/"):]).resolve()
            if not path.is_relative_to(STATIC.resolve()):
                return self.send_data(404, {"error": "文件不存在。"})
        else:
            return self.send_data(404, {"error": "页面不存在。"})
        if not path.is_file():
            return self.send_data(404, {"error": "资源未准备好。"})
        data = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if mime.startswith("text/") or path.suffix == ".js":
            mime += "; charset=utf-8"
        # Standard media ranges allow normal HTML5 playback and seeking by users.
        range_header = self.headers.get("Range", "")
        if path.suffix == ".mp4" and range_header.startswith("bytes="):
            try:
                lo, hi = range_header[6:].split("-", 1)
                start, end = int(lo or 0), int(hi) if hi else len(data) - 1
                if start < 0 or end < start or start >= len(data):
                    raise ValueError()
                end = min(end, len(data) - 1)
            except ValueError:
                return self.send_data(416, b"", mime)
            return self.send_data(206, data[start:end + 1], mime, {"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"})
        self.send_data(200, data, mime)

    def do_POST(self):
        if not self.allowed() or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf):
            return self.send_data(403, {"ok": False, "error": "本机会话校验失败，请刷新页面。"})
        if urlsplit(self.path).path != "/api/action":
            return self.send_data(404, {"ok": False, "error": "接口不存在。"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 512_000 or self.headers.get_content_type() != "application/json":
                raise ValueError("请求格式或大小不符合要求。")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求必须为对象。")
            if length > 100_000 and payload.get("action") not in {"grading_parse", "grading_save"}:
                raise ValueError("请求大小不符合要求。")
            if payload.get("action") == "app_exit":
                if not self.server.engine.lock.acquire(blocking=False):
                    raise ValueError("操作正在进行，请等待结果返回后再退出。")
                self.server.engine.stopping.set()
                self.server.engine.lock.release()
                self.send_data(200, {"ok": True, "result": {"message": "助手正在退出，可以关闭此页面。"}})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            result = self.server.engine.action(payload.get("action", ""), payload)
            self.send_data(200, {"ok": True, "result": result, "state": self.server.engine.state() | {"csrf": self.server.csrf}})
        except (ValueError, KeyError, TypeError):
            import sys
            exc = sys.exc_info()[1]
            message = str(exc) if isinstance(exc, ValueError) else "缺少必要字段，请检查表单。"
            self.send_data(400, {"ok": False, "error": message[:700]})

    def do_OPTIONS(self):
        self.send_data(403, {"error": "不提供跨域接口。"})
