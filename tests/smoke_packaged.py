"""Run the shipped executable against only its local demo; no real platform/API."""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    args.runtime.mkdir(parents=True, exist_ok=True)
    command = [str(args.exe.resolve()), "--no-open", "--headless", "--data-dir", str(args.runtime.resolve())]
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    report = {"checks": [], "real_platform": "not_tested", "paid_ai": "not_tested"}

    def start():
        p = subprocess.Popen(command, startupinfo=startup, cwd=args.exe.resolve().parent)
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError("packaged process exited before readiness")
            try:
                info = json.loads((args.runtime / "instance.json").read_text())
                if info["pid"] == p.pid:
                    with urllib.request.urlopen(info["url"] + "/api/state", timeout=1) as r:
                        state = json.load(r)
                    return p, info["url"], state["csrf"]
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        p.terminate()
        raise RuntimeError("packaged startup timed out")

    p, origin, csrf = start()

    def action(name, **kwargs):
        req = urllib.request.Request(origin + "/api/action", data=json.dumps({"action": name, **kwargs}).encode(),
                                     headers={"Content-Type": "application/json", "X-CSRF-Token": csrf})
        with urllib.request.urlopen(req, timeout=90) as r:
            result = json.load(r)
        assert result["ok"], result
        return result

    def state():
        with urllib.request.urlopen(origin + "/api/state", timeout=5) as r:
            return json.load(r)

    try:
        report["checks"].append("executable_started_without_python_command")
        with urllib.request.urlopen(origin, timeout=5) as response:
            html = response.read().decode("utf-8")
        assets = re.findall(r'(?:src|href)="(/static/[^"?#]+\.(?:js|css))"', html)
        assert assets, "packaged UI must reference its local script and stylesheet assets"
        for asset in assets:
            with urllib.request.urlopen(origin + asset, timeout=5) as response:
                assert response.status == 200 and len(response.read()) > 0
        report["checks"].append("ui_scripts_and_styles_served")
        second = subprocess.run(command, startupinfo=startup, timeout=10)
        assert second.returncode == 0
        report["checks"].append("second_instance_exits_cleanly")
        parsed = action("grading_parse", text="每天播放25分钟，累计12天；每天提问1条，回答1条。", format="text")
        assert not parsed["state"]["grading"]["saved"]
        rules = parsed["result"]["draft"]
        assert rules["rules"]
        rules["course_name"] = "打包合成评分样例"
        imported = action("grading_save", rules=rules, reviewed=True, expected_revision="", scope=parsed["state"]["grading"]["scope"])
        grading_record = imported["state"]["grading"]
        assert grading_record["saved"] and grading_record["score"]["earned"] is None
        report["checks"].append("grading_import_confirmed_without_guessing_scores")
        try:
            action("grading_save", rules=rules, reviewed=True, expected_revision="", scope=grading_record["scope"])
            raise AssertionError("stale grading revision was accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 400
        report["checks"].append("stale_grading_update_rejected")
        action("browser_open")
        action("video_start")
        for _ in range(100):
            s = state()
            lessons = s["course"]["lessons"]
            if len(lessons) == 2 and all(x["status"] == "verified" for x in lessons):
                break
            time.sleep(.3)
        else:
            raise AssertionError("demo videos did not complete")
        report["checks"].append("two_normal_demo_videos_verified")
        action("qa_sync")
        created = action("draft_create", kind="question", title="打包验收", body="这是一条仅用于本机演示的验收提问。")
        id = created["result"]["id"]
        d = next(x for x in created["state"]["drafts"] if x["id"] == id)
        published = action("draft_publish", id=id, reviewed=True, expected_revision=d["revision"])
        assert next(x for x in published["state"]["drafts"] if x["id"] == id)["status"] == "verified"
        report["checks"].append("demo_publish_verified")
        try:
            action("draft_publish", id=id, reviewed=True, expected_revision=d["revision"])
            raise AssertionError("duplicate was accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 400
        report["checks"].append("duplicate_publish_rejected")
        target = state()["qa"]["targets"][0]["url"]
        created = action("draft_create", kind="answer", title="", body="先核验输入条件，再根据课程材料逐步推导。", target_url=target)
        answer_id = created["result"]["id"]
        answer = next(x for x in created["state"]["drafts"] if x["id"] == answer_id)
        result = action("draft_publish", id=answer_id, reviewed=True, expected_revision=answer["revision"])
        assert next(x for x in result["state"]["drafts"] if x["id"] == answer_id)["status"] == "verified"
        report["checks"].append("demo_answer_verified")
        action("report_export")
        action("app_exit")
        p.wait(timeout=30)
        p, origin, csrf = start()
        assert state()["grading"] == grading_record
        report["checks"].append("restart_preserves_confirmed_grading_rules")
        action("browser_open")
        refreshed = action("qa_sync")["state"]
        assert any(x["id"] == id and x["status"] == "verified" for x in refreshed["drafts"])
        assert refreshed["qa"]["questions"] == 1 and refreshed["qa"]["answers"] == 1
        assert all(x["status"] == "verified" for x in refreshed["course"]["lessons"])
        report["checks"].append("restart_preserves_demo_progress_records_and_drafts")
        action("app_exit")
        p.wait(timeout=30)
        report["checks"].append("graceful_exit")
        report["ok"] = True
    finally:
        if p.poll() is None:
            try:
                action("app_exit")
                p.wait(timeout=30)
            except Exception:
                p.terminate()
        (args.runtime / "packaged-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
