"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { current: null, csrf: "", connected: false, actionBusy: false, pollBusy: false, revision: 0, stopped: false, settingsLoaded: false, settingsDirty: false, draftNodes: new Map(), unknownPublications: new Set() };
  const pages = { overview: "学习总览", videos: "视频学习", questions: "每日问答", grading: "评分细则", settings: "学习设置", records: "运行记录" };
  const settingsForm = $("settings-form");
  const statusLabels = { pending: "待学习", playing: "学习中", awaiting_verification: "待核验", verified: "已核验", draft: "待审阅", publishing: "正在发布", uncertain: "结果未知" };
  const node = (tag, className, value) => { const item = document.createElement(tag); if (className) item.className = className; if (value !== undefined) item.textContent = String(value); return item; };
  const setText = (id, value) => { $(id).textContent = String(value ?? ""); };
  const validWebURL = (value) => { try { return ["http:", "https:"].includes(new URL(value).protocol); } catch { return false; } };
  const count = (value) => Number.isFinite(Number(value)) ? Math.max(0, Math.floor(Number(value))) : 0;
  const isBusy = () => state.actionBusy || Boolean(state.current?.busy);
  function notify(text, error = false) { const box = $("message"); box.textContent = text; box.className = `message${error ? " error" : ""}`; box.hidden = false; }
  function navigate(page) {
    if (!pages[page]) page = "overview";
    document.querySelectorAll(".page").forEach((section) => { const active = section.id === `page-${page}`; section.hidden = !active; section.classList.toggle("active", active); });
    document.querySelectorAll(".nav-item").forEach((button) => { const active = button.dataset.page === page; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); });
    setText("page-name", pages[page]);
    if (location.hash !== `#${page}`) history.replaceState(null, "", `#${page}`);
  }
  function updateControls() {
    const blocked = !state.connected || isBusy();
    document.querySelectorAll("[data-action], button[type='submit']").forEach((button) => { button.disabled = blocked; });
    $("busy-notice").hidden = !isBusy();
    state.draftNodes.forEach((view) => {
      const effective = state.unknownPublications.has(view.id) ? "uncertain" : view.draft.status;
      const locked = effective !== "draft";
      view.save.disabled = blocked || locked;
      view.publish.disabled = blocked || locked || !view.review.checked || view.dirty || view.remoteChanged || !view.renderedRevision;
      view.verify.disabled = blocked || effective === "publishing";
      view.review.disabled = blocked || locked || view.dirty || view.remoteChanged || !view.renderedRevision;
      [view.title, view.body, view.target].forEach((input) => { input.readOnly = locked; });
    });
    window.CourseGrading?.updateControls(blocked);
  }
  function connection(online) {
    state.connected = online;
    $("connection").classList.toggle("offline", !online);
    setText("connection-text", online ? "本地服务已连接" : "本地服务未连接 · 正在重连");
    updateControls();
  }
  async function refresh() {
    if (state.pollBusy || state.stopped) return;
    state.pollBusy = true;
    const revision = state.revision;
    try {
      const response = await fetch("/api/state", { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) throw new Error("无法读取本地服务状态");
      const next = await response.json();
      if (!next || typeof next !== "object" || !next.settings || !next.csrf) throw new Error("本地服务返回了无效状态");
      if (revision !== state.revision || state.stopped) return;
      applyState(next);
      connection(true);
    } catch { connection(false); }
    finally { state.pollBusy = false; }
  }
  async function action(actionName, payload = {}) {
    if (isBusy() || !state.connected) { notify("当前服务未连接或正在处理上一项操作，请稍后再试。", true); return null; }
    state.actionBusy = true;
    updateControls();
    let sent = false;
    let explicitRejection = false;
    try {
      if (!state.csrf) throw new Error("尚未取得安全令牌，请等待页面连接后再操作。");
      sent = true;
      const response = await fetch("/api/action", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf }, body: JSON.stringify({ action: actionName, ...payload }) });
      let result;
      try { result = await response.json(); } catch { throw new Error("服务响应无法读取，请查看运行记录。"); }
      state.revision += 1;
      if (result.state) applyState(result.state);
      if (!response.ok || !result.ok) { explicitRejection = true; throw new Error(result.error || "操作未完成，请查看运行记录。"); }
      if (actionName === "app_exit") { state.stopped = true; connection(false); setText("connection-text", "本地助手正在退出"); return result; }
      if (actionName === "draft_publish" && result.state) {
        const published = result.state.drafts?.find((draft) => draft.id === payload.id);
        if (published?.status === "uncertain") state.unknownPublications.add(payload.id);
      }
      await refresh();
      return result;
    } catch (error) {
      if (actionName === "draft_publish" && sent && (!explicitRejection || /结果待确认|结果未知|待核验/.test(error.message || ""))) {
        state.unknownPublications.add(payload.id);
        notify(`发布结果尚未确认，请仅使用“核验发布结果”，不要再次发布。${error.message ? `\n${error.message}` : ""}`, true);
        if (state.current) renderDrafts(state.current.drafts || []);
      } else notify(error.message || "操作失败，请查看运行记录。", true);
      return null;
    } finally { state.actionBusy = false; updateControls(); }
  }
  function fillSettings(settings) {
    ["mode", "course_url", "qa_url", "question_goal", "answer_goal", "ai_base_url", "ai_protocol", "ai_model", "daily_time"].forEach((key) => { if (settingsForm.elements[key]) settingsForm.elements[key].value = settings[key] ?? ""; });
    settingsForm.elements.daily_enabled.checked = Boolean(settings.daily_enabled);
    settingsForm.elements.api_key.value = "";
    settingsForm.elements.clear_key.checked = false;
    state.settingsDirty = false;
    $("settings-dirty").hidden = true;
    state.settingsLoaded = true;
  }
  function applyState(next) {
    state.current = next;
    state.csrf = next.csrf || state.csrf;
    const settings = next.settings || {};
    if (!state.settingsLoaded) fillSettings(settings);
    setText("key-status", settings.key_saved ? "已保存 · 不回显" : "尚未保存");
    const demo = settings.mode !== "zhihuishu";
    $("mode-banner").classList.toggle("real", !demo);
    setText("mode-title", demo ? "当前为演示模式" : "当前为真实平台模式 · 智慧树");
    setText("mode-description", demo ? "这里的课程与问答均为本地演示数据，不代表真实平台学习完成。" : "仅将当前平台页面及个人记录的核验结果计入进度；未验收的能力不会显示为通过。");
    const today = next.today || "";
    setText("today-label", today ? today.replaceAll("-", " / ") : "日期尚未读取");
    setText("schedule-summary", settings.daily_enabled ? `每日 ${settings.daily_time || "09:00"} 执行计划` : "每日计划尚未开启");
    const lessons = Array.isArray(next.course?.lessons) ? next.course.lessons : [];
    const verified = lessons.filter((lesson) => lesson.status === "verified").length;
    const percent = lessons.length ? Math.round(verified / lessons.length * 100) : 0;
    setText("stat-videos", lessons.length ? verified : "—");
    setText("stat-video-total", `/ ${lessons.length || "—"} 节已读取`);
    setText("stat-video-note", lessons.length ? `${demo ? "演示课程 · " : ""}${lessons.length - verified} 节尚未核验完成` : "等待读取课程目录");
    $("course-progress").style.width = `${percent}%`;
    setText("course-progress-label", lessons.length ? `${verified} / ${lessons.length} 节 · ${percent}%` : "尚未读取");
    const courseName = next.course?.name || "尚未读取课程";
    setText("overview-course-name", courseName);
    setText("video-course-name", next.course?.name || "尚未读取课程");
    const browser = next.browser || {};
    const browserLabel = browser.running ? "运行中" : browser.connected ? "已连接" : "尚未连接";
    ["overview-browser-status", "video-browser-status"].forEach((id) => { setText(id, browserLabel); $(id).className = `badge${browser.connected ? "" : " neutral"}`; });
    setText("browser-detail", browser.message || "打开课程浏览器开始。");
    setText("overview-course-note", browser.message || "填写课程链接，打开专用浏览器后登录，再读取目录。");
    renderLessons(lessons);
    renderQA(next.qa || {}, settings, today, demo);
    const drafts = Array.isArray(next.drafts) ? next.drafts : [];
    setText("stat-drafts", Array.isArray(next.drafts) ? drafts.filter((draft) => draft.status === "draft").length : "—");
    renderDrafts(drafts);
    renderEvents(next.events || []);
    window.CourseDashboard?.render(next);
    window.CourseGrading?.render(next);
    setText("last-updated", `最近更新 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
    updateControls();
  }
  function renderLessons(lessons) {
    setText("lesson-count", `${lessons.length} 节视频`);
    const list = $("lesson-list");
    if (!lessons.length) { list.replaceChildren(empty("▷", "还没有读取到课程", "打开课程浏览器并完成登录，然后点击“读取 / 核验目录”。")); return; }
    const fragment = document.createDocumentFragment();
    lessons.forEach((lesson, index) => {
      const row = node("div", "lesson-row"); row.setAttribute("role", "row");
      row.append(node("span", "lesson-number", String(index + 1).padStart(2, "0")), node("span", "lesson-title", lesson.title || "未命名视频"));
      const progressKnown = typeof lesson.progress === "number" && Number.isFinite(lesson.progress) && lesson.progress >= 0 && lesson.progress <= 100;
      const progress = progressKnown ? Math.round(lesson.progress * 10) / 10 : 0;
      const progressBox = node("div", "lesson-progress");
      const track = node("div", "progress-track"); const fill = node("div"); fill.style.width = `${progress}%`; track.append(fill);
      progressBox.append(track, node("span", "", progressKnown ? `${progress}%` : "未知"));
      const badgeBox = node("div", "lesson-status"); badgeBox.append(node("span", `badge${lesson.status === "verified" ? "" : lesson.status === "awaiting_verification" ? " warning" : " neutral"}`, statusLabels[lesson.status] || "状态未知"));
      row.append(progressBox, badgeBox); Array.from(row.children).forEach((cell) => cell.setAttribute("role", "cell")); fragment.append(row);
    });
    list.replaceChildren(fragment);
  }
  function empty(symbol, title, text) { const box = node("div", "empty-state"); box.append(node("span", "", symbol), node("h3", "", title), node("p", "", text)); return box; }
  function renderQA(qa, settings, today, demo) {
    const known = qa.known === true && qa.date === today && Number.isInteger(qa.questions) && qa.questions >= 0 && Number.isInteger(qa.answers) && qa.answers >= 0;
    const qGoal = Number.isInteger(settings.question_goal) && settings.question_goal >= 0 ? settings.question_goal : null;
    const aGoal = Number.isInteger(settings.answer_goal) && settings.answer_goal >= 0 ? settings.answer_goal : null;
    setText("stat-questions", known ? count(qa.questions) : "—"); setText("stat-answers", known ? count(qa.answers) : "—");
    setText("stat-question-goal", `/ ${qGoal ?? "—"} 条`); setText("stat-answer-goal", `/ ${aGoal ?? "—"} 条`);
    setText("stat-question-note", known ? qGoal === null ? "每日提问目标未知" : `${demo ? "演示记录 · " : ""}还需 ${Math.max(0, qGoal - count(qa.questions))} 条提问` : "尚未核验今日个人记录");
    setText("stat-answer-note", known ? aGoal === null ? "每日回答目标未知" : `${demo ? "演示记录 · " : ""}还需 ${Math.max(0, aGoal - count(qa.answers))} 条回答` : "尚未核验今日个人记录");
    setText("qa-status", known ? `${demo ? "本地演示记录" : "今日个人记录"}已读取：提问 ${count(qa.questions)} / ${qGoal ?? "目标未知"}，回答 ${count(qa.answers)} / ${aGoal ?? "目标未知"}。${qGoal === null ? "提问目标未知" : `还需提问 ${Math.max(0, qGoal - count(qa.questions))} 条`}；${aGoal === null ? "回答目标未知" : `还需回答 ${Math.max(0, aGoal - count(qa.answers))} 条`}。` : "尚未核验今日个人记录，不能据此判断还需发布几条。请先点击“核验今日个人记录”。");
    setText("qa-record-date", known ? `${qa.date} · ${demo ? "演示" : "平台"}记录` : "等待今日核验");
    const records = Array.isArray(qa.records) ? qa.records.filter((record) => record.date === today) : [];
    const list = $("qa-records");
    if (!records.length) { list.replaceChildren(node("p", "empty-small", known ? "今日尚无已读取到的个人记录。" : "读取今日平台记录后，将在这里展示。")); return; }
    const fragment = document.createDocumentFragment();
    records.forEach((record) => { const row = node("div", "qa-record"); row.append(node("span", "badge neutral", record.kind === "answer" ? "回答" : "提问")); const content = node("div"); content.append(node("p", "", record.text || "记录正文未提供")); if (validWebURL(record.url)) content.append(safeLink(record.url, "查看原记录")); row.append(content); fragment.append(row); });
    list.replaceChildren(fragment);
  }
  function safeLink(url, label) { const link = node("a", "draft-link", label); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; return link; }
  function labelledInput(labelText, tag, value, type = "text") { const label = node("label", "", labelText); const input = node(tag); if (tag === "input") input.type = type; input.value = value || ""; label.append(input); return { label, input }; }
  function createDraftView(draft) {
    const card = node("article", "draft-card");
    const header = node("div", "draft-header"); const heading = node("div", "draft-heading");
    const titleText = node("h3"); const badge = node("span", "badge"); const meta = node("span", "draft-meta"); heading.append(titleText, badge); header.append(heading, meta);
    const form = node("form");
    const title = labelledInput("标题", "input", draft.title); title.input.maxLength = 200; title.input.required = draft.kind !== "answer";
    const body = labelledInput("正文", "textarea", draft.body); body.input.rows = 4; body.input.required = true; body.input.maxLength = 12000;
    const target = labelledInput("目标问题链接", "input", draft.target_url, "url"); target.input.required = draft.kind === "answer";
    const reviewLabel = node("label", "check-label"); const review = node("input"); review.type = "checkbox"; reviewLabel.append(review, node("span", "", "我已审阅内容、核对今日个人记录，并确认发布这条草稿。"));
    const warning = node("p", "draft-warning"); const actions = node("div", "draft-actions");
    const save = node("button", "button secondary", "保存修改"); save.type = "submit";
    const publish = node("button", "button primary", "确认发布"); publish.type = "button";
    const verify = node("button", "button secondary", "核验发布结果"); verify.type = "button";
    const linkBox = node("div"); actions.append(save, publish, verify);
    form.append(title.label, body.label, target.label, warning, reviewLabel, actions, linkBox); card.append(header, form);
    const view = { id: draft.id, draft, card, title: title.input, body: body.input, target: target.input, targetLabel: target.label, titleText, badge, meta, warning, review, reviewLabel, save, publish, verify, linkBox, dirty: false, remoteChanged: false, renderedRevision: draft.revision || "" };
    [view.title, view.body, view.target].forEach((input) => input.addEventListener("input", () => { view.dirty = true; review.checked = false; warning.textContent = "有未保存的修改。请先保存，再审阅确认。"; updateControls(); }));
    review.addEventListener("change", updateControls);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (!form.reportValidity()) return;
      const values = { id: view.id, title: view.title.value.trim(), body: view.body.value.trim(), target_url: view.target.value.trim() };
      if (!values.body || (view.draft.kind === "question" && !values.title)) { notify("标题与正文不能只包含空白，请补充内容。", true); return; }
      const result = await action("draft_save", values);
      if (result) {
        view.dirty = view.title.value.trim() !== values.title || view.body.value.trim() !== values.body || view.target.value.trim() !== values.target_url;
        view.review.checked = false;
        const saved = result.state?.drafts?.find((item) => item.id === view.id);
        if (!view.dirty && saved) { view.title.value = saved.title || ""; view.body.value = saved.body || ""; view.target.value = saved.target_url || ""; view.renderedRevision = saved.revision || ""; view.remoteChanged = false; }
        notify(view.dirty ? "提交时的草稿已保存，你之后输入的修改仍待保存。" : "草稿修改已保存。请再次审阅后确认发布。"); await refresh();
      }
    });
    publish.addEventListener("click", async () => {
      if (!review.checked || view.dirty || view.remoteChanged || !view.renderedRevision || (view.draft.status || "draft") !== "draft" || state.unknownPublications.has(view.id)) return;
      review.checked = false;
      const result = await action("draft_publish", { id: view.id, reviewed: true, expected_revision: view.renderedRevision });
      if (result) { const current = state.current?.drafts?.find((item) => item.id === view.id); notify(current?.status === "verified" ? "这条内容已通过发布结果核验。" : "已处理发布请求。请查看草稿状态；未核验的结果不代表发布成功。"); }
    });
    verify.addEventListener("click", async () => {
      const result = await action("draft_verify", { id: view.id });
      if (result) { const current = state.current?.drafts?.find((item) => item.id === view.id); if (current?.status === "verified") state.unknownPublications.delete(view.id); notify(current?.status === "verified" ? "已在个人记录中核验到这条内容。" : "本次尚未核验到发布成功的证据，请查看运行记录。未确认前不要重复发布。"); if (state.current) renderDrafts(state.current.drafts || []); }
    });
    return view;
  }
  function renderDrafts(drafts) {
    const list = $("draft-list"); const ids = new Set(drafts.map((draft) => draft.id));
    state.draftNodes.forEach((view, id) => { if (!ids.has(id)) { view.card.remove(); if (!view.dirty) state.draftNodes.delete(id); } });
    setText("draft-count", `${drafts.length} 条`);
    if (!drafts.length) { list.replaceChildren(empty("✎", "你的想法，从这里开始", "手写一条草稿，或让 AI 辅助起草。")); return; }
    list.querySelectorAll(":scope > .empty-state").forEach((item) => item.remove());
    drafts.forEach((draft) => {
      let view = state.draftNodes.get(draft.id);
      if (!view) { view = createDraftView(draft); state.draftNodes.set(draft.id, view); list.append(view.card); }
      else if (!view.card.isConnected) list.append(view.card);
      if (view.draft.revision !== draft.revision || view.draft.title !== draft.title || view.draft.body !== draft.body || view.draft.target_url !== draft.target_url) view.review.checked = false;
      view.draft = draft;
      if (draft.status === "verified") state.unknownPublications.delete(draft.id);
      const status = state.unknownPublications.has(draft.id) ? "uncertain" : draft.status || "draft";
      const editing = view.card.contains(document.activeElement);
      if (!view.dirty && !editing) { view.title.value = draft.title || ""; view.body.value = draft.body || ""; view.target.value = draft.target_url || ""; view.renderedRevision = draft.revision || ""; }
      view.remoteChanged = view.renderedRevision !== (draft.revision || "");
      view.titleText.textContent = draft.kind === "answer" ? "回答草稿" : "提问草稿";
      view.meta.textContent = `${draft.date || ""} · ${draft.created_at ? String(draft.created_at).slice(11, 16) : "已保存"}`;
      view.badge.textContent = statusLabels[status] || "待核验";
      view.badge.className = `badge${status === "uncertain" ? " warning" : status === "verified" ? "" : " neutral"}`;
      view.save.hidden = status !== "draft"; view.publish.hidden = status !== "draft"; view.reviewLabel.hidden = status !== "draft"; view.verify.hidden = status === "draft" || status === "publishing";
      view.warning.textContent = view.dirty ? "有未保存的修改。请先保存，再审阅确认。" : status === "uncertain" ? "发布结果未知。为避免重复发布，只能核验已有结果；本助手不会重试发布。" : status === "verified" ? "已核验到发布记录。演示模式中的核验仅代表本地演示结果。" : status === "publishing" ? "正在处理发布，请等待结果，不要重复操作。" : view.remoteChanged ? "这条草稿已在另一处更新，审阅确认已取消。结束编辑后会读取最新内容，请重新审阅。" : "请先核对今日个人记录与目标数量，再审阅确认。";
      view.targetLabel.hidden = draft.kind !== "answer" && !draft.target_url;
      view.linkBox.replaceChildren(); if (validWebURL(draft.url)) view.linkBox.append(safeLink(draft.url, "查看已发布内容 ↗"));
    });
    updateControls();
  }
  function renderEvents(events) {
    const render = (id, limit) => {
      const rows = Array.isArray(events) ? events.slice(0, limit) : [];
      if (!rows.length) { $(id).replaceChildren(node("p", "empty-small", "暂无运行记录。")); return; }
      const fragment = document.createDocumentFragment();
      rows.forEach((event) => { const level = ["error", "warning"].includes(event.level) ? event.level : "info"; const row = node("div", `event-row ${level}`); row.append(node("span", "event-dot"), node("span", "event-text", event.text || ""), node("time", "event-at", event.at ? String(event.at).replace("T", " ").slice(5, 19) : "")); fragment.append(row); });
      $(id).replaceChildren(fragment);
    };
    render("recent-events", 5); render("all-events", 60);
  }
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
  document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.go)));
  window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
  document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", async () => {
    const name = button.dataset.action;
    if (name === "app_exit" && !window.confirm("确认退出课程助手？正在运行的学习任务与每日定时计划会停止，已保存的内容将保留。")) return;
    if (name === "ai_test" && state.settingsDirty) { notify("AI 配置有未保存的修改。请先保存，再测试已保存的配置。", true); return; }
    const result = await action(name);
    if (!result) return;
    if (name === "adopt_current" && !state.settingsDirty && result.state?.settings) fillSettings(result.state.settings);
    if (name === "report_export") { const path = result.result?.path || result.result?.report_path || (typeof result.result === "string" ? result.result : ""); $("export-result").textContent = path ? `报告已导出到本机：${path}` : "报告导出请求已完成，请在运行记录中查看保存路径。"; $("export-result").hidden = false; }
    const messages = { browser_open: "课程浏览器已打开。需要登录时，请在浏览器中完成。", adopt_current: state.settingsDirty ? "课程接管请求已处理，请查看当前课程。设置页有未保存修改，保存前请核对课程链接。" : "课程接管请求已处理，已回读课程链接。请查看当前课程与核验状态。", scan: "课程目录读取 / 核验操作已完成，请查看当前目录状态。", video_start: "视频学习请求已处理，请查看浏览器提示与课程进度。", pause: "暂停请求已处理，请查看浏览器状态。", qa_sync: "个人记录核验已完成，请查看今天的核验状态。", ai_test: "AI 接口测试请求已完成，请查看运行记录中的实际结果。", report_export: "本地报告已导出。", app_exit: "退出请求已接受，正在关闭任务与专用浏览器。可以关闭此页面，下次请重新启动助手。" };
    notify(messages[name] || "操作已处理。");
  }));
  settingsForm.addEventListener("input", () => { state.settingsDirty = true; $("settings-dirty").hidden = false; });
  settingsForm.addEventListener("change", () => { state.settingsDirty = true; $("settings-dirty").hidden = false; });
  settingsForm.addEventListener("submit", async (event) => {
    event.preventDefault(); if (!settingsForm.reportValidity()) return;
    const fields = settingsForm.elements;
    const settings = { mode: fields.mode.value, course_url: fields.course_url.value.trim(), qa_url: fields.qa_url.value.trim(), question_goal: Number(fields.question_goal.value), answer_goal: Number(fields.answer_goal.value), daily_enabled: fields.daily_enabled.checked, daily_time: fields.daily_time.value, ai_base_url: fields.ai_base_url.value.trim(), ai_protocol: fields.ai_protocol.value, ai_model: fields.ai_model.value.trim() };
    const clear = fields.clear_key.checked; const key = fields.api_key.value.trim();
    if (clear && key) { notify("请只选择一种操作：保存新密钥，或清除原密钥。", true); return; }
    const payload = { settings }; if (key) payload.api_key = key; if (clear) payload.clear_key = true;
    const snapshot = new URLSearchParams(new FormData(settingsForm)).toString();
    const result = await action("settings", payload);
    if (result) {
      const unchanged = new URLSearchParams(new FormData(settingsForm)).toString() === snapshot;
      if (unchanged && result.state?.settings) fillSettings(result.state.settings);
      else if (fields.api_key.value.trim() === key) fields.api_key.value = "";
      setText("settings-save-note", unchanged ? "设置已保存并回读确认。" : "提交时的设置已保存，之后的修改仍待保存。");
      notify(unchanged ? "设置已保存。页面已重新读取本地状态。" : "提交时的设置已保存。你之后输入的修改已保留，请再次保存。");
    }
  });
  function updateDraftFormValidation(form) { const answer = form.elements.kind.value === "answer"; form.elements.target_url.required = answer; if (form.elements.title) form.elements.title.required = !answer; }
  [$("draft-form"), $("generate-form")].forEach((form) => { form.elements.kind.addEventListener("change", () => updateDraftFormValidation(form)); updateDraftFormValidation(form); });
  $("draft-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
    const values = { kind: form.elements.kind.value, title: form.elements.title.value.trim(), body: form.elements.body.value.trim(), target_url: form.elements.target_url.value.trim() };
    if (!values.body || (values.kind === "question" && !values.title)) { notify("标题与正文不能只包含空白，请补充内容。", true); return; }
    const snapshot = new URLSearchParams(new FormData(form)).toString();
    const result = await action("draft_create", values);
    if (result) { if (new URLSearchParams(new FormData(form)).toString() === snapshot) form.reset(); updateDraftFormValidation(form); notify("草稿已保存，尚未发布。请在下方审阅内容。"); }
  });
  $("generate-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget; if (!form.reportValidity()) return;
    if (form.elements.context.value.trim().length < 10) { notify("请至少提供 10 个字的课程背景或待回答问题。", true); return; }
    const result = await action("draft_generate", { kind: form.elements.kind.value, context: form.elements.context.value.trim(), target_url: form.elements.target_url.value.trim() });
    if (result) notify("AI 草稿已生成，尚未发布。请在下方核对内容并修改。");
  });
  window.CourseGrading?.init({ action, notify });
  navigate(location.hash.slice(1)); updateControls(); refresh(); setInterval(refresh, 2000);
})();
