(() => {
  const panel = document.getElementById("production-review");
  if (!panel) return;
  const task = panel.dataset.taskId;
  const base = `/api/production-review/${encodeURIComponent(task)}`;
  const byId = (suffix) => document.getElementById(`production-review-${suffix}`);
  const form = document.getElementById("clip-review-form");
  const generate = document.getElementById("generate-clips-button");
  let current = null;
  let pending = null;
  let busy = false;
  let sequence = 0;
  let progress = "";
  let errorMessage = "";
  const dirty = () => form?.dataset.dirty === "true";
  function renderState() {
    const editing = dirty();
    const working = form?.dataset.busy === "true";
    const locked = busy || working || !current;
    const needsCut = editing || (current && !current.outputs?.length && !current.processing);
    byId("choice").disabled = locked || editing || !current?.can_confirm;
    byId("choice").hidden = editing || !!current?.approved || !!needsCut;
    byId("prepare").hidden = editing || !current?.ready;
    byId("subtitles").hidden = editing || !(current?.approved && current.delivery_mode === "subtitled");
    byId("subtitle-link").hidden = byId("subtitles").hidden || locked;
    byId("publish-link").hidden = !current?.blocking_publish_count || !!current?.approved;
    byId("generate").hidden = !needsCut;
    byId("generate").disabled = locked || !!generate?.disabled;
    byId("generate").textContent = working && !busy ? "正在处理选择 / 生成成片…" : "保存选择并生成新版成片";
    byId("confirm").textContent = current?.configured_delivery_mode === "original" ? "确认成片并进入内容准备 →" : "确认成片";
    for (const id of ["prepare", "subtitles"]) byId(id).disabled = locked;
    byId("refresh").disabled = busy || working;
    byId("prepare").textContent = busy ? "正在准备内容…" : "进入内容准备 →";
    byId("outputs").hidden = editing;
    if (editing) byId("checked").checked = false;
    panel.dataset.state = editing || !current?.approved ? "waiting" : "approved";
    byId("status").textContent = busy ? progress : (editing ?
      `下方选择有未保存的修改（本任务共选中 ${form.dataset.enabledCount} 条）。请生成新版成片，再检查确认；此前确认不包含这次修改。` :
      errorMessage || (working ? "正在处理当前选择，请等待成片生成后再验收。" : current?.message || "正在读取成片版本…"));
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
    });
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "操作失败，请刷新后核对");
    return value;
  }
  async function refresh() {
    const version = ++sequence;
    current = null;
    errorMessage = "";
    renderState();
    const value = await request(base);
    if (version !== sequence) return;
    current = value;
    const mode = current.configured_delivery_mode;
    byId("policy").textContent = mode === "original" ? "跳过字幕 · 保留原视频画面" : mode === "subtitled" ? "新增字幕 · 审核后制作" : "字幕设置暂不可用";
    byId("policy-note").textContent = mode ?
      `${current.delivery_policy_source === "previous_review" ? "沿用此前已确认的字幕方式。" : "已在创建任务时确定，无需重复选择。"}${mode === "original" ? "确认成片后进入内容准备，不会新增或烧录字幕。" : "成片确认后继续核对字幕内容。"}` : "请刷新后重试，暂不能确认成片。";
    byId("checked").checked = false;
    byId("empty").hidden = !!current.outputs?.length;
    byId("empty").querySelector("strong").textContent = current.processing ? "等待后台完成后检查成片" : "请生成当前选择的成片";
    byId("empty").querySelector("p").textContent = current.processing ? "本任务正在处理或排队，无需重复生成。完成后点击刷新成片。" : "保存选择不会自动更新视频。点击右侧“保存选择并生成新版成片”，完成后在这里检查确认。";
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
      video.src = output.media_url;
      entry.append(title, duration, video);
      byId("outputs").append(entry);
    }
    renderState();
  }
  async function action(message, fn) {
    if (busy || form?.dataset.busy === "true") return;
    busy = true;
    progress = message;
    errorMessage = "";
    panel.setAttribute("aria-busy", "true");
    document.dispatchEvent(new CustomEvent("production-review-busy", {detail: {busy: true}}));
    renderState();
    try { await fn(); }
    catch (error) { errorMessage = error.message; }
    finally {
      busy = false;
      panel.removeAttribute("aria-busy");
      document.dispatchEvent(new CustomEvent("production-review-busy", {detail: {busy: false}}));
      renderState();
    }
  }
  async function prepare() {
    if (dirty() || !current?.ready) throw new Error("请先生成并确认当前选择的成片");
    progress = "成片已确认，正在同步内容并提取封面，请稍候；不会自动排期或发送。";
    renderState();
    const result = await request(`/api/publish/tasks/${encodeURIComponent(task)}/sync`, {});
    if ((result.errors || []).length) throw new Error(`成片确认已保留。${result.errors.join("；")} 请点击“进入内容准备”重试。`);
    // Successful handoff must not depend on a second status request.
    window.location.assign(`/publish?task_id=${encodeURIComponent(task)}&tab=content`);
  }
  byId("refresh").onclick = () => action("正在读取成片版本…", refresh);
  byId("confirm").onclick = () => action("正在保存成片确认…", async () => {
    if (dirty() || !byId("checked").checked || !current?.can_confirm) throw new Error("请先逐条检查实际成片，再勾选确认");
    if (!pending || pending.manifest_sha256 !== current.manifest_sha256) {
      pending = {request_key: crypto.randomUUID(), manifest_sha256: current.manifest_sha256, confirmed: true};
    }
    await request(`${base}/confirm`, pending);
    pending = null;
    await refresh();
    if (current?.ready) await prepare();
  });
  byId("subtitles").onclick = () => action("正在准备字幕审核…", async () => {
    if (dirty()) throw new Error("选择已变化，请先生成并确认新版成片");
    await request(`${base}/prepare-subtitles`, {});
    window.location.assign(`/subtitles/${encodeURIComponent(task)}`);
  });
  byId("prepare").onclick = () => action("正在准备内容…", prepare);
  byId("generate").onclick = () => {
    if (!generate?.disabled) {
      generate.click();
      document.getElementById("cut-job-progress")?.scrollIntoView({block: "center"});
    }
  };
  document.addEventListener("clip-review-state", renderState);
  document.addEventListener("clip-review-saved", () => refresh().catch(error => { errorMessage = error.message; renderState(); }));
  refresh().catch(error => { errorMessage = error.message; renderState(); });
})();
