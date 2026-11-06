"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const MAX_BYTES = 100 * 1024;
  const MAX_TEXT = 20000;
  const MAX_JSON_TEXT = 160000;
  const MAX_JSON_BYTES = 500 * 1024;
  const MAX_RULES = 30;
  const metrics = { video_days: "视频学习天数", video_completion: "视频完成度（%）", questions: "提问数量", answers: "回答数量", other: "其他 / 待确认" };
  const formulas = { unknown: "公式未知", threshold: "达到目标得满分", linear: "按完成比例计分", per_unit: "按完整数量组计分" };
  const periods = { total: "课程累计", daily: "每天" };
  const ui = { api: null, latest: null, loaded: false, scope: null, revision: "", draft: null, rows: [], dirty: false, sourceChanged: false, stale: "", blocked: true, pending: "", editVersion: 0, fileVersion: 0 };
  const node = (tag, className, value) => { const element = document.createElement(tag); if (className) element.className = className; if (value !== undefined) element.textContent = String(value); return element; };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const known = (value) => typeof value === "number" && Number.isFinite(value);
  const displayScore = (value) => known(value) ? `${value} 分` : "未知";
  const isSaved = (grading) => grading?.saved === true && grading.rules && Array.isArray(grading.rules.rules);
  const nextID = () => globalThis.crypto?.randomUUID?.() || `rule-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  function report(message, error = false) { $("grading-import-note").textContent = message; ui.api?.notify(message, error); }
  function clearReview() { $("grading-reviewed").checked = false; }
  function sourceLimit() { return $("grading-format").value === "json" ? MAX_JSON_TEXT : MAX_TEXT; }
  function updateSourceCount() {
    const limit = sourceLimit();
    $("grading-source").maxLength = limit;
    $("grading-source-count").textContent = `${$("grading-source").value.length.toLocaleString("en-US")} / ${limit.toLocaleString("en-US")} 字`;
  }
  function markEdited(source = false) {
    ui.dirty = true;
    ui.editVersion += 1;
    if (source) ui.sourceChanged = true;
    clearReview();
    updateSourceCount();
    updateControls();
  }
  function setStale(message) {
    ui.stale = message;
    clearReview();
    $("grading-conflict").textContent = `${message} 当前输入已保留；请点击“重新载入当前课程规则”后继续。`;
    $("grading-conflict").hidden = false;
  }
  function updateControls(blocked) {
    if (typeof blocked === "boolean") ui.blocked = blocked;
    const unavailable = ui.blocked || !ui.latest || Boolean(ui.pending);
    const invalidDraft = !ui.draft || !ui.rows.length || Boolean(ui.stale) || ui.sourceChanged;
    $("grading-parse").disabled = unavailable || Boolean(ui.stale) || !$("grading-source").value.trim();
    $("grading-save").disabled = unavailable || invalidDraft || !$("grading-reviewed").checked;
    $("grading-reviewed").disabled = unavailable || invalidDraft;
    $("grading-reload").disabled = unavailable;
    $("grading-export").disabled = !isSaved(ui.latest);
    $("grading-add").disabled = unavailable || Boolean(ui.stale) || ui.rows.length >= MAX_RULES;
    // Inputs are locked only while a grading request is in flight. Routine polling never writes them.
    document.querySelectorAll("#grading-form input:not(#grading-reviewed), #grading-form select, #grading-form textarea, #grading-rule-list button, #grading-source, #grading-file, #grading-format").forEach((input) => { input.disabled = Boolean(ui.pending); });
    $("grading-dirty").hidden = !ui.dirty;
    $("grading-rule-count").textContent = `${ui.rows.length} / ${MAX_RULES} 项`;
    $("grading-review-note").textContent = ui.stale ? "课程或规则版本已变化，需要重新载入后核对。" : ui.sourceChanged && ui.draft ? "导入原文已修改，请重新解析草稿，再核对确认。" : "任何编辑都会取消确认，需要重新核对。保存仅记录规则，不代表学习完成或平台已给分。";
  }
  function renderSummary(grading) {
    const saved = isSaved(grading);
    const rules = saved ? grading.rules : null;
    const summary = saved ? `${rules.course_name || "当前课程"} · 已保存 ${rules.rules.length} 项评分规则` : "当前课程尚未保存评分细则。可粘贴课程要求后逐项核对。";
    $("grading-saved-summary").textContent = summary;
    $("grading-overview-summary").textContent = `${summary}${saved ? " · 本地估算不代表平台成绩。" : ""}`;
    $("grading-saved-badge").textContent = saved ? "已保存到本机" : "尚未保存";
    $("grading-saved-badge").className = `badge${saved ? "" : " neutral"}`;
    const estimated = saved && grading.score?.complete === true ? grading.score.earned : null;
    ["grading-saved-target", "grading-overview-target"].forEach((id) => { $(id).textContent = displayScore(rules?.target_score); });
    ["grading-saved-score", "grading-overview-score"].forEach((id) => { $(id).textContent = displayScore(estimated); });
    $("grading-score-note").textContent = known(estimated) ? "依据本机已有证据和已核对公式估算，不是平台成绩，最终以平台公布为准。" : "公式或学习证据尚不完整，本地预计分保持未知。规则保存不代表学习完成，也不是平台成绩。";
  }
  function render(next) {
    const grading = next.grading;
    if (!grading || typeof grading !== "object") {
      ui.latest = null;
      $("grading-saved-summary").textContent = "当前本地服务暂未提供评分细则功能。";
      updateControls();
      return;
    }
    ui.latest = grading;
    renderSummary(grading);
    if (!ui.loaded && ui.dirty) { ui.loaded = true; ui.scope = grading.scope; ui.revision = grading.revision || ""; }
    else if (!ui.loaded) loadCurrent();
    else if (grading.scope !== ui.scope) setStale("当前课程已切换，原草稿不属于现在的课程。");
    else if ((grading.revision || "") !== ui.revision && ui.pending !== "save") setStale("已保存的规则已在其他页面更新，当前草稿版本已过期。");
    updateControls();
  }
  function loadCurrent() {
    if (!ui.latest) return;
    ui.loaded = true;
    ui.scope = ui.latest.scope;
    ui.revision = ui.latest.revision || "";
    ui.stale = "";
    ui.dirty = false;
    ui.sourceChanged = false;
    ui.fileVersion += 1;
    ui.editVersion += 1;
    $("grading-conflict").hidden = true;
    $("grading-format").value = "text";
    $("grading-file").value = "";
    $("grading-file-note").textContent = "只读取你选择的文件。";
    const saved = isSaved(ui.latest);
    $("grading-source").value = saved ? ui.latest.rules.source_text || "" : "";
    setDraft(saved ? clone(ui.latest.rules) : null);
    updateSourceCount();
    $("grading-import-note").textContent = saved ? "已载入当前课程的本地规则。修改后需要重新核对并保存。" : "解析只整理草稿；核对并保存后才会成为当前课程规则。";
    updateControls();
  }
  function inputField(labelText, key, value, type = "text") {
    const label = node("label", "", labelText);
    const input = node("input");
    input.type = type;
    input.dataset.ruleField = key;
    input.value = value ?? "";
    if (type === "number") { input.min = "0"; input.max = "1000000"; input.step = "any"; input.placeholder = "未知"; }
    label.append(input);
    return { label, input };
  }
  function selectField(labelText, key, value, options) {
    const label = node("label", "", labelText);
    const input = node("select");
    input.dataset.ruleField = key;
    Object.entries(options).forEach(([id, text]) => { const option = node("option", "", text); option.value = id; input.append(option); });
    input.value = Object.hasOwn(options, value) ? value : Object.keys(options)[0];
    label.append(input);
    return { label, input };
  }
  function addRow(rule) {
    const card = node("article", "grading-rule-card");
    const heading = node("div", "grading-rule-heading");
    const title = node("h3", "", `规则 ${ui.rows.length + 1}`);
    const remove = node("button", "text-button", "移除本项"); remove.type = "button";
    heading.append(title, remove);
    const grid = node("div", "grading-rule-fields");
    const fields = {};
    const add = (key, field) => { fields[key] = field.input; grid.append(field.label); };
    add("name", inputField("规则名称", "name", rule.name)); fields.name.maxLength = 200; fields.name.required = true;
    add("metric", selectField("统计项目", "metric", rule.metric || "other", metrics));
    add("target", inputField("达标数量 / 每次计分所需数量", "target", rule.target, "number"));
    add("period", selectField("统计周期", "period", rule.period || "total", periods));
    add("daily_minutes", inputField("每天至少学习分钟数", "daily_minutes", rule.daily_minutes, "number"));
    add("max_score", inputField("本项满分", "max_score", rule.max_score, "number"));
    add("formula", selectField("评分公式", "formula", rule.formula || "unknown", formulas));
    add("points_per_unit", inputField("每组得分", "points_per_unit", rule.points_per_unit, "number"));
    const evidenceLabel = node("label", "grading-evidence", "原文依据 / 核对说明");
    const evidence = node("textarea"); evidence.value = rule.evidence || ""; evidence.rows = 2; evidence.maxLength = 1500; evidence.dataset.ruleField = "evidence"; evidenceLabel.append(evidence); fields.evidence = evidence;
    const help = node("p", "field-help", "完成度使用百分数。按完整数量组计分时，例如每 3 次得 2 分，请填写数量 3、每组得分 2；不足一组不计分。每天分钟数仅适用于视频学习天数条件。");
    card.append(heading, grid, help, evidenceLabel);
    const row = { id: rule.id || nextID(), card, fields, title };
    remove.addEventListener("click", () => { ui.rows = ui.rows.filter((entry) => entry !== row); card.remove(); ui.rows.forEach((entry, index) => { entry.title.textContent = `规则 ${index + 1}`; }); markEdited(); });
    ui.rows.push(row);
    $("grading-rule-list").append(card);
  }
  function setDraft(draft) {
    ui.draft = draft;
    ui.rows = [];
    clearReview();
    $("grading-rule-list").replaceChildren();
    $("grading-form").hidden = !draft;
    if (!draft) return;
    $("grading-course-name").value = draft.course_name || "";
    $("grading-total-score").value = draft.total_score ?? "";
    $("grading-target-score").value = draft.target_score ?? "";
    (draft.rules || []).forEach(addRow);
    const warnings = Array.isArray(draft.warnings) ? draft.warnings : [];
    $("grading-warning-list").replaceChildren(...warnings.map((warning) => node("li", "", warning)));
    $("grading-warnings").hidden = !warnings.length;
  }
  function numberValue(input) {
    if (!input.value.trim()) return null;
    const value = Number(input.value);
    if (!Number.isFinite(value) || value < 0 || value > 1000000) throw new Error("数值必须在 0 至 1,000,000 之间；未知数值请留空。");
    return value;
  }
  function collectDraft() {
    const rules = ui.rows.map((row) => {
      const fields = row.fields;
      const rule = { id: row.id, name: fields.name.value.trim(), metric: fields.metric.value, period: fields.period.value, formula: fields.formula.value, evidence: fields.evidence.value.trim() };
      ["target", "daily_minutes", "max_score", "points_per_unit"].forEach((key) => { rule[key] = numberValue(fields[key]); });
      if (!rule.name) throw new Error("请填写每项规则的名称。");
      return rule;
    });
    if (!rules.length || rules.length > MAX_RULES) throw new Error(`请保留 1 至 ${MAX_RULES} 项规则。`);
    const result = { course_name: $("grading-course-name").value.trim(), source_text: ui.draft.source_text || "", target_score: numberValue($("grading-target-score")), total_score: numberValue($("grading-total-score")), rules };
    if (Array.isArray(ui.draft.warnings)) result.warnings = ui.draft.warnings;
    if (result.target_score !== null && result.total_score !== null && result.target_score > result.total_score) throw new Error("目标分不能高于课程总分，请核对后再保存。");
    return result;
  }
  async function parseSource() {
    if (ui.pending || ui.blocked || ui.stale) return;
    const text = $("grading-source").value.replace(/^\uFEFF/, "");
    if (!text.trim()) { report("请先粘贴评分要求，或导入文字文件。", true); return; }
    if (text.length > sourceLimit()) { report(`当前格式不能超过 ${sourceLimit().toLocaleString("en-US")} 字，请缩短后重试。`, true); return; }
    if (new TextEncoder().encode(JSON.stringify({ action: "grading_parse", text, format: $("grading-format").value })).length > 512 * 1024) { report("导入内容编码后超过本地请求大小限制，请缩短后重试。", true); return; }
    const scope = ui.scope;
    const version = ui.editVersion;
    ui.pending = "parse"; clearReview(); updateControls();
    $("grading-import-note").textContent = "正在本机解析评分要求……";
    try {
      const result = await ui.api.action("grading_parse", { text, format: $("grading-format").value });
      if (!result) { $("grading-import-note").textContent = "解析未完成。已保存规则和现有草稿均保留，请检查上方提示。"; return; }
      if (ui.scope !== scope || ui.latest?.scope !== scope || ui.stale || ui.editVersion !== version) { report("解析期间课程或原文发生变化，未替换当前草稿。请重新载入后再解析。", true); return; }
      const draft = result.result?.draft;
      if (!draft || !Array.isArray(draft.rules) || draft.rules.length > MAX_RULES) { report("本地服务未返回有效规则草稿，原有内容已保留。", true); return; }
      setDraft(clone(draft));
      ui.sourceChanged = false; ui.dirty = true; ui.editVersion += 1;
      report(`已整理 ${draft.rules.length} 项规则草稿。请逐项核对；未知信息不会自动补成事实。`);
    } finally { ui.pending = ""; updateControls(); }
  }
  async function importFile() {
    const file = $("grading-file").files?.[0];
    if (!file) return;
    clearReview(); updateControls();
    const version = ++ui.fileVersion;
    const editVersion = ui.editVersion;
    const scope = ui.scope;
    try {
      if (!/\.(txt|md|json)$/i.test(file.name)) throw new Error("仅支持 .txt、.md、.json。截图和 PDF 请先复制文字。");
      const isJSON = /\.json$/i.test(file.name);
      const limit = isJSON ? MAX_JSON_TEXT : MAX_TEXT;
      if (file.size > (isJSON ? MAX_JSON_BYTES : MAX_BYTES)) throw new Error(`文件超过 ${isJSON ? "500" : "100"} KiB，请缩短内容后导入。`);
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer()).replace(/^\uFEFF/, "");
      if (text.length > limit) throw new Error(`文件文字超过 ${limit.toLocaleString("en-US")} 字，请缩短后导入。`);
      if (!text.trim()) throw new Error("文件为空，请选择包含评分要求的文件。");
      if (version !== ui.fileVersion || editVersion !== ui.editVersion || scope !== ui.scope || ui.latest?.scope !== scope || ui.pending) return;
      $("grading-source").value = text;
      $("grading-format").value = isJSON ? "json" : "text";
      $("grading-file-note").textContent = `已读取：${file.name}`;
      markEdited(true);
      report("文件文字已载入。点击“解析为可编辑草稿”，再逐项核对。");
    } catch (error) { report(error instanceof TypeError ? "文件不是有效的 UTF-8 文字，请转换编码后重试。现有规则和草稿已保留。" : error.message, true); }
    finally { $("grading-file").value = ""; }
  }
  async function saveDraft(event) {
    event.preventDefault();
    if (ui.pending || ui.blocked || ui.stale || ui.sourceChanged || !$("grading-reviewed").checked) return;
    if (!$("grading-form").reportValidity()) return;
    let rules;
    try { rules = collectDraft(); } catch (error) { report(error.message, true); clearReview(); updateControls(); return; }
    const scope = ui.scope;
    ui.pending = "save"; clearReview(); updateControls();
    try {
      const result = await ui.api.action("grading_save", { rules, reviewed: true, expected_revision: ui.revision, scope });
      if (!result) { if (ui.latest?.revision !== ui.revision && ui.latest?.scope === scope) setStale("保存结果需要核对，请重新载入当前课程规则。"); return; }
      if (ui.latest?.scope !== scope || ui.stale) { report("保存请求已处理，但当前课程已经变化。请重新载入当前课程规则后核对。", true); return; }
      if (!isSaved(ui.latest) || !ui.latest.revision) { report("保存响应已收到，但尚未回读到已保存规则，请重新载入核对。", true); return; }
      loadCurrent();
      report("评分细则已保存到当前课程。这里记录的是本地规则，不是平台成绩，也不代表学习任务已完成。");
    } finally { ui.pending = ""; updateControls(); }
  }
  function exportSaved() {
    if (!isSaved(ui.latest)) return;
    const blob = new Blob([JSON.stringify(ui.latest.rules, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = node("a"); link.href = url; link.download = "课程评分细则.json";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function init(api) {
    ui.api = api;
    $("grading-source").addEventListener("input", () => markEdited(true));
    $("grading-format").addEventListener("change", () => markEdited(true));
    $("grading-file").addEventListener("change", importFile);
    $("grading-parse").addEventListener("click", parseSource);
    $("grading-form").addEventListener("input", (event) => { if (event.target.id !== "grading-reviewed") markEdited(); });
    $("grading-form").addEventListener("change", (event) => { if (event.target.id !== "grading-reviewed") markEdited(); });
    $("grading-reviewed").addEventListener("change", updateControls);
    $("grading-form").addEventListener("submit", saveDraft);
    $("grading-add").addEventListener("click", () => { if (ui.rows.length >= MAX_RULES || ui.stale || ui.pending) return; addRow({ name: "", metric: "other", period: "total", formula: "unknown", evidence: "" }); markEdited(); });
    $("grading-reload").addEventListener("click", () => { if (ui.dirty && !window.confirm("重新载入会替换当前未保存的原文与规则草稿。确认载入当前课程已保存的规则？")) return; loadCurrent(); });
    $("grading-export").addEventListener("click", exportSaved);
  }
  window.CourseGrading = { init, render, updateControls };
})();
