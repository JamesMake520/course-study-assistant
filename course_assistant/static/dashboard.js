"use strict";

(() => {
  const NS = "http://www.w3.org/2000/svg";
  const DAY = 86400000;
  const colors = { verified: "#31d7b8", playing: "#42c9ff", awaiting_verification: "#ffbd66", pending: "#41526f", unknown: "#647089", question: "#42c9ff", answer: "#9382ff", total: "#31d7b8" };
  const statuses = ["verified", "playing", "awaiting_verification", "pending", "unknown"];
  const labels = { verified: "已核验", playing: "学习中", awaiting_verification: "待核验", pending: "待学习", unknown: "状态未知" };
  let range = 14;
  let current = null;
  const $ = (id) => document.getElementById(id);
  const put = (id, value) => { const target = $(id); if (target) target.textContent = String(value); };
  const element = (tag, className, value) => { const item = document.createElement(tag); if (className) item.className = className; if (value !== undefined) item.textContent = String(value); return item; };
  const svgElement = (tag, attributes = {}, value) => {
    const item = document.createElementNS(NS, tag);
    Object.entries(attributes).forEach(([name, content]) => item.setAttribute(name, String(content)));
    if (value !== undefined) item.textContent = String(value);
    return item;
  };
  const nonnegativeNumber = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const nonnegativeInteger = (value) => nonnegativeNumber(value) && Number.isInteger(value);
  const goalValue = (value) => nonnegativeInteger(value) ? value : null;
  const safeLessons = (state) => Array.isArray(state.course?.lessons) ? state.course.lessons.filter((lesson) => lesson && typeof lesson === "object") : [];
  function dateStamp(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
    const stamp = Date.parse(`${value}T00:00:00.000Z`);
    if (!Number.isFinite(stamp)) return null;
    return new Date(stamp).toISOString().slice(0, 10) === value ? stamp : null;
  }
  function lessonStatus(lesson) { return statuses.includes(lesson.status) ? lesson.status : "unknown"; }

  function renderVideo(state) {
    const svg = $("video-donut");
    const legend = $("video-legend");
    if (!svg || !legend) return;
    const lessons = safeLessons(state);
    const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
    lessons.forEach((lesson) => { counts[lessonStatus(lesson)] += 1; });
    const fragment = document.createDocumentFragment();
    const total = lessons.length;
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", total ? `视频状态分布：已读取 ${total} 节，${statuses.map((status) => `${labels[status]} ${counts[status]} 节`).join("，")}` : "暂无课程目录，视频状态未知");
    fragment.append(svgElement("circle", { cx: 80, cy: 80, r: 61, fill: "none", stroke: "#233148", "stroke-width": 13 }));
    const circumference = 2 * Math.PI * 61;
    let used = 0;
    const nonempty = statuses.filter((status) => counts[status] > 0).length;
    statuses.forEach((status) => {
      if (!total || !counts[status]) return;
      const arc = counts[status] / total * circumference;
      const gap = nonempty > 1 ? Math.min(3, arc * 0.2) : 0;
      const segment = svgElement("circle", { cx: 80, cy: 80, r: 61, fill: "none", stroke: colors[status], "stroke-width": 13, "stroke-dasharray": `${Math.max(0, arc - gap)} ${circumference}`, "stroke-dashoffset": -used, transform: "rotate(-90 80 80)", class: `donut-segment ${status}` });
      segment.append(svgElement("title", {}, `${labels[status]}：${counts[status]} 节`));
      fragment.append(segment);
      used += arc;
    });
    svg.replaceChildren(fragment);
    put("video-ring-value", total ? `${Math.round(counts.verified / total * 100)}%` : "—");
    put("video-ring-label", total ? "已核验占比" : "暂无目录");
    const legendRows = document.createDocumentFragment();
    statuses.forEach((status) => {
      if (status === "unknown" && !counts[status]) return;
      const row = element("div", "legend-item");
      const dot = element("span", "legend-dot"); dot.style.backgroundColor = colors[status];
      row.append(dot, element("span", "legend-name", labels[status]), element("strong", "legend-value", total ? counts[status] : "—"));
      legendRows.append(row);
    });
    legend.replaceChildren(legendRows);
  }

  function renderGoals(state) {
    const target = $("qa-goals-chart");
    if (!target) return;
    const qa = state.qa || {};
    const settings = state.settings || {};
    const currentDay = dateStamp(state.today) !== null && qa.date === state.today;
    const known = qa.known === true && currentDay && nonnegativeInteger(qa.questions) && nonnegativeInteger(qa.answers);
    const fragment = document.createDocumentFragment();
    [{ kind: "question", name: "今日提问", value: qa.questions, goal: goalValue(settings.question_goal) }, { kind: "answer", name: "今日回答", value: qa.answers, goal: goalValue(settings.answer_goal) }].forEach((metric) => {
      const row = element("div", `goal-row ${metric.kind}${known ? "" : " unknown"}`);
      const top = element("div", "goal-top");
      const value = known ? Math.floor(metric.value) : null;
      top.append(element("span", "goal-name", metric.name), element("strong", "goal-value", known ? `${value} / ${metric.goal === null ? "—" : metric.goal}` : "未知"));
      const track = element("div", "goal-track");
      const fill = element("div", "goal-fill");
      const percent = known && metric.goal !== null && metric.goal > 0 ? Math.min(100, value / metric.goal * 100) : 0;
      fill.style.width = `${percent}%`; fill.style.backgroundColor = colors[metric.kind];
      track.append(fill);
      if (known && metric.goal !== null && metric.goal > 0) {
        track.setAttribute("role", "progressbar"); track.setAttribute("aria-label", metric.name); track.setAttribute("aria-valuemin", "0"); track.setAttribute("aria-valuemax", String(metric.goal)); track.setAttribute("aria-valuenow", String(Math.min(value, metric.goal))); track.setAttribute("aria-valuetext", `已核验 ${value} 条，目标 ${metric.goal} 条`);
      } else {
        track.setAttribute("role", "img"); track.setAttribute("aria-label", !known ? `${metric.name}尚未核验` : metric.goal === 0 ? "目标为零，不计算完成比例" : "目标未知，不显示完成比例");
      }
      const note = !known ? "今日个人记录尚未核验" : metric.goal === null ? "目标未知" : metric.goal === 0 ? "目标为 0 · 不计算完成比例" : value >= metric.goal ? "今日目标已达到" : `距目标还需 ${metric.goal - value} 条`;
      row.append(top, track, element("p", "goal-note", note));
      fragment.append(row);
    });
    target.replaceChildren(fragment);
  }

  function trendDays(state) {
    if (!Array.isArray(state.drafts)) return null;
    const end = dateStamp(state.today);
    if (end === null) return null;
    const days = Array.from({ length: range }, (_, index) => ({ date: new Date(end - (range - 1 - index) * DAY).toISOString().slice(0, 10), question: 0, answer: 0 }));
    const byDate = new Map(days.map((day) => [day.date, day]));
    const drafts = Array.isArray(state.drafts) ? state.drafts : [];
    drafts.forEach((draft) => {
      if (!draft || draft.status !== "verified" || !["question", "answer"].includes(draft.kind) || dateStamp(draft.date) === null) return;
      const day = byDate.get(draft.date);
      if (day) day[draft.kind] += 1;
    });
    return days;
  }
  function renderTrend(state) {
    const svg = $("trend-chart");
    if (!svg) return;
    const days = trendDays(state);
    const total = days ? days.reduce((sum, day) => sum + day.question + day.answer, 0) : null;
    put("trend-total", total === null ? "—" : total);
    put("trend-period", days ? `${days[0].date.slice(5).replace("-", "/")} — ${days[days.length - 1].date.slice(5).replace("-", "/")}` : !Array.isArray(state.drafts) ? "本机记录尚未读取" : "日期尚未核验");
    const empty = $("trend-empty");
    if (empty) { empty.hidden = total !== null && total > 0; empty.textContent = total === null ? !Array.isArray(state.drafts) ? "本机记录尚未读取" : "日期未知，暂不能汇总" : `近 ${range} 天暂无本机已核验问答记录`; }
    svg.setAttribute("role", "img");
    svg.setAttribute("data-range", String(range));
    svg.setAttribute("data-record-total", total === null ? "unknown" : String(total));
    svg.setAttribute("aria-label", days ? `当前课程近 ${range} 天本机已核验问答，共 ${total} 条；按草稿创建或最近保存日期分组，非核验日或平台历史总量` : "日期或本机记录尚未读取，问答分布暂不可用");
    const fragment = document.createDocumentFragment();
    fragment.append(svgElement("title", {}, "当前课程 · 本机已核验问答；按草稿创建 / 最近保存日分组，非核验日或平台历史总量"));
    const area = { x: 34, y: 12, width: 748, height: 132 };
    const displayWidth = svg.getBoundingClientRect().width || 560;
    const axisFont = Math.max(12, Math.round(8000 / displayWidth));
    const baseline = area.y + area.height;
    const maxValue = days ? Math.max(0, ...days.map((day) => day.question + day.answer)) : 0;
    const step = Math.max(1, Math.ceil(maxValue / 4));
    const maximum = step * 4;
    for (let index = 0; index <= 4; index += 1) {
      const y = baseline - area.height * index / 4;
      fragment.append(svgElement("line", { x1: area.x, y1: y, x2: area.x + area.width, y2: y, stroke: "#25334a", "stroke-width": 1, "stroke-dasharray": index ? "3 5" : "none", class: "chart-grid" }));
      fragment.append(svgElement("text", { x: area.x - 11, y: y + 3, fill: "#8fa7c5", "font-size": axisFont, style: `font-size:${axisFont}px`, "text-anchor": "end", class: "chart-axis" }, index * step));
    }
    if (days) {
      const slot = area.width / days.length;
      const barWidth = Math.min(26, slot * 0.53);
      const points = [];
      const markers = [];
      const stride = days.length <= 7 ? 1 : days.length <= 14 ? 2 : 5;
      days.forEach((day, index) => {
        const x = area.x + slot * (index + 0.5);
        const qHeight = day.question / maximum * area.height;
        const aHeight = day.answer / maximum * area.height;
        const dayTotal = day.question + day.answer;
        const y = baseline - qHeight - aHeight;
        points.push(`${x},${y}`);
        const group = svgElement("g", { class: "trend-day", "data-date": day.date, "data-question": day.question, "data-answer": day.answer, "aria-label": `${day.date}：提问 ${day.question} 条，回答 ${day.answer} 条，合计 ${dayTotal} 条` });
        group.append(svgElement("title", {}, `${day.date}  提问 ${day.question} · 回答 ${day.answer} · 合计 ${dayTotal}`));
        if (day.question) group.append(svgElement("rect", { x: x - barWidth / 2, y: baseline - qHeight, width: barWidth, height: qHeight, rx: 2, fill: colors.question, opacity: 0.85, class: "trend-bar-question" }));
        if (day.answer) group.append(svgElement("rect", { x: x - barWidth / 2, y, width: barWidth, height: aHeight, rx: 2, fill: colors.answer, opacity: 0.85, class: "trend-bar-answer" }));
        group.append(svgElement("rect", { x: x - slot / 2, y: area.y, width: slot, height: area.height, fill: "transparent", class: "trend-hit-area" }));
        fragment.append(group);
        if (total && dayTotal) {
          const point = svgElement("circle", { cx: x, cy: y, r: days.length > 14 ? 2.5 : 3, fill: colors.total, stroke: "#0e182b", "stroke-width": 1.5, class: "trend-point" });
          point.append(svgElement("title", {}, `${day.date} 合计 ${dayTotal} 条`)); markers.push(point);
        }
        if (index === days.length - 1 || (index % stride === 0 && days.length - 1 - index >= stride)) fragment.append(svgElement("text", { x, y: baseline + 23, fill: "#8fa7c5", "font-size": axisFont, style: `font-size:${axisFont}px`, "text-anchor": "middle", class: "chart-axis" }, day.date.slice(5).replace("-", "/")));
      });
      if (total) {
        fragment.append(svgElement("polyline", { points: points.join(" "), fill: "none", stroke: colors.total, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", "pointer-events": "none", class: "chart-line" }));
        markers.forEach((marker) => fragment.append(marker));
      }
    }
    svg.replaceChildren(fragment);
    document.querySelectorAll("[data-trend-range]").forEach((button) => {
      const selected = Number(button.dataset.trendRange) === range;
      button.classList.toggle("active", selected); button.setAttribute("aria-pressed", String(selected));
    });
  }

  function renderCourse(state) {
    const body = $("course-mini-table");
    if (!body) return;
    const lessons = safeLessons(state);
    const visible = lessons.slice(0, 5);
    put("course-mini-note", lessons.length ? `前 ${visible.length} 节 / 已读取 ${lessons.length} 节` : "尚未读取课程目录");
    if (!visible.length) {
      const row = element("tr"); const cell = element("td", "table-empty", "暂无课程数据 · 打开课程后读取目录"); cell.colSpan = 4; row.append(cell); body.replaceChildren(row); return;
    }
    const fragment = document.createDocumentFragment();
    visible.forEach((lesson, index) => {
      const status = lessonStatus(lesson);
      const row = element("tr");
      const title = element("td", "mini-lesson-title", lesson.title || "未命名视频"); title.title = String(lesson.title || "未命名视频");
      const progressCell = element("td"); const progress = element("div", "mini-progress"); const track = element("div", "mini-progress-track"); const fill = element("div", "mini-progress-fill");
      const known = nonnegativeNumber(lesson.progress) && lesson.progress <= 100;
      const value = known ? Math.floor(lesson.progress) : null;
      fill.style.width = `${value || 0}%`; fill.style.backgroundColor = colors[status]; track.append(fill);
      progress.append(track, element("span", "", known ? `${value}%` : "—")); progressCell.append(progress);
      const statusCell = element("td"); statusCell.append(element("span", `status-pill ${status}`, labels[status]));
      row.append(element("td", "mini-lesson-index", String(index + 1).padStart(2, "0")), title, progressCell, statusCell);
      fragment.append(row);
    });
    body.replaceChildren(fragment);
  }

  function bindRanges() {
    document.querySelectorAll("[data-trend-range]").forEach((button) => {
      button.addEventListener("click", () => {
        const selected = Number(button.dataset.trendRange);
        if (![7, 14, 30].includes(selected)) return;
        range = selected;
        if (current) renderTrend(current);
      });
    });
  }
  window.CourseDashboard = Object.freeze({
    render(state) {
      if (!state || typeof state !== "object") return;
      current = state;
      renderVideo(state); renderGoals(state); renderTrend(state); renderCourse(state);
    }
  });
  window.addEventListener("resize", () => { if (current) renderTrend(current); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindRanges, { once: true });
  else bindRanges();
})();
