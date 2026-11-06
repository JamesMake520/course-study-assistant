"""Windows entry point; package with PyInstaller for Python-free use."""
import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from course_assistant.engine import Engine
from course_assistant.server import LocalServer


def main():
    parser = argparse.ArgumentParser(description="知行 · 课程助手")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--headless", action="store_true", help="仅用于本地测试")
    args = parser.parse_args()
    runtime = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "CourseStudyAssistant"
    runtime.mkdir(parents=True, exist_ok=True)
    lock = open(runtime / "instance.lock", "a+b")
    lock.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        info = runtime / "instance.json"
        if info.exists() and not args.no_open:
            from urllib.parse import urlsplit
            url = json.loads(info.read_text()).get("url", "")
            u = urlsplit(url)
            if u.scheme == "http" and u.hostname == "127.0.0.1":
                webbrowser.open(url)
        lock.close()
        return
    engine = Engine(runtime)
    preferred_port = args.port
    if not preferred_port:
        try:
            from urllib.parse import urlsplit
            previous = urlsplit(json.loads((runtime / "instance.json").read_text()).get("url", ""))
            if previous.hostname == "127.0.0.1" and previous.scheme == "http":
                preferred_port = previous.port or 0
        except (OSError, ValueError):
            pass
    try:
        server = LocalServer(engine, preferred_port)
    except OSError:
        if args.port:
            raise
        server = LocalServer(engine, 0)
    try:
        engine.attach(server.origin + "/demo/course", headless=args.headless)
        (runtime / "instance.json").write_text(json.dumps({"url": server.origin, "pid": os.getpid()}), encoding="utf-8")
        if not args.no_open:
            webbrowser.open(server.origin)
        if sys.stdout:
            print("知行课程助手：" + server.origin, flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()
        server.server_close()
        lock.close()


if __name__ == "__main__":
    main()
