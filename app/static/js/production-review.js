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
    current = await request(base);
    byId("status").textContent = current.message;
    byId("choice").disabled = !current.can_confirm;
    byId("checked").checked = false;
    byId("outputs").replaceChildren();
    for (const output of current.outputs || []) {
      const entry = document.createElement("details");
      const title = document.createElement("summary");
      title.textContent = `${output.name} · ${(output.start_ms / 1000).toFixed(3)}–${(output.end_ms / 1000).toFixed(3)} 秒`;
      const video = document.createElement("video");
      video.controls = true;
      video.preload = "none";
      video.style.maxWidth = "100%";
      video.src = output.media_url;
      entry.append(title, video);
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
    const mode = panel.querySelector("input[name='production-delivery']:checked").value;
    if (!pending || pending.manifest_sha256 !== current.manifest_sha256 || pending.delivery_mode !== mode) {
      pending = {request_key: crypto.randomUUID(), manifest_sha256: current.manifest_sha256, delivery_mode: mode, confirmed: true};
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
  });
  action(refresh);
})();
