const contentReviewAccount = document.querySelector("#content-review-account");
const contentReviewMessage = document.querySelector("#content-review-message");
const contentReviewImportForm = document.querySelector("#content-review-import-form");
const contentReviewFile = document.querySelector("#content-review-file");
const contentReviewPreview = document.querySelector("#content-review-preview");
const contentReviewCommit = document.querySelector("#content-review-commit");
const contentReviewSync = document.querySelector("#content-review-sync");

let previewBatchId = "";

function currentAccountId() {
  return contentReviewAccount?.value || "";
}

function showContentReviewMessage(message, tone = "info") {
  if (!contentReviewMessage) return;
  contentReviewMessage.hidden = !message;
  contentReviewMessage.textContent = message || "";
  contentReviewMessage.className = `page-alert ${tone}`;
}

function errorMessage(data, fallback) {
  if (!data) return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (data.detail && typeof data.detail.message === "string") {
    const code = data.detail.error_code ? `（${data.detail.error_code}）` : "";
    return `${data.detail.message}${code}`;
  }
  return data.message || fallback;
}

async function contentReviewApi(path, options = {}) {
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch (_error) {
    data = {};
  }
  if (!response.ok) throw new Error(errorMessage(data, "请求失败"));
  return data;
}

function textNode(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text == null || text === "" ? "—" : String(text);
  if (className) node.className = className;
  return node;
}

function formatNumber(value) {
  if (value == null) return "—";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 1 });
}

