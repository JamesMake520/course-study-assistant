"""Real Edge integration tests against the local demo, never the live site."""
import functools
import http.server
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

from course_assistant.browser import BrowserService, beijing_today, draft_fingerprint, normalized, validate_real_url


class BrowserPureTests(unittest.TestCase):
    def test_real_url_boundary(self):
        self.assertEqual(validate_real_url("https://study.zhihuishu.com/course"), "https://study.zhihuishu.com/course")
        for url in ("http://zhihuishu.com/", "https://zhihuishu.com.evil.test/", "https://evil.test/?zhihuishu.com", "file:///tmp/x", "https://user:secret@zhihuishu.com/", "https://zhihuishu.com:444/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_real_url(url)

    def test_duplicate_normalization_and_target(self):
        self.assertEqual(normalized("Ａ B\nC"), "ABC")
        one = {"kind": "answer", "body": "A B", "target_url": "https://example.test/1"}
        self.assertEqual(draft_fingerprint(one), draft_fingerprint({**one, "body": "AB", "id": "other"}))
        self.assertNotEqual(draft_fingerprint(one), draft_fingerprint({**one, "target_url": "https://example.test/2"}))

    def test_verification_requires_kind_date_and_answer_target(self):
        draft = {"kind": "answer", "body": "内容", "date": beijing_today(), "target_url": "target"}
        record = {"kind": "answer", "text": "内容", "date": beijing_today(), "target_url": "other"}
        self.assertIsNone(BrowserService._find_record(draft, {"records": [record]}))
        record["target_url"] = "target"
        self.assertEqual(BrowserService._find_record(draft, {"records": [record]}), record)
        record["date"] = "2000-01-01"
        self.assertIsNone(BrowserService._find_record(draft, {"records": [record]}))

    def test_attempt_scope_isolation(self):
        service = BrowserService.__new__(BrowserService)
        service._mode, service._configured_url, service._qa_url = "zhihuishu", "https://fixture.zhihuishu.com/course-a", "https://fixture.zhihuishu.com/qa-a"
        draft = {"kind": "question", "body": "同正文"}
        first = service._attempt_key(draft)
        service._configured_url = "https://fixture.zhihuishu.com/course-b"
        self.assertNotEqual(first, service._attempt_key(draft))
        self.assertNotEqual(service._attempt_key({**draft, "scope": "a"}), service._attempt_key({**draft, "scope": "b"}))


class QuietDemoHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/demo/course":
            self.path = "/demo.html"
        elif self.path.startswith("/static/"):
            self.path = self.path[len("/static"):]
        return super().do_GET()

    def log_message(self, *args):
        pass


class BrowserDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        static = Path(__file__).resolve().parents[1] / "course_assistant" / "static"
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietDemoHandler, directory=str(static)))
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.demo_url = f"http://127.0.0.1:{cls.httpd.server_port}/demo/course"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="course-browser-test-")
        self.browser = BrowserService(Path(self.folder.name), self.demo_url, headless=True)
        result = self.browser.perform("open", {"mode": "demo"})
        self.assertNotIn("error", result, result.get("error"))

    def tearDown(self):
        self.browser.close()
        self.folder.cleanup()

    def test_publish_verify_deduplicate_and_restart(self):
        qa = self.browser.perform("qa_sync")["qa"]
        self.assertTrue(qa["known"])
        self.assertEqual((qa["questions"], qa["answers"]), (0, 0))
        question = {"id": "test-question", "kind": "question", "title": "演示验收", "body": "如何把学习内容用自己的话复述？", "target_url": "", "date": beijing_today()}
        first = self.browser.perform("publish", {"draft": question})
        self.assertNotIn("error", first)
        self.assertTrue(first["published"]["verified"], first)
        self.assertTrue(self.browser.perform("publish", {"draft": question})["published"]["verified"])
        self.assertEqual(self.browser.perform("qa_sync")["qa"]["questions"], 1)
        answer = {"id": "test-answer", "kind": "answer", "title": "演示回答", "body": "合上课本复述，再对照内容查漏补缺。", "target_url": qa["targets"][0]["url"], "date": beijing_today()}
        self.assertTrue(self.browser.perform("publish", {"draft": answer})["published"]["verified"])
        self.assertTrue(self.browser.perform("verify", {"draft": answer})["published"]["verified"])
        self.browser.close()
        self.browser = BrowserService(Path(self.folder.name), self.demo_url, headless=True)
        self.assertNotIn("error", self.browser.perform("open", {"mode": "demo"}))
        qa = self.browser.perform("qa_sync")["qa"]
        self.assertEqual((qa["questions"], qa["answers"]), (1, 1))
        self.assertTrue(self.browser.perform("publish", {"draft": question})["published"]["verified"])
        self.assertEqual(self.browser.perform("qa_sync")["qa"]["questions"], 1)

    def test_real_video_completion_and_pause(self):
        result = self.browser.perform("video_start")
        self.assertNotIn("error", result)
        time.sleep(1)
        paused = self.browser.perform("pause")
        self.assertFalse(paused["browser"]["running"])
        self.assertTrue(all(row["status"] != "verified" for row in self.browser.perform("scan")["course"]["lessons"]))
        self.browser.perform("video_start")
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            state = self.browser.status()
            if state["browser"]["status"] == "completed":
                break
            time.sleep(0.3)
        self.assertEqual(state["browser"]["status"], "completed", state)
        self.assertEqual([row["progress"] for row in state["course"]["lessons"]], [100, 100])
        self.assertTrue(all(row["status"] == "verified" for row in state["course"]["lessons"]))
        self.browser.perform("open", {"mode": "demo"})
        self.assertTrue(all(row["status"] == "verified" for row in self.browser.status()["course"]["lessons"]))

    def test_attempt_guard_and_old_date(self):
        draft = {"id": "guard", "kind": "question", "title": "", "body": "一次尝试保护", "date": "2000-01-01"}
        self.assertIn("error", self.browser.perform("publish", {"draft": draft}))
        draft["date"] = beijing_today()
        # Preexisting attempt ledger represents a process lost after a click.
        self.browser.close()
        import json
        (Path(self.folder.name) / "assistant-publish-attempts.json").write_text(json.dumps({"demo:" + draft_fingerprint(draft): {"id": "guard"}}), encoding="utf-8")
        self.browser = BrowserService(Path(self.folder.name), self.demo_url, headless=True)
        self.browser.perform("open", {"mode": "demo"})
        result = self.browser.perform("publish", {"draft": draft})
        self.assertFalse(result["published"]["verified"])
        self.assertEqual(self.browser.perform("qa_sync")["qa"]["questions"], 0)


