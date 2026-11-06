"""End-to-end import UI checks with the real local engine and synthetic rules."""
import json
import threading

from playwright.sync_api import expect, sync_playwright

from course_assistant.engine import Engine
from course_assistant.server import LocalServer


def test_grading_import_roundtrip_and_review_guards(tmp_path):
    engine = Engine(tmp_path)
    server = LocalServer(engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(server.origin + "/") else route.abort())
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(server.origin + "/#grading")
            expect(page.locator("#grading-saved-summary")).to_contain_text("尚未保存")
            page.locator("#grading-source").fill("每天播放25分钟，累计12天；每天提问1条，回答1条。")
            page.locator("#grading-parse").click()
            expect(page.locator("#grading-form")).to_be_visible()
            assert page.locator(".grading-rule-card").count() >= 1
            assert not engine.state()["grading"]["saved"]
            page.locator("#grading-course-name").fill("合成评分测试课程")
            page.locator("#grading-total-score").fill("100")
            page.locator("#grading-target-score").fill("60")
            page.locator("#grading-reviewed").check()
            expect(page.locator("#grading-save")).to_be_enabled()
            page.locator("#grading-target-score").fill("61")
            expect(page.locator("#grading-reviewed")).not_to_be_checked()
            expect(page.locator("#grading-save")).to_be_disabled()
            page.wait_for_timeout(2200)
            expect(page.locator("#grading-target-score")).to_have_value("61")
            page.locator("#grading-reviewed").check()
            page.locator("#grading-save").click()
            expect(page.locator("#grading-saved-summary")).to_contain_text("合成评分测试课程")
            expect(page.locator("#grading-saved-score")).to_have_text("未知")
            saved = engine.state()["grading"]
            assert saved["rules"]["target_score"] == 61
            with page.expect_download() as download_info:
                page.locator("#grading-export").click()
            exported = download_info.value.path()
            content = json.loads(exported.read_text(encoding="utf-8"))
            assert content == saved["rules"]
            page.locator("#grading-file").set_input_files({"name": "rules.json", "mimeType": "application/json", "buffer": exported.read_bytes()})
            expect(page.locator("#grading-format")).to_have_value("json")
            page.locator("#grading-parse").click()
            expect(page.locator("#grading-import-note")).to_contain_text("规则草稿")
            page.locator("#grading-reviewed").check()
            page.locator("#grading-save").click()
            expect(page.locator("#grading-import-note")).to_contain_text("已保存")
            original = engine.state()["grading"]
            page.locator("#grading-format").select_option("json")
            page.locator("#grading-source").fill('{"rules":[NaN]}')
            page.locator("#grading-parse").click()
            expect(page.locator("#grading-import-note")).to_contain_text("解析未完成")
            assert engine.state()["grading"] == original
            page.on("dialog", lambda dialog: dialog.accept())
            page.locator("#grading-reload").click()
            expect(page.locator("#grading-format")).to_have_value("text")
            page.locator("#grading-course-name").fill("未保存编辑仍应保留")
            changed = json.loads(json.dumps(original["rules"]))
            changed["course_name"] = "其他窗口更新"
            engine.action("grading_save", {"rules": changed, "reviewed": True,
                          "scope": original["scope"], "expected_revision": original["revision"]})
            expect(page.locator("#grading-conflict")).to_be_visible(timeout=6000)
            expect(page.locator("#grading-course-name")).to_have_value("未保存编辑仍应保留")
            expect(page.locator("#grading-save")).to_be_disabled()
            page.locator("#grading-reload").click()
            expect(page.locator("#grading-course-name")).to_have_value("其他窗口更新")
            page.locator('[data-page="overview"]').click()
            expect(page.locator("#grading-overview-target")).to_have_text("61 分")
            expect(page.locator("#grading-overview-score")).to_have_text("未知")
            assert not errors, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        engine.close()