function formatPercent(value) {
  if (value == null) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatSeconds(value) {
  if (value == null) return "—";
  return `${Number(value).toFixed(1)} 秒`;
}

function formatMetric(key, value) {
  if (["completion_rate", "five_second_completion_rate", "two_second_bounce_rate", "cover_click_rate"].includes(key)) {
    return formatPercent(value);
  }
  if (key === "average_watch_seconds") return formatSeconds(value);
  return formatNumber(value);
}

function formatDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function renderSummary(summary) {
  const latestDate = document.querySelector("#content-review-latest-date");
  const syncAge = document.querySelector("#content-review-sync-age");
  if (latestDate) latestDate.textContent = summary.latest_metric_date || "暂无数据";
  if (syncAge) {
    syncAge.textContent = summary.days_since_sync == null
      ? "尚未同步"
      : summary.days_since_sync === 0 ? "今天已同步" : `${summary.days_since_sync} 天前同步`;
  }
  document.querySelectorAll("[data-metric]").forEach((card) => {
    const key = card.dataset.metric;
    const current = summary.current_period?.[key];
    const previous = summary.previous_period?.[key];
    const delta = summary.comparisons?.[key];
    card.querySelector("[data-current]").textContent = formatMetric(key, current);
    card.querySelector("[data-previous]").textContent = formatMetric(key, previous);
    const deltaNode = card.querySelector("[data-delta]");
    deltaNode.classList.remove("is-up", "is-down");
    if (delta == null) {
      deltaNode.textContent = "暂无可比基线";
    } else {
      deltaNode.textContent = `${delta >= 0 ? "↑" : "↓"} ${Math.abs(Number(delta) * 100).toFixed(1)}%`;
      deltaNode.classList.add(delta >= 0 ? "is-up" : "is-down");
    }
  });

  const body = document.querySelector("#content-review-history");
  if (!body) return;
  body.replaceChildren();
  const history = Array.from(summary.history || []).reverse();
  if (!history.length) {
    const row = document.createElement("tr");
    const cell = textNode("td", "暂无账号级数据。请先预览并确认导入抖音数据表。");
    cell.colSpan = 8;
    row.append(cell);
    body.append(row);
    return;
  }
  history.forEach((item) => {
    const row = document.createElement("tr");
    const interaction = Number(item.like_count || 0) + Number(item.share_count || 0) + Number(item.comment_count || 0);
    [
      item.metric_date,
      formatNumber(item.post_count),
      formatNumber(item.play_count),
      formatNumber(interaction),
      formatPercent(item.five_second_completion_rate),
      formatPercent(item.two_second_bounce_rate),
      formatPercent(item.cover_click_rate),
      formatSeconds(item.average_watch_seconds),
    ].forEach((value) => row.append(textNode("td", value)));
    body.append(row);
  });
}

function appendCell(row, primary, secondary = "") {
  const cell = document.createElement("td");
  cell.append(textNode("strong", primary));
  if (secondary) cell.append(textNode("small", secondary));
  row.append(cell);
  return cell;
}

function appendDetailedCell(row, primary, details = []) {
  const cell = document.createElement("td");
  cell.append(textNode("strong", primary));
  details.filter(Boolean).forEach((detail) => cell.append(textNode("small", detail)));
  row.append(cell);
  return cell;
}

function matchLabel(status) {
  return {
    matched_exact: "作品 ID 精确匹配",
    matched_unique: "标题 / 正文 + 时间唯一匹配",
    confirmed_manual: "人工确认",
    ambiguous: "存在多个候选",
    unmatched: "未匹配",
  }[status] || status || "未匹配";
}

async function updateItemMatch(snapshotId, publishJobId) {
  await contentReviewApi(`/api/content-review/item-matches/${encodeURIComponent(snapshotId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ publish_job_id: publishJobId }),
  });
  showContentReviewMessage("人工关联已保存。", "success");
  await loadContentReviewData();
}

async function removeItemMatch(snapshotId) {
  await contentReviewApi(`/api/content-review/item-matches/${encodeURIComponent(snapshotId)}`, {
    method: "DELETE",
  });
  showContentReviewMessage("错误关联已解除。", "success");
  await loadContentReviewData();
}

function renderWorks(works) {
  const body = document.querySelector("#content-review-works");
  if (!body) return;
  body.replaceChildren();
  if (!works.length) {
    const row = document.createElement("tr");
    const cell = textNode("td", "暂无作品级指标。请上传官方作品列表，或点击“自动导出并同步全部作品”。");
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
    return;
  }
  works.forEach((work) => {
    const row = document.createElement("tr");
    const isExportKey = String(work.aweme_id || "").startsWith("export:");
    const sourceName = work.metric_source_filename || "历史作品快照";
    appendDetailedCell(
      row,
      work.title || "未命名作品",
      [
        formatDateTime(work.published_at),
        `来源：${sourceName}`,
        `${work.content_genre || "体裁未知"} · ${work.audit_status || "审核状态未知"}`,
        !isExportKey && work.aweme_id ? `抖音作品 ID：${work.aweme_id}` : "",
      ],
    );
    appendDetailedCell(
      row,
      `${formatNumber(work.play_count)} 播放 · ${formatPercent(work.completion_rate)} 完播`,
      [
        `${formatNumber(work.like_count)} 赞 · ${formatNumber(work.comment_count)} 评 · ${formatNumber(work.share_count)} 转 · ${formatNumber(work.collect_count)} 藏`,
        `${formatPercent(work.five_second_completion_rate)} 5s 完播 · ${formatPercent(work.cover_click_rate)} 封面点击 · ${formatPercent(work.two_second_bounce_rate)} 2s 跳出`,
        `${formatSeconds(work.average_watch_seconds)} 平均播放 · ${formatNumber(work.home_visit_count)} 主页访问 · ${formatNumber(work.follower_gain_count)} 粉丝增量`,
      ],
    );
    appendCell(
      row,
      work.publish_title || "未关联发布记录",
      work.publish_job_id ? `${work.publish_job_id} · ${work.publish_status || ""}` : "等待匹配",
    );
    appendCell(
      row,
      work.candidate_title || "候选来源不完整",
      work.review_decision
        ? `${work.review_decision === "keep" ? "保留" : "淘汰"} · ${work.review_reason_label || "未填写原因"}`
        : "暂无审核反馈",
    );
    appendCell(
      row,
      work.analysis_run_number ? `第 ${work.analysis_run_number} 次 · ${work.provider_label || "AI"}` : "AI 来源不完整",
      work.prompt_version_number ? `${work.prompt_name || "Prompt"} v${work.prompt_version_number}` : "Prompt 来源不完整",
    );
    const matchCell = document.createElement("td");
    const tools = document.createElement("div");
    tools.className = "content-review-match-tools";
    tools.append(textNode("strong", matchLabel(work.match_status)));
    if (["ambiguous", "unmatched"].includes(work.match_status)) {
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "输入发布记录 ID";
      input.setAttribute("aria-label", "发布记录 ID");
      const button = textNode("button", "人工确认", "secondary-button compact-button");
      button.type = "button";
      button.addEventListener("click", async () => {
        const jobId = input.value.trim();
        if (!jobId) {
          showContentReviewMessage("请先填写要关联的发布记录 ID。", "error");
          return;
        }
        button.disabled = true;
        try {
          await updateItemMatch(work.id, jobId);
        } catch (error) {
          showContentReviewMessage(`关联失败：${error.message}`, "error");
        } finally {
          button.disabled = false;
        }
      });
      tools.append(input, button);
    } else if (work.publish_job_id) {
      const button = textNode("button", "解除关联", "link-button danger");
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await removeItemMatch(work.id);
        } catch (error) {
          showContentReviewMessage(`解除失败：${error.message}`, "error");
        } finally {
          button.disabled = false;
        }
      });
      tools.append(button);
    }
    matchCell.append(tools);
    row.append(matchCell);
    body.append(row);
  });
}

function promptMetric(label, value) {
  const wrapper = document.createElement("div");
  wrapper.append(textNode("dt", label), textNode("dd", value));
  return wrapper;
}

function renderPromptComparison(data) {
  const cycles = document.querySelector("#content-review-cycles");
  const message = document.querySelector("#content-review-prompt-message");
  const list = document.querySelector("#content-review-prompts");
  if (cycles) cycles.textContent = `${data.completed_cycles || 0} / ${data.minimum_cycles || 3} 周期`;
  if (message) message.textContent = data.message || "数据不足。";
  if (!list) return;
  list.replaceChildren();
  if (!(data.versions || []).length) {
    list.append(textNode("p", "还没有带准确作品归因的 Prompt 版本。", "empty-note"));
    return;
  }
  Array.from(data.versions).reverse().forEach((version) => {
    const item = document.createElement("article");
    item.className = "content-review-prompt-item";
    const header = document.createElement("header");
    header.append(
      textNode("strong", `${version.prompt_name} · v${version.version_number}`),
      textNode("span", version.evaluable ? "可评估" : "数据不足", "status-pill"),
    );
    const metrics = document.createElement("dl");
    metrics.append(
      promptMetric("准确发布", `${version.accurate_published_count} 条`),
      promptMetric("保留率", formatPercent(version.keep_rate)),
      promptMetric("发布率", formatPercent(version.publish_rate)),
      promptMetric("播放中位数", formatNumber(version.median_play_count)),
      promptMetric("5 秒完播", formatPercent(version.five_second_completion_rate)),
      promptMetric("2 秒跳出", formatPercent(version.two_second_bounce_rate)),
      promptMetric("平均观看 / 片长", formatPercent(version.average_watch_ratio)),
      promptMetric("互动率", formatPercent(version.interaction_rate)),
    );
    item.append(header, metrics);
    list.append(item);
  });
}

function renderImports(imports) {
  const list = document.querySelector("#content-review-imports");
  if (!list) return;
  list.replaceChildren();
  if (!imports.length) {
    list.append(textNode("p", "暂无同步记录。", "empty-note"));
    return;
  }
  imports.forEach((batch) => {
    const item = document.createElement("article");
    item.className = "content-review-import-item";
    const header = document.createElement("header");
    header.append(
      textNode("strong", batch.source_kind === "douyin_item_export" ? "官方作品列表" : batch.source_filename),
      textNode("span", batch.status, "status-pill"),
    );
    const summary = batch.source_kind === "douyin_item_export"
      ? `${batch.row_count || 0} 条作品 · 唯一匹配 ${batch.matched_count || 0} · 歧义 ${batch.ambiguous_count || 0} · 未匹配 ${Math.max(0, Number(batch.row_count || 0) - Number(batch.matched_count || 0) - Number(batch.ambiguous_count || 0))}`
      : `${batch.row_count || 0} 天账号趋势 · 未归因历史基线`;
    item.append(
      header,
      textNode(
        "small",
        `${summary} · ${batch.period_start || "—"} 至 ${batch.period_end || "—"} · ${formatDateTime(batch.committed_at || batch.created_at)}`,
      ),
    );
    list.append(item);
  });
}

async function loadContentReviewData() {
  const accountId = currentAccountId();
  if (!accountId) return;
  const query = `account_id=${encodeURIComponent(accountId)}`;
  try {
    const [summary, works, prompts, imports] = await Promise.all([
      contentReviewApi(`/api/content-review/summary?${query}&days=28`),
      contentReviewApi(`/api/content-review/works?${query}&limit=200`),
      contentReviewApi(`/api/content-review/prompt-comparison?${query}`),
      contentReviewApi(`/api/content-review/imports?${query}&limit=20`),
    ]);
    renderSummary(summary);
    renderWorks(works.works || []);
    renderPromptComparison(prompts);
    renderImports(imports.imports || []);
  } catch (error) {
    showContentReviewMessage(`读取复盘数据失败：${error.message}`, "error");
  }
}

contentReviewImportForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = contentReviewFile?.files?.[0];
  if (!file) {
    showContentReviewMessage("请先选择 .xlsx 或 .csv 文件。", "error");
    return;
  }
  const button = contentReviewImportForm.querySelector("button[type='submit']");
  button.disabled = true;
  showContentReviewMessage("正在只读解析并校验数据表…", "info");
  const formData = new FormData();
  formData.append("file", file);
  formData.append("account_id", currentAccountId());
  try {
    const data = await contentReviewApi("/api/content-review/imports/preview", {
      method: "POST",
      body: formData,
    });
    previewBatchId = data.batch_id || "";
    document.querySelector("#content-review-file-status").textContent = data.already_imported ? "已导入" : "预览通过";
    if (contentReviewPreview) contentReviewPreview.hidden = data.already_imported;
    if (!data.already_imported) {
      contentReviewPreview.querySelector("[data-preview-filename]").textContent = data.filename || file.name;
      contentReviewPreview.querySelector("[data-preview-type]").textContent = data.report_type === "douyin_item_export" ? "官方作品列表" : "账号趋势表";
      contentReviewPreview.querySelector("[data-preview-period]").textContent = `${data.period_start} 至 ${data.period_end}`;
      contentReviewPreview.querySelector("[data-preview-rows]").textContent = `${data.row_count} 行`;
      contentReviewPreview.querySelector("[data-preview-attribution]").textContent = data.report_type === "douyin_item_export" ? "仅唯一证据自动关联" : "未归因账号历史基线";
    }
    showContentReviewMessage(data.message, data.already_imported ? "info" : "success");
  } catch (error) {
    previewBatchId = "";
    if (contentReviewPreview) contentReviewPreview.hidden = true;
    showContentReviewMessage(`预览失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
});

contentReviewCommit?.addEventListener("click", async () => {
  if (!previewBatchId) return;
  contentReviewCommit.disabled = true;
  try {
    const data = await contentReviewApi(`/api/content-review/imports/${encodeURIComponent(previewBatchId)}/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    showContentReviewMessage(data.message, "success");
    contentReviewPreview.hidden = true;
    previewBatchId = "";
    await loadContentReviewData();
  } catch (error) {
    showContentReviewMessage(`导入失败：${error.message}`, "error");
  } finally {
    contentReviewCommit.disabled = false;
  }
});

contentReviewSync?.addEventListener("click", async () => {
  contentReviewSync.disabled = true;
  contentReviewSync.textContent = "正在导出官方报表…";
  showContentReviewMessage("正在点击一次“导出数据”并读取全部官方作品；不会触发投稿或自动重试。", "info");
  try {
    const data = await contentReviewApi("/api/content-review/douyin/export-sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: currentAccountId() }),
    });
    showContentReviewMessage(data.message, "success");
    await loadContentReviewData();
  } catch (error) {
    showContentReviewMessage(`同步已安全停止：${error.message}`, "error");
  } finally {
    contentReviewSync.disabled = false;
    contentReviewSync.textContent = "自动导出并同步全部作品";
  }
});

contentReviewAccount?.addEventListener("change", () => {
  previewBatchId = "";
  if (contentReviewPreview) contentReviewPreview.hidden = true;
  loadContentReviewData();
});

loadContentReviewData();