FIXTURE_ORIGIN = "https://fixture.zhihuishu.com"
FIXTURE_QA = """<!doctype html><meta charset="utf-8"><title>离线合成课程问答</title>
<h1>课程问答</h1><button>我参与的</button><button id="questions" class="active">我的提问</button><button id="answers">我的回答</button>
<div id="records"></div><script>
function draw(kind){document.querySelector('#questions').className=kind==='question'?'active':'';document.querySelector('#answers').className=kind==='answer'?'active':'';
 const records=JSON.parse(localStorage.getItem('offline-records')||'[]').filter(r=>r.kind===kind),container=document.querySelector('#records');container.replaceChildren();
 if(!records.length){container.textContent=kind==='answer'?'暂无回答':'暂无提问';return;}
 for(const r of records){const article=document.createElement('article'),p=document.createElement('p'),link=document.createElement('a'),date=document.createElement('time');p.textContent=r.body;link.href=r.target;link.textContent='受控离线问题';date.textContent='发表于'+r.date;article.append(p,link,date);container.append(article);}}
document.querySelector('#questions').onclick=()=>draw('question');document.querySelector('#answers').onclick=()=>draw('answer');draw('question');</script>"""
FIXTURE_QUESTION = """<!doctype html><meta charset="utf-8"><title>受控离线问题</title><h1>课程问答</h1><h2>受控离线问题</h2><nav><a href="/qa?courseId=fixture-a">返回本课程问答</a></nav>
<p>这是一份本地拦截的合成网页，所有请求由测试直接返回，绝不访问真实平台。</p><button id="open">我要回答</button>
<form id="answer-form" hidden><textarea placeholder="请输入回答内容"></textarea><button type="submit">发布回答</button></form><div id="answers"></div>
<script>document.querySelector('#open').onclick=()=>document.querySelector('form').hidden=false;
function draw(){const container=document.querySelector('#answers');container.replaceChildren();for(const r of JSON.parse(localStorage.getItem('offline-records')||'[]')){const article=document.createElement('article'),p=document.createElement('p'),date=document.createElement('time');p.textContent=r.body;date.textContent='发表于'+r.date;article.append(p,date);container.append(article);}}
document.querySelector('form').onsubmit=e=>{e.preventDefault();const records=JSON.parse(localStorage.getItem('offline-records')||'[]');records.push({kind:'answer',body:document.querySelector('textarea').value,target:location.href,date:new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'})});localStorage.setItem('offline-records',JSON.stringify(records));document.querySelector('form').hidden=true;draw();};draw();</script>"""

