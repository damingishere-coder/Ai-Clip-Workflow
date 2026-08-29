const contentReviewAccount = document.querySelector("#content-review-account");
const contentReviewMessage = document.querySelector("#content-review-message");
const contentReviewImportForm = document.querySelector("#content-review-import-form");
const contentReviewFile = document.querySelector("#content-review-file");
const contentReviewFileName = document.querySelector("#content-review-file-name");
const contentReviewPreviewButton = document.querySelector("#content-review-preview-button");
const contentReviewPreview = document.querySelector("#content-review-preview");
const contentReviewCommit = document.querySelector("#content-review-commit");
const contentReviewSync = document.querySelector("#content-review-sync");

let previewBatchId = "";
let contentReviewLoadSequence = 0;
let contentReviewPreviewSequence = 0;
let contentReviewPreviewController = null;

function currentAccountId() {
  return contentReviewAccount?.value || "";
}

function invalidateContentReviewPreview() {
  contentReviewPreviewSequence += 1;
  contentReviewPreviewController?.abort();
  contentReviewPreviewController = null;
  previewBatchId = "";
  if (contentReviewPreview) contentReviewPreview.hidden = true;
  if (contentReviewCommit) contentReviewCommit.disabled = true;
  if (contentReviewPreviewButton) {
    contentReviewPreviewButton.disabled = !contentReviewFile?.files?.[0];
  }
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
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).formatToParts(parsed);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}`;
}

function initializeDisclosures() {
  document.querySelectorAll("[data-content-review-disclosure]").forEach((details) => {
    const key = `niuma.content-review.${details.dataset.contentReviewDisclosure}.open`;
    details.open = window.localStorage.getItem(key) === "true";
    details.addEventListener("toggle", () => {
      window.localStorage.setItem(key, details.open ? "true" : "false");
    });
  });
}

function renderSummary(summary) {
  const latestDate = document.querySelector("#content-review-latest-date");
  const syncAge = document.querySelector("#content-review-sync-age");
  const lastExport = document.querySelector("#content-review-last-export");
  const lastExportStats = document.querySelector("#content-review-last-export-stats");
  const matchSummary = document.querySelector("#content-review-match-summary");
  if (latestDate) latestDate.textContent = summary.latest_metric_date || "暂无数据";
  if (syncAge) {
    syncAge.textContent = summary.last_export_committed_at ? "已同步" : "尚未同步";
  }
  if (lastExport) {
    lastExport.textContent = summary.last_export_committed_at
      ? `上次成功导出：北京时间 ${formatDateTime(summary.last_export_committed_at)}`
      : "上次成功导出：尚未同步";
  }
  if (lastExportStats) {
    lastExportStats.textContent = summary.last_export_batch_id
      ? `${summary.last_export_row_count || 0} 条作品 · 已匹配 ${summary.last_export_matched_count || 0} · 待确认 ${summary.last_export_ambiguous_count || 0} · 未匹配 ${summary.last_export_unmatched_count || 0}`
      : "等待官方作品数据";
  }
  if (matchSummary) {
    const counts = summary.match_summary || {};
    matchSummary.textContent = `${counts.matched || 0} 已匹配 / ${counts.ambiguous || 0} 待确认 / ${counts.unmatched || 0} 未匹配`;
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
      const improvement = key === "two_second_bounce_rate" ? delta <= 0 : delta >= 0;
      deltaNode.classList.add(improvement ? "is-up" : "is-down");
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
    matched_exact: "已匹配·唯一证据",
    matched_unique: "已匹配·唯一证据",
    confirmed_manual: "已匹配·人工确认",
    ambiguous: "待人工确认·多个候选",
    unmatched: "未匹配·没有候选",
  }[status] || status || "未匹配 · 没有候选";
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
    tools.append(textNode("strong", work.match_label || matchLabel(work.match_status)));
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

function metricLabel(key) {
  return {
    play_count: "播放",
    five_second_completion_rate: "5 秒完播",
    two_second_bounce_rate: "2 秒跳出",
    completion_rate: "完播率",
    watch_ratio: "平均观看 / 片长",
  }[key] || key;
}

function formatInsightMetric(key, value) {
  if (["five_second_completion_rate", "two_second_bounce_rate", "completion_rate", "watch_ratio"].includes(key)) {
    return formatPercent(value);
  }
  return formatNumber(value);
}

async function createExperiment(recommendationId, button) {
  button.disabled = true;
  try {
    const data = await contentReviewApi("/api/content-review/experiments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: currentAccountId(), recommendation_id: recommendationId }),
    });
    showContentReviewMessage(data.message || "实验已建立。", "success");
    await loadContentReviewData();
  } catch (error) {
    showContentReviewMessage(`建立实验失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function recordExperimentDecision(experimentId, decision, button) {
  button.disabled = true;
  try {
    const data = await contentReviewApi(`/api/content-review/experiments/${encodeURIComponent(experimentId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    showContentReviewMessage(data.message || "实验结论已记录。", "success");
    await loadContentReviewData();
  } catch (error) {
    showContentReviewMessage(`记录实验失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

function renderInsights(data) {
  const count = document.querySelector("#content-review-insight-count");
  const note = document.querySelector("#content-review-insight-note");
  const list = document.querySelector("#content-review-insights");
  const recommendations = Array.from(data.recommendations || []);
  const recordedRecommendationIds = new Set(
    (data.experiments || []).map((item) => item.recommendation_id),
  );
  if (count) count.textContent = `${recommendations.length} 条建议`;
  if (note) {
    const summary = data.summary || {};
    note.textContent = `${summary.eligible_works || 0} 条作品证据完整，${summary.insufficient_works || 0} 条证据不足。${summary.cover_metric_available ? "已读取作品级封面指标。" : "官方作品级封面点击率缺失，本轮不生成封面建议。"}`;
  }
  if (!list) return;
  list.replaceChildren();
  if (!recommendations.length) {
    list.append(textNode("p", "当前没有达到规则阈值的改进建议。请先同步最新官方作品数据，或继续积累准确匹配作品。", "empty-note"));
    return;
  }
  recommendations.forEach((recommendation) => {
    const item = document.createElement("article");
    item.className = "content-review-insight-item";
    const header = document.createElement("header");
    const identity = document.createElement("div");
    identity.append(
      textNode("strong", recommendation.title),
      textNode("small", recommendation.source_work?.title || "未命名作品"),
    );
    header.append(identity, textNode("span", recommendation.baseline?.cohort?.label || "同类对照", "status-pill"));
    const hypothesis = textNode("p", recommendation.hypothesis);
    const evidence = document.createElement("div");
    evidence.className = "content-review-insight-evidence";
    Object.entries(recommendation.evidence || {}).forEach(([key, value]) => {
      const metric = document.createElement("div");
      metric.append(textNode("small", metricLabel(key)), textNode("strong", formatInsightMetric(key, value)));
      evidence.append(metric);
    });
    const action = textNode("p", `建议动作：${recommendation.action_text}`, "content-review-insight-action");
    const primaryBenchmark = recommendation.comparison_interval?.[recommendation.primary_metric] || {};
    const benchmark = textNode(
      "p",
      `同类对照 ${recommendation.baseline?.work_count || 0} 条：${metricLabel(recommendation.primary_metric)} P25 ${formatInsightMetric(recommendation.primary_metric, primaryBenchmark.p25)} / 中位数 ${formatInsightMetric(recommendation.primary_metric, primaryBenchmark.median)} / P75 ${formatInsightMetric(recommendation.primary_metric, primaryBenchmark.p75)}。`,
      "content-review-insight-benchmark",
    );
    const actions = document.createElement("div");
    actions.className = "button-row";
    const recorded = recordedRecommendationIds.has(recommendation.recommendation_id);
    const button = textNode("button", recorded ? "已记录实验" : "采纳为下轮实验", recorded ? "secondary-button" : "primary-button");
    button.type = "button";
    button.disabled = recorded;
    if (!recorded) button.addEventListener("click", () => createExperiment(recommendation.recommendation_id, button));
    actions.append(button, textNode("small", `主指标：${metricLabel(recommendation.primary_metric)} · 对照 ${recommendation.baseline?.work_count || 0} 条`));
    item.append(header, hypothesis, evidence, benchmark, action, actions);
    list.append(item);
  });
}

function experimentStageLabel(experiment) {
  if (experiment.status === "completed") {
    return { keep: "已保留改动", revert: "已决定回退", inconclusive: "结论不足" }[experiment.decision] || "已完成";
  }
  if (experiment.status === "cancelled") return "已取消";
  return {
    collecting: "收集中",
    early: "早期趋势",
    decision_ready: "可做决定",
  }[experiment.progress?.stage] || "收集中";
}

function renderExperiments(experiments) {
  const list = document.querySelector("#content-review-experiments");
  if (!list) return;
  list.replaceChildren();
  if (!(experiments || []).length) {
    list.append(textNode("p", "还没有实验。先从上方建议中选择一条，再去发送中心标记实际采用规则的作品。", "empty-note"));
    return;
  }
  experiments.forEach((experiment) => {
    const progress = experiment.progress || {};
    const item = document.createElement("article");
    item.className = `content-review-experiment-item${experiment.status === "active" ? "" : " is-completed"}`;
    const header = document.createElement("header");
    header.append(textNode("strong", experiment.title), textNode("span", experimentStageLabel(experiment), "status-pill"));
    const action = textNode("p", experiment.action_text);
    const progressGrid = document.createElement("div");
    progressGrid.className = "content-review-experiment-progress";
    [
      ["投稿前已标记", `${progress.assigned_count || 0} 条`],
      ["已有官方指标", `${progress.treatment_count || 0} / ${progress.target_sample_size || 20} 条`],
      ["冻结对照", `${progress.baseline_count || 0} / ${progress.minimum_baseline_size || 20} 条`],
      ["官方导出周", `${progress.official_export_weeks || 0} / ${progress.minimum_weeks || 3} 周`],
    ].forEach(([label, value]) => {
      const cell = document.createElement("div");
      cell.append(textNode("small", label), textNode("strong", value));
      progressGrid.append(cell);
    });
    const result = textNode(
      "p",
      !progress.trend_visible
        ? `主指标：${metricLabel(experiment.primary_metric)}；累计 10 条实验作品后才显示早期趋势。`
        : progress.treatment_primary == null
          ? `主指标：${metricLabel(experiment.primary_metric)}，等待实验作品同步官方指标。`
        : `主指标：对照 ${formatInsightMetric(experiment.primary_metric, progress.baseline_primary)} → 实验 ${formatInsightMetric(experiment.primary_metric, progress.treatment_primary)}。`,
    );
    const guardrails = textNode(
      "small",
      `护栏指标：${(experiment.guardrail_metrics || []).map(metricLabel).join("、") || "无"}`,
      "content-review-experiment-guardrails",
    );
    item.append(header, action, progressGrid, result, guardrails);
    if (experiment.status === "active") {
      const actions = document.createElement("div");
      actions.className = "button-row";
      if (progress.decision_ready) {
        [["保留改动", "keep"], ["回退", "revert"], ["结论不足", "inconclusive"]].forEach(([label, decision]) => {
          const button = textNode("button", label, decision === "keep" ? "primary-button" : "secondary-button");
          button.type = "button";
          button.addEventListener("click", () => recordExperimentDecision(experiment.id, decision, button));
          actions.append(button);
        });
      }
      const cancel = textNode("button", "取消实验", "text-button danger");
      cancel.type = "button";
      cancel.addEventListener("click", () => recordExperimentDecision(experiment.id, "cancel", cancel));
      actions.append(cancel);
      item.append(actions);
    }
    list.append(item);
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
  if (cycles) cycles.textContent = `${data.completed_cycles || 0} / ${data.minimum_cycles || 3} 个官方导出周`;
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
    const gap = textNode(
      "small",
      version.evaluable
        ? "已达到单版本评估门槛"
        : `还差 ${version.remaining_accurate_published_count ?? Math.max(0, 30 - Number(version.accurate_published_count || 0))} 条准确关联作品`,
      "content-review-sample-gap",
    );
    item.append(header, metrics, gap);
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
  const loadSequence = ++contentReviewLoadSequence;
  const query = `account_id=${encodeURIComponent(accountId)}`;
  try {
    const [summary, works, prompts, imports, insights] = await Promise.all([
      contentReviewApi(`/api/content-review/summary?${query}&days=28`),
      contentReviewApi(`/api/content-review/works?${query}&limit=200`),
      contentReviewApi(`/api/content-review/prompt-comparison?${query}`),
      contentReviewApi(`/api/content-review/imports?${query}&limit=20`),
      contentReviewApi(`/api/content-review/insights?${query}`),
    ]);
    if (loadSequence !== contentReviewLoadSequence || accountId !== currentAccountId()) return;
    renderSummary(summary);
    renderWorks(works.works || []);
    renderPromptComparison(prompts);
    renderImports(imports.imports || []);
    renderInsights(insights);
    renderExperiments(insights.experiments || []);
  } catch (error) {
    if (loadSequence !== contentReviewLoadSequence) return;
    showContentReviewMessage(`读取复盘数据失败：${error.message}`, "error");
  }
}

contentReviewFile?.addEventListener("change", () => {
  const file = contentReviewFile.files?.[0];
  invalidateContentReviewPreview();
  if (contentReviewFileName) contentReviewFileName.textContent = file?.name || "未选择文件";
  if (contentReviewPreviewButton) contentReviewPreviewButton.disabled = !file;
  const status = document.querySelector("#content-review-file-status");
  if (status) status.textContent = file ? "已选择" : "尚未选择";
  showContentReviewMessage("", "info");
});

contentReviewImportForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = contentReviewFile?.files?.[0];
  if (!file) {
    showContentReviewMessage("请先选择 .xlsx 或 .csv 文件。", "error");
    return;
  }
  contentReviewPreviewController?.abort();
  const requestSequence = ++contentReviewPreviewSequence;
  const accountId = currentAccountId();
  const controller = new AbortController();
  contentReviewPreviewController = controller;
  const button = contentReviewImportForm.querySelector("button[type='submit']");
  button.disabled = true;
  showContentReviewMessage("正在只读解析并校验数据表…", "info");
  const formData = new FormData();
  formData.append("file", file);
  formData.append("account_id", accountId);
  try {
    const data = await contentReviewApi("/api/content-review/imports/preview", {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });
    if (
      requestSequence !== contentReviewPreviewSequence
      || accountId !== currentAccountId()
      || file !== contentReviewFile?.files?.[0]
    ) return;
    previewBatchId = data.batch_id || "";
    document.querySelector("#content-review-file-status").textContent = data.already_imported ? "已导入" : "预览通过";
    if (contentReviewPreview) contentReviewPreview.hidden = data.already_imported;
    if (contentReviewCommit) contentReviewCommit.disabled = data.already_imported || !previewBatchId;
    if (!data.already_imported) {
      contentReviewPreview.querySelector("[data-preview-filename]").textContent = data.filename || file.name;
      contentReviewPreview.querySelector("[data-preview-type]").textContent = data.report_type === "douyin_item_export" ? "官方作品列表" : "账号趋势表";
      contentReviewPreview.querySelector("[data-preview-period]").textContent = `${data.period_start} 至 ${data.period_end}`;
      contentReviewPreview.querySelector("[data-preview-rows]").textContent = `${data.row_count} 行`;
      contentReviewPreview.querySelector("[data-preview-attribution]").textContent = data.report_type === "douyin_item_export" ? "仅唯一证据自动关联" : "未归因账号历史基线";
    }
    showContentReviewMessage(data.message, data.already_imported ? "info" : "success");
  } catch (error) {
    if (error.name === "AbortError" || requestSequence !== contentReviewPreviewSequence) return;
    previewBatchId = "";
    if (contentReviewPreview) contentReviewPreview.hidden = true;
    if (contentReviewCommit) contentReviewCommit.disabled = true;
    showContentReviewMessage(`预览失败：${error.message}`, "error");
  } finally {
    if (requestSequence === contentReviewPreviewSequence) {
      if (contentReviewPreviewController === controller) contentReviewPreviewController = null;
      button.disabled = !contentReviewFile?.files?.[0];
    }
  }
});

contentReviewCommit?.addEventListener("click", async () => {
  if (!previewBatchId) return;
  const batchId = previewBatchId;
  const requestSequence = contentReviewPreviewSequence;
  contentReviewCommit.disabled = true;
  try {
    const data = await contentReviewApi(`/api/content-review/imports/${encodeURIComponent(batchId)}/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    if (requestSequence !== contentReviewPreviewSequence || batchId !== previewBatchId) return;
    showContentReviewMessage(data.message, "success");
    contentReviewPreview.hidden = true;
    previewBatchId = "";
    await loadContentReviewData();
  } catch (error) {
    if (requestSequence !== contentReviewPreviewSequence || batchId !== previewBatchId) return;
    showContentReviewMessage(`导入失败：${error.message}`, "error");
  } finally {
    contentReviewCommit.disabled = !previewBatchId;
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
  invalidateContentReviewPreview();
  const file = contentReviewFile?.files?.[0];
  const status = document.querySelector("#content-review-file-status");
  if (status) status.textContent = file ? "已选择" : "尚未选择";
  showContentReviewMessage("", "info");
  loadContentReviewData();
});

initializeDisclosures();
loadContentReviewData();
