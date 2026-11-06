"""A dedicated, serial Playwright browser. No private platform API is used.

The real adapter deliberately treats unsupported DOM structures as unknown.
Only demo selectors are guaranteed; real-site completion needs platform text.
"""
from __future__ import annotations

import copy
import hashlib
import json
import queue
import re
import threading
import unicodedata
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


def beijing_today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def draft_fingerprint(draft: dict) -> str:
    fields = [draft.get("kind", ""), normalized(draft.get("body", ""))]
    if draft.get("kind") == "answer":
        fields.append(draft.get("target_url", ""))
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def validate_real_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "zhihuishu.com" or host.endswith(".zhihuishu.com")) or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("真实模式只接受 HTTPS 智慧树域名链接，请从课程页面复制地址。")
    return url


def course_identifiers(url: str) -> dict:
    parsed = urlparse(url)
    query_parts = [parsed.query]
    if "?" in parsed.fragment:
        query_parts.append(parsed.fragment.split("?", 1)[1])
    allowed = {"courseid", "recruitid", "recruitandcourseid", "classid", "teachingclassid"}
    result = {}
    for query in query_parts:
        for key, value in parse_qsl(query):
            key = key.lower().replace("_", "")
            if key in allowed and value:
                if key in result and result[key] != value:
                    raise ValueError("链接包含冲突的课程标识，无法确认归属。")
                result[key] = value
    return result