FIXTURE_PAGED_QA = """<!doctype html><meta charset="utf-8"><title>离线合成分页</title><h1>课程问答</h1>
<button>我参与的</button><button id="questions">我的提问</button><button id="answers">我的回答</button><div id="records"></div>
<div class="pagination"><button id="previous">上一页</button><span aria-current="page" id="current">1</span><button id="next">下一页</button></div>
<script>let kind='question',pageIndex=0;function draw(){document.querySelector('#questions').className=kind==='question'?'active':'';document.querySelector('#answers').className=kind==='answer'?'active':'';
const records=JSON.parse(localStorage.getItem('offline-records')||'[]').filter(r=>r.kind===kind),container=document.querySelector('#records');container.replaceChildren();document.querySelector('#previous').disabled=pageIndex===0;document.querySelector('#next').disabled=pageIndex+1>=records.length;document.querySelector('#current').textContent=String(pageIndex+1);
if(!records.length){container.textContent=kind==='answer'?'暂无回答':'暂无提问';return;}const r=records[pageIndex],article=document.createElement('article'),p=document.createElement('p'),link=document.createElement('a'),date=document.createElement('time');p.textContent=r.body;link.href=r.target;link.textContent='受控离线问题';date.textContent='发表于'+r.date;article.append(p,link,date);container.append(article);}
document.querySelector('#questions').onclick=()=>{kind='question';pageIndex=0;draw();};document.querySelector('#answers').onclick=()=>{kind='answer';pageIndex=0;draw();};document.querySelector('#previous').onclick=()=>{pageIndex--;draw();};document.querySelector('#next').onclick=()=>{pageIndex++;draw();};draw();</script>"""


class OfflineFixtureBrowser(BrowserService):
    """Routes synthetic HTTPS pages locally before any real-mode navigation."""
    def _open(self, payload):
        super()._open({"mode": "demo"})
        def route_request(route):
            url = route.request.url
            html = FIXTURE_QA if urlparse(url).path == "/qa" else FIXTURE_QUESTION if "/question/" in url else '<title>受控离线课程</title><h1>受控离线课程</h1><nav>课程问答 课程资料 目录</nav>'
            if urlparse(url).path == "/qa" and getattr(self, "paged", False):
                html = FIXTURE_PAGED_QA
            if url.endswith("/question/no-display"):
                html += '<style>#answers { display:none }</style>'
            elif url.endswith("/question/ambiguous"):
                html = html.replace('</form>', '<textarea placeholder="另一回答框"></textarea></form>')
            elif url.endswith("/question/other-course"):
                html = html.replace('courseId=fixture-a', 'courseId=fixture-b')
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        self._context.route(FIXTURE_ORIGIN + "/**", route_request)
        self._mode = "zhihuishu"
        self._configured_url = FIXTURE_ORIGIN + "/course?courseId=fixture-a"
        self._qa_url = FIXTURE_ORIGIN + "/qa?courseId=fixture-a"
        self._page.goto(self._configured_url)
        self._browser("ready", "受控离线DOM测试，不代表真实平台验收。")
        return self.status()

    def _dispatch(self, action, payload):
        if action == "offline_seed_paged":
            self.paged = True
            records = [{"kind": "answer", "body": f"第{index + 1}条离线分页记录正文。", "target": FIXTURE_ORIGIN + f"/question/page-{index}", "date": beijing_today()} for index in range(payload.get("count", 2))]
            self._page.evaluate("records=>localStorage.setItem('offline-records',JSON.stringify(records))", records)
            return self.status()
        if action == "offline_lesson_progress":
            self._page.set_content('<nav>课程问答 目录</nav><ul><li id="lesson-fixed"><a href="https://fixture.zhihuishu.com/video?lessonId=1">固定课程标题</a><span>00:06 学习进度：0%</span></li></ul>')
            first = self._scan()
            first_id = self.status()["course"]["lessons"][0]["id"]
            self._page.locator("#lesson-fixed span").evaluate("e=>e.textContent='00:06 学习进度：75%'")
            self._scan()
            return {"first_id": first_id, "lesson": self.status()["course"]["lessons"][0]}
        if action == "offline_new_tab":
            self._context.new_page().goto(FIXTURE_ORIGIN + "/course-new")
            return self.status()
        if action == "offline_ambiguous_tabs":
            # Synthetic visibility makes ambiguity deterministic in headless Edge.
            self._context.new_page().goto(FIXTURE_ORIGIN + "/course-new")
            for page in self._context.pages:
                page.evaluate("document.hasFocus=()=>false;Object.defineProperty(document,'visibilityState',{get:()=> 'visible',configurable:true})")
            return self.status()
        return super()._dispatch(action, payload)


