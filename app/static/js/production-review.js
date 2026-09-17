(() => {
  const panel = document.getElementById("production-review");
  if (!panel) return;
  const task = panel.dataset.taskId;
  const base = `/api/production-review/${encodeURIComponent(task)}`;
  const byId = (suffix) => document.getElementById(`production-review-${suffix}`);
  let current = null;
  let pending = null;
  let busy = false;
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "操作失败，请刷新后核对");
    return value;
  }
  async function refresh() {
    current = null;
    byId("choice").disabled = true;
    byId("prepare").hidden = true;
    byId("subtitles").hidden = true;
    byId("subtitle-link").hidden = true;
    byId("publish-link").hidden = true;
    current = await request(base);
    byId("status").textContent = current.message;
    panel.dataset.state = current.approved ? "approved" : current.can_confirm ? "review" : "waiting";
    const mode = current.configured_delivery_mode;
    byId("policy").textContent = mode === "original" ? "跳过字幕 · 保留原视频画面" : mode === "subtitled" ? "新增字幕 · 审核后制作" : "字幕设置暂不可用";
    byId("policy-note").textContent = mode ?
      `${current.delivery_policy_source === "previous_review" ? "沿用此前已确认的字幕方式。" : "已在创建任务时确定，无需重复选择。"}${mode === "original" ? "已有字幕保留，不会新增或烧录字幕。" : "成片确认后继续核对字幕内容。"}` : "请刷新后重试，暂不能确认成片。";
    byId("choice").disabled = !current.can_confirm;
    byId("publish-link").hidden = !current.blocking_publish_count;
    byId("choice").hidden = !!current.approved;
    byId("checked").checked = false;
    byId("empty").hidden = !!current.outputs?.length;
    byId("empty").querySelector("strong").textContent = current.processing ? "等待后台完成后检查成片" : "当前还没有可确认的成片";
    byId("empty").querySelector("p").textContent = current.processing ? "本任务正在处理或排队，无需重复生成。完成后点击刷新成片。" : "如果修改过片段，请先保存选择并生成最新切片，再回来检查。";
    byId("empty").querySelector("a").hidden = !!current.processing;
    byId("outputs").replaceChildren();
    for (const output of current.outputs || []) {
      const entry = document.createElement("details");
      const title = document.createElement("summary");
      title.textContent = output.name;
      const duration = document.createElement("p");
      duration.className = "production-review-note";
      duration.textContent = `${((output.end_ms - output.start_ms) / 1000).toFixed(1)} 秒 · 原片 ${(output.start_ms / 1000).toFixed(1)}–${(output.end_ms / 1000).toFixed(1)} 秒`;
      const video = document.createElement("video");
      video.controls = true;
      video.preload = "none";
      video.style.maxWidth = "100%";
      video.src = output.media_url;
      entry.append(title, duration, video);
      byId("outputs").append(entry);
    }
    byId("subtitles").hidden = !(current.approved && current.delivery_mode === "subtitled");
    byId("subtitle-link").hidden = byId("subtitles").hidden;
    byId("prepare").hidden = !current.ready;
  }
  async function action(fn) {
    if (busy) return;
    busy = true;
    panel.setAttribute("aria-busy", "true");
    try { await fn(); }
    catch (error) { byId("status").textContent = error.message; }
    finally { busy = false; panel.removeAttribute("aria-busy"); }
  }
  byId("refresh").onclick = () => action(refresh);
  byId("confirm").onclick = () => action(async () => {
    if (!byId("checked").checked || !current?.can_confirm) throw new Error("请先逐条检查实际成片，再勾选确认");
    if (!pending || pending.manifest_sha256 !== current.manifest_sha256) {
      pending = {request_key: crypto.randomUUID(), manifest_sha256: current.manifest_sha256, confirmed: true};
    }
    await request(`${base}/confirm`, pending);
    pending = null;
    await refresh();
  });
  byId("subtitles").onclick = () => action(async () => {
    await request(`${base}/prepare-subtitles`, {});
    window.location.assign(`/subtitles/${encodeURIComponent(task)}`);
  });
  byId("prepare").onclick = () => action(async () => {
    const result = await request(`/api/publish/tasks/${encodeURIComponent(task)}/sync`, {});
    await refresh();
    byId("status").textContent = [result.message, ...(result.errors || []), ...(result.warnings || []),
      "请在发送中心检查文案、封面，再单独决定排期。"].join(" ");
    if (!(result.errors || []).length) {
      window.location.assign(`/publish?task_id=${encodeURIComponent(task)}&tab=content`);
    }
  });
  action(refresh);
})();