class BrowserService:
    def __init__(self, profile_dir: Path, demo_url: str, headless: bool = False):
        self.profile_dir = Path(profile_dir)
        self.demo_url = demo_url
        self.headless = headless
        self._lock = threading.Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._closed = False
        self._cache = {"browser": {"connected": False, "running": False, "status": "closed", "message": "尚未打开专用浏览器。", "url": ""}}
        self._context = self._playwright = self._page = self._qa_page = self._detail_page = None
        self._mode = "demo"
        self._qa_url = ""
        self._configured_url = ""
        self._running = False
        self._active_lesson = None
        self._real_selectors = {}
        self._attempts = {}
        self._attempt_file = self.profile_dir / "assistant-publish-attempts.json"
        self._thread = threading.Thread(target=self._run, name="course-browser", daemon=True)
        self._thread.start()

    def status(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._cache)

    def perform(self, action: str, payload: dict | None = None) -> dict:
        if self._closed:
            return {"error": "浏览器服务已关闭。", **self.status()}
        future = Future()
        self._jobs.put((action, payload or {}, future))
        # Caller waits for a definite result; publish is never retried on timeout.
        return future.result(timeout=150)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        future = Future()
        self._jobs.put(("_close", {}, future))
        self._thread.join(timeout=20)

    def _set(self, **values):
        with self._lock:
            self._cache.update(copy.deepcopy(values))

    def _browser(self, status, message):
        connected = bool(self._page and not self._page.is_closed())
        self._set(browser={"connected": connected, "running": self._running, "status": status, "message": message, "url": self._page.url if connected else "", "mode": self._mode, "course_url": self._configured_url, "qa_url": self._qa_url})

    def _run(self):
        while True:
            try:
                action, payload, future = self._jobs.get(timeout=0.4)
            except queue.Empty:
                try:
                    self._tick()
                except ValueError as exc:
                    self._running = False
                    self._browser("needs_attention", str(exc))
                except Exception:
                    self._running = False
                    self._browser("needs_attention", "浏览器页面已变化或关闭，请重新打开并检查当前页面。")
                continue
            if action == "_close":
                self._shutdown()
                future.set_result({})
                return
            try:
                result = self._dispatch(action, payload)
                future.set_result(result)
            except Exception as exc:
                self._running = False
                self._browser("needs_attention", str(exc) if isinstance(exc, ValueError) else "操作未完成；请检查专用浏览器。真实发布不会自动重试。")
                # Do not leak browser diagnostics, login URLs, or page content.
                message = str(exc) if isinstance(exc, ValueError) else "浏览器操作未完成（可能页面变化、等待超时或 Edge 不可用），请检查浏览器后重试读取。"
                future.set_result({**self.status(), "error": message})

    def _shutdown(self):
        self._running = False
        try:
            if self._context:
                self._context.close()
        finally:
            if self._playwright:
                self._playwright.stop()
            self._context = self._page = self._qa_page = self._detail_page = self._playwright = None
            self._browser("closed", "专用浏览器已关闭，登录状态保留在本项目专用目录。")

    def _dispatch(self, action, payload):
        if action == "open":
            return self._open(payload)
        if action == "adopt_current":
            return self._adopt_current(payload)
        if not self._page or self._page.is_closed():
            raise ValueError("请先打开专用浏览器。")
        if action == "pause":
            self._pause_media(self._page)
            self._running = False
            self._browser("paused", "已暂停视频学习。")
        elif action == "scan":
            self._scan()
        elif action == "video_start":
            self._start()
        elif action == "qa_sync":
            self._sync_qa()
        elif action in {"publish", "verify"}:
            draft = payload.get("draft") or {}
            return self._publish(draft) if action == "publish" else self._verify(draft)
        else:
            raise ValueError("不支持的浏览器操作。")
        return self.status()

    def _adopt_current(self, payload):
        """Adopt a course tab opened by the user inside this dedicated context."""
        if self._mode != "zhihuishu" or not self._context:
            raise ValueError("请先打开专用智慧树浏览器，再使用“采用当前课程页”。")
        requested_url = payload.get("url", "")
        if requested_url:
            validate_real_url(requested_url)
        requested_qa = payload.get("qa_url", "")
        if requested_qa:
            validate_real_url(requested_qa)
        candidates = []
        for page in self._context.pages:
            if page.is_closed() or page in (self._qa_page, self._detail_page):
                continue
            try:
                validate_real_url(page.url)
                if requested_url and page.url != requested_url:
                    continue
                if self._blocker(page):
                    continue
                text = page.locator("body").inner_text(timeout=2000)
                video = any(frame.locator("video:visible").count() for frame in page.frames)
                course_navigation = "课程问答" in text and bool(re.search(r"目录|课程资料|作业考试|成绩分析", text))
                if video or course_navigation:
                    state = page.evaluate("({focus:document.hasFocus(),visible:document.visibilityState==='visible'})")
                    candidates.append((page, state))
            except Exception:
                continue
        if not candidates:
            raise ValueError("未找到可确认的课程页。请在专用浏览器打开课程视频或目录，再采用当前课程；不会选择登录页或问答详情。")
        focused = [page for page, state in candidates if state["focus"]]
        visible = [page for page, state in candidates if state["visible"]]
        if len(candidates) == 1:
            selected = candidates[0][0]
        elif not requested_url and len(focused) == 1:
            selected = focused[0]
        elif not requested_url and len(visible) == 1:
            selected = visible[0]
        else:
            raise ValueError("有多个可用课程标签页，当前页无法唯一确认。请复制目标课程页完整地址并明确采用该地址。")
        self._pause_media(self._page)
        self._running = False
        changed = selected.url != self._configured_url
        self._page = selected
        self._configured_url = selected.url
        if changed:
            self._qa_url, self._qa_page = requested_qa, None
            self._active_lesson = None
            self._set(qa=self._unknown_qa("已采用新的课程页，请绑定此课程问答页并重新同步个人记录。"))
        self._browser("ready", "已采用专用浏览器中打开的课程页；请确认助手设置里的课程与问答地址。")
        self._scan()
        return {**self.status(), "adopted": {"course_url": self._configured_url, "qa_url": self._qa_url, "title": selected.title()}}

    def _open(self, payload):
        mode = payload.get("mode", "demo")
        if mode not in {"demo", "zhihuishu"}:
            raise ValueError("模式必须是 demo 或 zhihuishu。")
        url = self.demo_url if mode == "demo" else validate_real_url(payload.get("url") or "https://www.zhihuishu.com/")
        qa_url = "" if mode == "demo" else payload.get("qa_url", "")
        if qa_url:
            validate_real_url(qa_url)
        if self._context:
            self._shutdown()
        from playwright.sync_api import sync_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._attempts = json.loads(self._attempt_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._attempts = {}
        except (ValueError, OSError):
            raise ValueError("发布尝试记录无法读取，为避免重复发布已停止。请保留文件并检查。")
        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir), channel="msedge", headless=self.headless,
                viewport={"width": 1280, "height": 900}, locale="zh-CN", timezone_id="Asia/Shanghai",
                args=["--autoplay-policy=no-user-gesture-required"] if self.headless else [],
            )
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        self._context.set_default_timeout(6000)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._mode, self._qa_url, self._qa_page = mode, qa_url, None
        self._configured_url = url
        self._running, self._active_lesson = False, None
        self._set(course={"name": "", "url": url, "lessons": []}, qa=self._unknown_qa("尚未同步今日个人记录。"))
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._browser("ready", "已打开独立演示浏览器。" if mode == "demo" else "已打开专用智慧树浏览器；请在该窗口登录，真实平台适配尚未验收。")
        self._scan()
        return self.status()

    def _blocker(self, page):
        if self._mode == "demo":
            if page.locator('body[data-demo="course-study-assistant-v1"]').count() != 1:
                return "演示页面标识未找到；不会将其他页面当作演示结果。"
            return None
        try:
            validate_real_url(page.url)
        except ValueError:
            return "页面已跳转离开智慧树域名，请手动检查登录或课程地址。"
        if page.locator('input[type="password"]:visible').count():
            return "需要在专用浏览器手动登录。"
        body = page.locator("body").inner_text(timeout=3000)
        if re.search(r"请先登录|扫码登录|账号密码登录|登录已失效|重新登录", body):
            return "需要在专用浏览器手动登录。"
        if re.search(r"请完成验证|滑动.*验证|拖动.*滑块|安全验证|人机验证|请输入验证码", body):
            return "检测到验证码或安全验证，已暂停，请手动完成。"
        dialogs = page.locator('[role="dialog"]:visible, .el-dialog:visible, .ant-modal:visible, [class*="pop-up"]:visible')
        for index in range(min(dialogs.count(), 10)):
            text = dialogs.nth(index).inner_text()
            if re.search(r"单选|多选|继续学习|学习提醒|请.*答题", text) or (page is self._page and re.search(r"答题|题目|判断", text)):
                return "检测到课程弹题或学习提醒，已暂停，请本人处理。"
        if re.search(r"随堂测验|请先完成.*答题|请完成.*题目", body):
            return "检测到课程弹题，已暂停，请本人处理。"
        return None

    def _guard(self, page):
        reason = self._blocker(page)
        if reason:
            self._running = False
            self._pause_media(self._page)
            self._browser("needs_attention", reason)
            raise ValueError(reason)

    @staticmethod
    def _pause_media(page):
        if page and not page.is_closed():
            for frame in page.frames:
                try:
                    frame.locator("video").evaluate_all("videos => videos.forEach(v => v.pause())")
                except Exception:
                    pass

    def _scan(self):
        self._guard(self._page)
        if self._mode == "demo":
            self._page.wait_for_selector("[data-lesson-id]")
            lessons = self._page.locator("[data-lesson-id]").evaluate_all("rows => rows.map(r => ({id:r.dataset.lessonId,title:r.querySelector('button').textContent.trim(),progress:Number(r.dataset.progress),status:r.dataset.status}))")
            name = self._page.locator("[data-course-name]").inner_text()
        else:
            # Read only visible course rows with an explicit duration or state.
            rows = self._page.evaluate("""() => {
              const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
              const selector = e => { const parts=[]; while(e && e.nodeType===1 && e!==document.body){let n=1,s=e;while((s=s.previousElementSibling))n++;parts.unshift(e.tagName.toLowerCase()+':nth-child('+n+')');e=e.parentElement;} return 'body > '+parts.join(' > ');};
              return [...document.querySelectorAll('li, [class*="video-item"], [class*="videoItem"], [class*="lesson-item"], [class*="lessonItem"]')]
              .filter(e => visible(e) && e.innerText.length<350 && /\\d{1,2}:\\d{2}|已完成|已学完/.test(e.innerText) && !e.querySelector('li'))
              .slice(0,300).map(e => {const link=e.querySelector('a[href]');return {text:e.innerText.trim(),selector:selector(e),stable:e.getAttribute('data-video-id')||e.getAttribute('data-lesson-id')||e.getAttribute('data-id')||e.id||(link&&/^https?:/.test(link.href)?link.href:'')};});
            }""")
            lessons, self._real_selectors = [], {}
            for row in rows:
                title = re.sub(r"\s+", " ", row["text"]).strip()
                stable_title = re.sub(r"(?:学习进度|观看进度)\s*[:：]?\s*\d{1,3}%|\d{1,3}%|已完成|已学完|未完成|未学习|正在播放|学习中|播放中", "", title).strip()
                identity = hashlib.sha256((row.get("stable") or stable_title).encode()).hexdigest()[:16]
                complete = bool(re.search(r"已完成|已学完|(?:学习进度|观看进度)\s*[:：]?\s*100%", title))
                match = re.search(r"(?:学习进度|观看进度)\s*[:：]?\s*(\d{1,3})%", title)
                progress = 100 if complete else min(99, int(match.group(1))) if match else 0
                lessons.append({"id": identity, "title": title, "progress": progress, "status": "verified" if complete else "pending"})
                self._real_selectors[identity] = row["selector"]
            name = self._page.title()
            if not lessons:
                self._browser("needs_attention", "课程目录结构尚未识别，可手动选课后正常播放；平台进度未知，不会标记完成或自动跳课。")
        self._set(course={"name": name, "url": self._page.url, "lessons": lessons})

    def _play_current(self):
        self._guard(self._page)
        if self._mode == "demo":
            self._page.locator("[data-play]").click()
        else:
            videos = [(frame, frame.locator("video:visible")) for frame in self._page.frames]
            found = [(frame, locator) for frame, locator in videos if locator.count() == 1]
            if len(found) != 1:
                raise ValueError("未能唯一识别可见视频，请在专用浏览器手动选择课程。")
            # HTMLMediaElement.play() is normal playback. Never alter time/rate.
            found[0][1].evaluate("v => v.play()")
        self._running = True
        self._browser("playing", "正在正常播放；仅以平台显示的完成记录判断课程完成。" if self._mode != "demo" else "正在播放真实演示视频，结束后自动进入下一节。")

    def _start(self):
        self._scan()
        lessons = self.status().get("course", {}).get("lessons", [])
        pending = [lesson for lesson in lessons if lesson["status"] != "verified"]
        if lessons and not pending:
            self._browser("completed", "已识别课程均显示完成。" if self._mode != "demo" else "两节演示视频均已完整播放。")
            return
        if pending:
            self._select_lesson(pending[0]["id"])
        self._play_current()

    def _select_lesson(self, identity):
        self._guard(self._page)
        if self._mode == "demo":
            self._page.locator(f'[data-select-lesson="{identity}"]').click()
        else:
            self._page.locator(self._real_selectors[identity]).click()
            self._page.wait_for_timeout(500)
        self._active_lesson = identity

    def _tick(self):
        if not self._page or self._page.is_closed():
            if self._running:
                self._running = False
                self._browser("closed", "专用浏览器页面已关闭。")
            return
        if not self._running:
            return
        self._guard(self._page)
        self._scan()
        lessons = self.status().get("course", {}).get("lessons", [])
        active = next((row for row in lessons if row["id"] == self._active_lesson), None)
        if active and active["status"] == "verified":
            next_lesson = next((row for row in lessons if row["status"] != "verified"), None)
            if next_lesson:
                self._select_lesson(next_lesson["id"])
                self._play_current()
            else:
                self._running = False
                self._browser("completed", "已识别课程均有完成证据。" if self._mode != "demo" else "两节演示视频均已完整播放并保存。")
            return
        media = []
        for frame in self._page.frames:
            media.extend(frame.locator("video:visible").evaluate_all("vs => vs.map(v=>({ended:v.ended,paused:v.paused,error:!!v.error}))"))
        if media and all(row["ended"] or row["paused"] or row["error"] for row in media):
            self._running = False
            if active and any(row["ended"] for row in media):
                active["status"] = "awaiting_verification"
                self._set(course={**self.status()["course"], "lessons": lessons})
            self._browser("needs_attention", "视频已停止；若已播放结束，仍需读取平台完成记录。请检查弹题、登录或手动暂停状态。")

    @staticmethod
    def _unknown_qa(message):
        return {"known": False, "questions": None, "answers": None, "records": [], "targets": [], "date": beijing_today(), "message": message}

    def _get_qa_page(self):
        if self._mode == "demo":
            return self._page
        if not self._qa_url:
            raise ValueError("请在设置中填写该课程的问答页链接，再同步个人记录。")
        if not self._qa_page or self._qa_page.is_closed():
            self._qa_page = self._context.new_page()
            self._qa_page.goto(validate_real_url(self._qa_url), wait_until="domcontentloaded", timeout=30000)
        return self._qa_page

    def _sync_qa(self):
        self._set(qa=self._unknown_qa("正在读取个人记录；未完成读取时不允许发布。"))
        page = self._get_qa_page()
        self._guard(page)
        if self._mode == "demo":
            records = page.locator("[data-record]").evaluate_all("rows => rows.map(r=>({id:r.dataset.id,kind:r.dataset.kind,title:r.querySelector('[data-record-title]').textContent,text:r.querySelector('[data-record-body]').textContent,url:r.querySelector('[data-record-link]').href,date:r.dataset.date,target_url:r.dataset.targetUrl}))")
            targets = page.locator("[data-target]").evaluate_all("rows => rows.map(r=>({title:r.querySelector('a').textContent,url:r.querySelector('a').href,text:r.querySelector('p').textContent}))")
            known, message = True, "演示个人记录已完整读取。"
        else:
            records, targets = [], []
            known, message = self._read_real_personal(page, records)
        today = beijing_today()
        self._set(qa={"known": known, "questions": sum(r["kind"] == "question" and r["date"] == today for r in records) if known else None,
                      "answers": sum(r["kind"] == "answer" and r["date"] == today for r in records) if known else None,
                      "records": records, "targets": targets, "date": today, "message": message})
        return self.status()["qa"]

    def _read_real_personal(self, page, records):
        # Navigation uses visible labels from the public personal-record UI.
        participate = page.get_by_text("我参与的", exact=True)
        if participate.count() == 1 and participate.is_visible():
            participate.click()
            page.wait_for_timeout(600)
        unique_records = set()
        for kind, label in (("question", "我的提问"), ("answer", "我的回答")):
            tab = page.get_by_text(label, exact=True)
            if tab.count() != 1 or not tab.is_visible():
                return False, "未找到唯一的个人提问/回答入口，请在专用问答页进入“我参与的”。"
            tab.click()
            page.wait_for_timeout(700)
            self._guard(page)
            seen_pages = set()
            for page_index in range(10):
                self._guard(page)
                selected = tab.evaluate("e => {for(let n=e,i=0;n&&i<3;n=n.parentElement,i++){const t=n.innerText||'';if(t.length>80||(t.includes('我的提问')&&t.includes('我的回答')))continue;if(n.getAttribute('aria-selected')==='true'||n.getAttribute('aria-current')==='page'||/(^|[ _-])(active|selected|current)([ _-]|$)/i.test(n.className||''))return true;}return false;}")
                if not selected:
                    return False, "个人记录标签的选中状态无法确认；为避免把公共问答算成个人记录，暂不统计或发布。"
                if page.locator('[aria-busy="true"]:visible, .el-loading-mask:visible, .ant-spin-spinning:visible').count():
                    return False, "个人记录页面仍在加载，暂不统计或发布。"
                result = self._read_real_record_page(page)
                if result["unsupported_paging"] or result["next_count"] > 1 or result["dates"] != len(result["rows"]) or (not result["rows"] and not result["empty"]):
                    return False, "个人记录分页或条目结构无法确认，今日数量未知；不会自动发布。"
                if page_index == 0 and (result["previous_enabled"] or result["current_page"] not in (None, 1) or not result["first_page_evidence"]):
                    return False, "个人记录没有从第一页开始，请在平台回到第一页再同步；不会漏算前页记录。"
                signature = json.dumps(result["rows"], sort_keys=True, ensure_ascii=False)
                if signature in seen_pages:
                    return False, "翻页后内容未变化，无法证明记录已完整读取，已停止。"
                seen_pages.add(signature)
                for row in result["rows"]:
                    try:
                        validate_real_url(row["url"])
                        datetime.strptime(row["date"], "%Y-%m-%d")
                    except ValueError:
                        return False, "个人记录详情链接或日期无法确认，暂不计入。"
                    identity = hashlib.sha256((kind + row["url"] + row["date"] + row["text"]).encode()).hexdigest()[:20]
                    if identity in unique_records:
                        continue
                    unique_records.add(identity)
                    row.update(kind=kind, id=identity)
                    if kind == "answer":
                        links = [link for link in row.pop("links", []) if link["title"] and not re.fullmatch(r"详情|查看详情|展开|回复|评论|点赞|赞", link["title"])]
                        unique = {(link["url"], link["title"]) for link in links}
                        if len(unique) == 1:
                            row["target_url"], row["target_title"] = next(iter(unique))
                    records.append(row)
                if not result["next_count"]:
                    break
                if page_index == 9:
                    return False, "已读取10页，仍有后续记录；完整数量未知，请手动核对，禁止自动发布。"
                page.locator(result["next_selector"]).click()
                page.wait_for_timeout(700)
            else:
                return False, "个人记录未读取到结束，今日数量未知。"
        return True, "已逐页读取可见个人记录并去重；平台审核状态另行判断。"

    @staticmethod
    def _read_real_record_page(page):
        return page.evaluate("""() => {
              const visible=e=>!!e.getClientRects().length;
              const selector=e=>{const parts=[];while(e&&e.nodeType===1&&e!==document.body){let n=1,s=e;while((s=s.previousElementSibling))n++;parts.unshift(e.tagName.toLowerCase()+':nth-child('+n+')');e=e.parentElement;}return 'body > '+parts.join(' > ');};
              const disabled=e=>!!(e.disabled||e.getAttribute('aria-disabled')==='true'||e.closest('[disabled],[aria-disabled="true"],.disabled,.is-disabled'));
              const label=e=>(e.innerText||e.getAttribute('aria-label')||e.getAttribute('title')||'').trim();
              const dates=[...document.querySelectorAll('body *')].filter(e=>visible(e)&&e.children.length===0&&/^发表于\\s*\\d{4}-\\d{2}-\\d{2}$/.test(e.textContent.trim()));
              const rows=[];
              for(const d of dates){let e=d.parentElement,found=null;for(let i=0;e&&i<5;i++,e=e.parentElement){const text=e.innerText||'';const count=(text.match(/发表于\\s*\\d{4}-\\d{2}-\\d{2}/g)||[]).length;if(count!==1||text.length>12000)break;const links=[...e.querySelectorAll('a[href]')].filter(a=>visible(a)&&a.href.startsWith('https://')&&!/javascript:/.test(a.href));if(text.length>d.textContent.length+10&&links.length){found={text:text.split(/发表于/)[0].trim(),date:d.textContent.match(/\\d{4}-\\d{2}-\\d{2}/)[0],url:links[0].href,links:links.map(a=>({url:a.href,title:a.innerText.trim()}))};break;}}if(found)rows.push(found);}
              const controls=[...document.querySelectorAll('a,button,[role="button"]')].filter(visible);
              const nextAll=controls.filter(e=>/^(下一页|下页|Next|›|»|>)$/i.test(label(e)));
              const next=nextAll.filter(e=>!disabled(e));
              const previousAll=controls.filter(e=>/^(上一页|上页|Previous|‹|«|<)$/i.test(label(e)));
              const previous=previousAll.filter(e=>!disabled(e));
              const pagers=[...document.querySelectorAll('[class*="pagination"],[aria-label*="分页"]')].filter(visible);
              const active=pagers.flatMap(e=>[...e.querySelectorAll('[aria-current="page"],.active,.is-active')]).map(e=>e.innerText.trim()).filter(t=>/^\\d+$/.test(t));
              const empty=/暂无(提问|回答|数据|记录)|还没有(提问|回答)/.test(document.body.innerText);
              const current=active.length===1?Number(active[0]):null;
              return {rows,dates:dates.length,next_count:next.length,next_selector:next.length===1?selector(next[0]):'',previous_enabled:previous.length>0,current_page:current,first_page_evidence:(!pagers.length&&!nextAll.length)||(current===1)||(previousAll.length>0&&previous.length===0),unsupported_paging:pagers.length>0&&nextAll.length===0,empty};
            }""")

    @staticmethod
    def _find_record(draft, qa):
        body = normalized(draft.get("body", ""))
        if not body:
            return None
        for record in qa.get("records", []):
            if not draft.get("date") or record.get("kind") != draft.get("kind") or record.get("date") != draft.get("date"):
                continue
            # Exact normalized body avoids accepting a page merely containing a draft.
            text = normalized(record.get("text", ""))
            title_body = normalized(draft.get("title", "")) + body
            target_title = normalized(record.get("target_title", ""))
            if text not in {body, title_body, body + target_title, target_title + body}:
                continue
            if draft.get("kind") == "answer" and record.get("target_url") != draft.get("target_url"):
                continue
            return record
        return None

    def _verify(self, draft):
        qa = self._sync_qa()
        record = self._find_record(draft, qa) if qa["known"] else None
        detail_confirmed = self._mode == "demo"
        if record and self._mode != "demo":
            detail_url = draft.get("target_url") if draft.get("kind") == "answer" else record["url"]
            detail_confirmed = self._verify_detail(detail_url, draft)
        verified = bool(record and detail_confirmed)
        published = {"verified": verified, "url": record["url"] if record else "", "id": record["id"] if record else "", "message": "已在当天个人记录及页面正文核验。" if verified else "未同时确认当天个人记录、目标链接与详情正文；结果未知，不会自动重发。"}
        return {**self.status(), "published": published}

    def _open_detail(self, url):
        validate_real_url(url)
        if not self._detail_page or self._detail_page.is_closed():
            self._detail_page = self._context.new_page()
        self._detail_page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._guard(self._detail_page)
        # A login redirect or a different question must never inherit approval.
        if self._detail_page.url.rstrip("/") != url.rstrip("/"):
            raise ValueError("回答目标链接发生跳转，无法确认仍是已审阅的问题；已停止。")
        return self._detail_page

    def _verify_detail(self, url, draft):
        if not url:
            return False
        page = self._open_detail(url)
        if draft.get("kind") == "answer":
            self._validate_target_course(page, url)
        return page.evaluate("""({body,date}) => {
          const norm=s=>String(s||'').normalize('NFKC').replace(/\\s+/g,'');
          const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
          const forbidden=e=>e.closest('textarea,input,[contenteditable="true"],form,[role="dialog"]');
          // A filled editor is not a published reply. Match a visible text block.
          return [...document.querySelectorAll('p,div,span,article,li')].some(e=>visible(e)&&!forbidden(e)&&!e.querySelector('textarea,input,[contenteditable="true"]')&&norm(e.innerText)===norm(body)&&e.innerText.length>0);
        }""", {"body": draft["body"], "date": draft["date"]})

    def _prepare_real_answer(self, draft):
        target = draft.get("target_url", "")
        if not target:
            raise ValueError("回答需要已审阅的问题详情链接。")
        page = self._open_detail(target)
        text = page.locator("body").inner_text()
        if "课程问答" not in text or "回答" not in text or re.search(r"考试作答|试卷作答|正在考试", text):
            raise ValueError("目标页未被识别为课程问题详情，已停止回答。")
        self._validate_target_course(page, target)
        trigger = page.get_by_text(re.compile(r"^(我要回答|我来回答|写回答|添加回答)$"))
        opened_explicitly = trigger.count() == 1 and trigger.is_visible()
        if opened_explicitly:
            trigger.click()
            page.wait_for_timeout(250)
        self._guard(page)
        editors = []
        for frame in page.frames:
            locator = frame.locator('textarea:visible, [contenteditable="true"]:visible')
            for index in range(locator.count()):
                item = locator.nth(index)
                # Nested editable children are not separate inputs.
                if item.evaluate("e=>!!e.parentElement.closest('[contenteditable=true]')"):
                    continue
                editors.append(item)
        if len(editors) != 1:
            raise ValueError("回答编辑器无法唯一识别；请在目标问题页打开回答框，保留一个输入区域后再操作。")
        editor = editors[0]
        semantics = " ".join(editor.get_attribute(attribute) or "" for attribute in ("placeholder", "aria-label", "data-placeholder"))
        if not opened_explicitly and "回答" not in semantics:
            raise ValueError("输入框无法确认用于回答（可能是评论），已停止。")
        editor.fill(draft["body"])
        entered = editor.evaluate("e=>'value' in e ? e.value : e.innerText")
        if entered.replace("\r\n", "\n").strip() != draft["body"].replace("\r\n", "\n").strip():
            raise ValueError("回答编辑器内容与已审阅草稿不一致，已停止发送。")
        button = page.get_by_role("button", name=re.compile(r"^(发布回答|提交回答|发表回答)$"))
        if button.count() == 0:
            button = page.get_by_text(re.compile(r"^(发布回答|提交回答|发表回答)$"))
        if button.count() == 0 and opened_explicitly:
            button = page.get_by_role("button", name=re.compile(r"^(发布|提交)$"))
        if button.count() != 1 or not button.is_visible() or not button.is_enabled():
            raise ValueError("回答发布按钮无法唯一识别或尚不可用，已停止。")
        if page.url.rstrip("/") != target.rstrip("/"):
            raise ValueError("编辑期间问题地址变化，已停止。")
        return page, button

    def _validate_target_course(self, page, target):
        """Require visible navigation tying this question to the configured course."""
        configured = course_identifiers(self._configured_url)
        qa_identity = course_identifiers(self._qa_url)
        for key in configured.keys() & qa_identity.keys():
            if configured[key] != qa_identity[key]:
                raise ValueError("课程页与问答页的课程标识不一致，已阻止发送。")
        expected = {**configured, **qa_identity}
        if not expected:
            raise ValueError("配置地址缺少可确认的课程标识，无法证明回答目标归属；请填写从该课程打开的完整课程和问答链接。")
        target_identity = course_identifiers(target)
        if any(target_identity[key] != expected[key] for key in target_identity.keys() & expected.keys()):
            raise ValueError("回答目标属于不同课程，已阻止发送。")
        navigation = page.locator("a[href]:visible").evaluate_all("links=>links.filter(a=>/课程问答|返回.*课程|课程首页|返回学堂/.test(a.innerText)).map(a=>a.href)")
        for url in navigation:
            try:
                validate_real_url(url)
                identity = course_identifiers(url)
            except ValueError:
                continue
            page_path = (urlparse(url).netloc.lower(), urlparse(url).path.rstrip("/"))
            for reference in (self._configured_url, self._qa_url):
                required = course_identifiers(reference)
                reference_path = (urlparse(reference).netloc.lower(), urlparse(reference).path.rstrip("/"))
                # Require every identifier on the matching configured endpoint.
                if required and page_path == reference_path and all(identity.get(key) == value for key, value in required.items()):
                    return
        raise ValueError("问题详情没有可见导航证明属于当前课程，已阻止发送；请核对目标题目与课程问答链接。")

    def _attempt_key(self, draft):
        scope = draft.get("scope") or "|".join((self._mode, self._configured_url, self._qa_url))
        return hashlib.sha256(str(scope).encode()).hexdigest() + ":" + draft_fingerprint(draft)

    def _remember_attempt(self, draft):
        self._attempts[self._attempt_key(draft)] = {"id": str(draft.get("id", "")), "date": draft["date"], "at": datetime.now(timezone.utc).isoformat()}
        temporary = self._attempt_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._attempts, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self._attempt_file)

    def _publish(self, draft):
        if draft.get("kind") not in {"question", "answer"} or not str(draft.get("body", "")).strip():
            raise ValueError("草稿类型或正文无效。")
        if draft.get("date") != beijing_today():
            raise ValueError("仅允许发布今天审阅的草稿；请重新核对日期。")
        self._pause_media(self._page)
        self._running = False
        qa = self._sync_qa()
        if not qa["known"]:
            raise ValueError("今日个人记录尚未完整确认，已阻止发布。请先同步并处理浏览器提示。")
        existing = self._find_record(draft, qa)
        if existing:
            if self._mode != "demo":
                return self._verify(draft)
            return {**self.status(), "published": {"verified": True, "url": existing["url"], "id": existing["id"], "message": "相同内容已在当天个人记录中，未重复发送。"}}
        if self._attempt_key(draft) in self._attempts or self._mode + ":" + draft_fingerprint(draft) in self._attempts:
            return {**self.status(), "published": {"verified": False, "url": "", "id": "", "message": "这条内容已有发送尝试。为避免重复，禁止重发，请使用核验功能并手动检查个人记录。"}}
        if any(r["kind"] == draft["kind"] and normalized(r.get("text", "")) == normalized(draft["body"]) for r in qa["records"]):
            raise ValueError("相同正文已出现在个人记录中，已阻止重复发布。")
        page = self._get_qa_page()
        if self._mode == "demo":
            if draft["kind"] == "answer" and draft.get("target_url") not in {t["url"] for t in qa["targets"]}:
                raise ValueError("回答目标必须是演示页面列出的题目链接。")
            page.locator('[name="draft_id"]').fill(str(draft.get("id", "")))
            page.locator('[name="kind"]').select_option(draft["kind"])
            page.locator('[name="title"]').fill(draft.get("title", ""))
            page.locator('[name="body"]').fill(draft["body"])
            page.locator('[name="target_url"]').fill(draft.get("target_url", ""))
            button = page.get_by_role("button", name="发布到演示记录", exact=True)
        else:
            if draft["kind"] == "answer":
                page, button = self._prepare_real_answer(draft)
            else:
                # Unsupported editor layouts require user handling; never guess fields.
                page.goto(validate_real_url(self._qa_url), wait_until="domcontentloaded", timeout=30000)
                self._guard(page)
                trigger = page.get_by_text(re.compile(r"^(我要提问|发布问题|提问)$"))
                if trigger.count() != 1 or not trigger.is_visible():
                    raise ValueError("无法唯一识别提问入口，请手动打开提问编辑器后核对页面。")
                trigger.click()
                self._guard(page)
                title = page.get_by_placeholder(re.compile(".*(问题标题|输入问题).*"))
                body = page.locator('textarea:visible, [contenteditable="true"]:visible')
                if title.count() != 1 or body.count() != 1 or title.evaluate("e=>e.tagName==='TEXTAREA'||e.isContentEditable"):
                    raise ValueError("提问编辑器字段结构尚未确认，已停止；不会猜测发布内容。")
                title.fill(draft.get("title", ""))
                body.fill(draft["body"])
                if body.evaluate("e=>'value' in e?e.value:e.innerText").strip() != draft["body"].strip():
                    raise ValueError("提问正文与已审阅草稿不一致，已停止。")
                button = page.get_by_role("button", name=re.compile(r"^(发布|提交问题|发布问题)$"))
                if button.count() != 1:
                    raise ValueError("发布按钮无法唯一识别，已停止。")
        self._guard(page)
        # Persist before the only click, even if the click result becomes unknown.
        self._remember_attempt(draft)
        try:
            button.click(timeout=8000)
            page.wait_for_timeout(350)
            return self._verify(draft)
        except Exception:
            return {**self.status(), "published": {"verified": False, "url": "", "id": "", "message": "发送后未取得明确核验结果。可能已发布，禁止自动重发；请检查个人记录。"}}