class BrowserRealAdapterOfflineTests(BrowserDemoTests):
    # Reuse the local demo HTTP setup, not its demonstration test cases.
    test_publish_verify_deduplicate_and_restart = None
    test_real_video_completion_and_pause = None
    test_attempt_guard_and_old_date = None

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="course-offline-adapter-")
        self.browser = OfflineFixtureBrowser(Path(self.folder.name), self.demo_url, headless=True)
        self.assertNotIn("error", self.browser.perform("open", {}))

    def test_real_answer_conditional_path_and_detail_verification(self):
        initial = self.browser.perform("qa_sync")
        self.assertTrue(initial["qa"]["known"], initial)
        self.assertEqual(initial["qa"]["answers"], 0)
        draft = {"id": "offline-answer", "kind": "answer", "title": "", "body": "这是只在离线测试页面发布的回答。", "target_url": FIXTURE_ORIGIN + "/question/one", "date": beijing_today()}
        result = self.browser.perform("publish", {"draft": draft})
        self.assertNotIn("error", result, result)
        self.assertTrue(result["published"]["verified"], result)
        self.assertEqual(result["qa"]["answers"], 1)
        duplicate = self.browser.perform("publish", {"draft": draft})
        self.assertTrue(duplicate["published"]["verified"], duplicate)
        self.assertEqual(duplicate["qa"]["answers"], 1)

    def test_non_question_target_stops_before_attempt(self):
        draft = {"id": "bad-target", "kind": "answer", "body": "目标不是问题页，不应发送。", "target_url": FIXTURE_ORIGIN + "/course", "date": beijing_today()}
        result = self.browser.perform("publish", {"draft": draft})
        self.assertIn("error", result)
        self.assertFalse((Path(self.folder.name) / "assistant-publish-attempts.json").exists())

    def test_adopt_new_course_tab_clears_old_qa_binding(self):
        self.browser.perform("offline_new_tab")
        result = self.browser.perform("adopt_current", {"url": FIXTURE_ORIGIN + "/course-new"})
        self.assertNotIn("error", result, result)
        self.assertEqual(result["adopted"]["course_url"], FIXTURE_ORIGIN + "/course-new")
        self.assertEqual(result["browser"]["course_url"], FIXTURE_ORIGIN + "/course-new")
        self.assertEqual(result["browser"]["qa_url"], "")
        self.assertFalse(result["qa"]["known"])

    def test_personal_record_without_detail_stays_uncertain(self):
        draft = {"id": "no-display", "kind": "answer", "body": "有个人记录但详情未显示，应保持未知。", "target_url": FIXTURE_ORIGIN + "/question/no-display", "date": beijing_today()}
        result = self.browser.perform("publish", {"draft": draft})
        self.assertFalse(result["published"]["verified"], result)
        self.assertEqual(result["qa"]["answers"], 1)
        repeated = self.browser.perform("publish", {"draft": draft})
        self.assertFalse(repeated["published"]["verified"])
        self.assertEqual(repeated["qa"]["answers"], 1)

    def test_ambiguous_answer_editors_stop_before_attempt(self):
        draft = {"id": "ambiguous", "kind": "answer", "body": "两个回答框时不可猜测。", "target_url": FIXTURE_ORIGIN + "/question/ambiguous", "date": beijing_today()}
        result = self.browser.perform("publish", {"draft": draft})
        self.assertIn("error", result)
        self.assertFalse((Path(self.folder.name) / "assistant-publish-attempts.json").exists())

    def test_other_course_target_stops_before_attempt(self):
        draft = {"id": "other-course", "kind": "answer", "body": "不同课程目标题目不可发送。", "target_url": FIXTURE_ORIGIN + "/question/other-course", "date": beijing_today()}
        result = self.browser.perform("publish", {"draft": draft})
        self.assertIn("error", result)
        self.assertFalse((Path(self.folder.name) / "assistant-publish-attempts.json").exists())

    def test_ambiguous_course_tabs_require_explicit_selection(self):
        self.browser.perform("offline_ambiguous_tabs")
        result = self.browser.perform("adopt_current")
        self.assertIn("error", result)
        self.assertEqual(result["browser"]["course_url"], FIXTURE_ORIGIN + "/course?courseId=fixture-a")

    def test_paged_personal_records_are_read_and_deduplicated(self):
        self.browser.perform("offline_seed_paged", {"count": 2})
        result = self.browser.perform("qa_sync")
        self.assertTrue(result["qa"]["known"], result)
        self.assertEqual(result["qa"]["answers"], 2)
        repeated = self.browser.perform("qa_sync")
        self.assertEqual(repeated["qa"]["answers"], 2)

    def test_paging_limit_returns_unknown_not_partial_count(self):
        self.browser.perform("offline_seed_paged", {"count": 11})
        result = self.browser.perform("qa_sync")
        self.assertFalse(result["qa"]["known"])
        self.assertIsNone(result["qa"]["answers"])
        self.assertEqual(len(result["qa"]["records"]), 10)

    def test_lesson_identity_stays_stable_as_progress_changes(self):
        result = self.browser.perform("offline_lesson_progress")
        self.assertNotIn("error", result, result)
        self.assertEqual(result["first_id"], result["lesson"]["id"])
        self.assertEqual(result["lesson"]["progress"], 75)


if __name__ == "__main__":
    unittest.main()
