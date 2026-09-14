(() => {
  const page = document.querySelector("#visual-evidence-page");
  if (!page) return;
  const list = document.querySelector("#visual-evidence-list");
  const message = document.querySelector("#visual-message");
  const more = document.querySelector("#visual-more");
  const taskId = encodeURIComponent(page.dataset.taskId);
  const labels = { disabled: "关闭", pending: "待验证", completed: "完成", partial: "部分可用", unavailable: "不可用", uncertain: "结果不确定", retryable_failed: "确定未调用，已降级" };
  let offset = 0;
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  const summary = JSON.parse(document.querySelector("#visual-summary-data").textContent);
  document.querySelector("#visual-summary").textContent = summary.status
    ? `${page.dataset.runId ? "所选" : "最近一次"}分析：视觉${labels[summary.status] || summary.status} · ${summary.verified_count || 0}/${summary.candidate_count || 0} 个候选有证据${summary.global_status ? ` · 综合评审${labels[summary.global_status] || summary.global_status}` : ""}`
    : "此任务尚无已记录视觉策略的分析结果；下方可查看未完成 Job 已保留的证据。";
  const unavailable = document.querySelector("#visual-unavailable");
  if (summary.global_reason) unavailable.append(el("p", `综合评审说明：${summary.global_reason}`));
  for (const item of summary.unavailable || []) unavailable.append(el("p", `${item.source_id}：${item.reason || "未验证"}`));

  function card(item) {
    const article = el("article", null, "panel");
    article.dataset.evidenceId = item.id;
    let refreshing = false;
    async function refreshMissing() {
      if (refreshing) return;
      refreshing = true;
      try {
        const response = await fetch(`/api/tasks/${taskId}/visual-evidence/${encodeURIComponent(item.id)}`);
        if (!response.ok) return;
        const current = await response.json();
        if (current.cache_cleaned_at || current.pinned !== item.pinned) article.replaceWith(card(current));
      } catch (_) { /* 保留原证据与缺失提示，不重发模型请求。 */ }
    }
    article.append(el("h2", `${item.candidate_key} · ${labels[item.status] || item.status}`));
    article.append(el("p", `${item.provider} / ${item.model} · Run ${item.analysis_run_id || "尚未提交"} · ${item.created_at}`, "visual-evidence-meta"));
    if (item.failure_reason) article.append(el("p", item.failure_reason));
    if (item.cache_cleanup_error) article.append(el("p", `图片清理未完成：${item.cache_cleanup_error}`));
    const frameStatus = el("p", item.cache_cleaned_at ? `图片已于 ${item.cache_cleaned_at} 清理，结构证据继续保留。` : item.pinned ? "图片已固定" : "图片按保留期自动清理");
    article.append(frameStatus);
    const frames = el("div", null, "visual-frames");
    for (const [index, frame] of item.frames.entries()) {
      const figure = el("figure");
      const caption = el("figcaption", `图 ${index + 1} · ${Number(frame.actual_timestamp_seconds).toFixed(2)} 秒 · ${frame.role} / ${frame.source}`);
      if (!item.cache_cleaned_at) {
        const img = el("img");
        img.loading = "lazy";
        img.src = frame.url;
        img.alt = `候选关键帧 ${index + 1}，原片 ${Number(frame.actual_timestamp_seconds).toFixed(2)} 秒`;
        img.addEventListener("error", () => { img.replaceWith(el("p", "图片不可用；不会重新请求模型")); refreshMissing(); });
        figure.append(img);
      }
      figure.append(caption);
      frames.append(figure);
    }
    article.append(frames);
    for (const observation of item.response?.observations || []) {
      article.append(el("p", `图 ${observation.image_indices.map(i => i + 1).join("、")}：${observation.description}（置信 ${observation.confidence}）`));
    }
    for (const limitation of item.response?.limitations || []) article.append(el("p", `限制：${limitation}`, "form-hint"));
    if (item.frames.length && !item.cache_cleaned_at) {
      const pin = el("button", item.pinned ? "取消固定图片" : "固定图片，暂停自动清理", "secondary-button");
      pin.type = "button";
      pin.addEventListener("click", async () => {
        pin.disabled = true;
        try {
          const response = await fetch(`/api/tasks/${taskId}/visual-evidence/${encodeURIComponent(item.id)}/pin`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pinned: !item.pinned }) });
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "固定失败");
          item.pinned = data.pinned;
          pin.textContent = item.pinned ? "取消固定图片" : "固定图片，暂停自动清理";
          frameStatus.textContent = item.pinned ? "图片已固定" : "图片按保留期自动清理";
          message.textContent = "图片保留设置已保存。";
        } catch (error) { message.textContent = error.message; await refreshMissing(); }
        finally { pin.disabled = false; }
      });
      article.append(pin);
    }
    const details = el("details");
    details.append(el("summary", "来源与校验信息"));
    for (const [label, value] of [["Job", item.workflow_job_id], ["原片 SHA-256", item.sampling.source_sha256], ["采样器", item.sampling.sampler_version], ["请求 SHA-256", item.request_fingerprint], ["响应 SHA-256", item.raw_response_sha256], ["调用状态", item.call_status]]) {
      details.append(el("p", `${label}：${value || "无"}`));
    }
    article.append(details);
    return article;
  }
  async function load() {
    more.disabled = true;
    try {
      const params = new URLSearchParams({ offset: String(offset), limit: "20" });
      if (page.dataset.runId) params.set("run_id", page.dataset.runId);
      const response = await fetch(`/api/tasks/${taskId}/visual-evidence?${params}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "读取视觉证据失败");
      for (const item of data.items) list.append(card(item));
      offset += data.items.length;
      more.hidden = offset >= data.total;
      message.textContent = data.total ? `已显示 ${offset}/${data.total} 条视觉记录。` : "没有已采样的视觉记录。关闭、未支持或超预算的原因见上方分析说明。";
    } catch (error) { message.textContent = error.message; }
    finally { more.disabled = false; }
  }
  more.addEventListener("click", load);
  load();
})();
