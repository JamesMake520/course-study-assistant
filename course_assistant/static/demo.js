"use strict";
(() => {
  const storageKey = "course-study-assistant-demo-v1";
  const today = () => new Date().toLocaleDateString("sv-SE", {timeZone: "Asia/Shanghai"});
  const normalize = s => String(s || "").normalize("NFKC").replace(/\s+/g, "").trim();
  let saved;
  try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { saved = {}; }
  const state = {completed: saved.completed || {}, records: Array.isArray(saved.records) ? saved.records : []};
  const persist = () => localStorage.setItem(storageKey, JSON.stringify(state));
  const video = document.querySelector("video");
  let active = "demo-1";
  function drawLessons() {
    document.querySelectorAll("[data-lesson-id]").forEach(row => {
      const done = Boolean(state.completed[row.dataset.lessonId]);
      const progress = done ? 100 : row.dataset.lessonId === active && video.duration ? Math.min(99, Math.floor(video.currentTime / video.duration * 100)) : 0;
      row.dataset.progress = String(progress);
      row.dataset.status = done ? "verified" : row.dataset.lessonId === active && !video.paused ? "playing" : "pending";
      row.dataset.active = String(row.dataset.lessonId === active);
      row.querySelector("[data-progress-text]").textContent = `${done ? "已完整播放（演示）" : "未完成"} · ${progress}%`;
    });
  }
  function drawRecords() {
    const container = document.querySelector("#records"); container.replaceChildren();
    state.records.slice().reverse().forEach(record => {
      const item = document.createElement("article");
      item.dataset.record = "true"; item.dataset.id = record.id; item.dataset.kind = record.kind;
      item.dataset.date = record.date; item.dataset.targetUrl = record.target_url || "";
      item.id = `record-${record.id}`;
      const title = document.createElement("h3"); title.dataset.recordTitle = "true"; title.textContent = record.title || (record.kind === "question" ? "演示提问" : "演示回答");
      const body = document.createElement("p"); body.dataset.recordBody = "true"; body.textContent = record.body;
      const date = document.createElement("time"); date.dateTime = record.date; date.textContent = `${record.kind === "question" ? "我的提问" : "我的回答"} · 发表于${record.date}`;
      const link = document.createElement("a"); link.href = `#record-${record.id}`; link.dataset.recordLink = "true"; link.textContent = "记录定位";
      item.append(title, body, date, document.createTextNode(" · "), link); container.append(item);
    });
    const records = state.records.filter(record => record.date === today());
    document.querySelector("#qa-counts").textContent = `今日演示记录：${records.filter(r => r.kind === "question").length} 个提问，${records.filter(r => r.kind === "answer").length} 个回答`;
  }
  document.querySelectorAll("[data-select-lesson]").forEach(button => button.addEventListener("click", () => {
    video.pause(); active = button.dataset.selectLesson; video.src = `/static/demo.mp4?lesson=${encodeURIComponent(active)}`; video.load();
    document.querySelector("#video-status").textContent = `当前：${button.textContent}`; drawLessons();
  }));
  document.querySelector("[data-play]").addEventListener("click", () => video.play().catch(() => { document.querySelector("#video-status").textContent = "浏览器未能播放视频，请检查视频资源后手动点击播放。"; }));
  document.querySelector("[data-pause]").addEventListener("click", () => video.pause());
  ["timeupdate", "play", "pause", "loadedmetadata"].forEach(event => video.addEventListener(event, drawLessons));
  video.addEventListener("ended", () => { state.completed[active] = new Date().toISOString(); persist(); drawLessons(); document.querySelector("#video-status").textContent = "当前视频已完整播放，演示完成记录已保存。"; });
  document.querySelector("#qa-form").addEventListener("submit", event => {
    event.preventDefault(); const form = new FormData(event.target);
    const record = {id: String(form.get("draft_id") || crypto.randomUUID()), kind: String(form.get("kind")), title: String(form.get("title") || "").trim(), body: String(form.get("body") || "").trim(), target_url: String(form.get("target_url") || "").trim(), date: today()};
    const status = document.querySelector("#publish-status");
    if (!record.body || (record.kind === "answer" && !record.target_url)) { status.textContent = "请填写正文；回答还需要目标链接。"; return; }
    const existing = state.records.find(r => r.id === record.id || (r.kind === record.kind && normalize(r.body) === normalize(record.body) && (r.kind === "question" || r.target_url === record.target_url)));
    if (existing) { status.textContent = "这条内容已经在演示个人记录中，已阻止重复发布。"; return; }
    state.records.push(record); persist(); drawRecords(); status.textContent = "已发布到演示个人记录（不是智慧树记录）。";
  });
  drawLessons(); drawRecords();
})();
