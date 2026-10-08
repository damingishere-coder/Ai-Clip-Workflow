async function handleProcessAction(button) {
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
      return;
    }
    const result = document.querySelector("#process-result");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "处理中...";
    if (result) result.textContent = "正在执行，请稍等...";

    try {
      let endpoint = button.dataset.endpoint;
      let data;
      try {
        data = await apiFetch(endpoint, { method: "POST" });
      } catch (error) {
        const needsAIConfirmation = error.status === 409
          && error.details?.code === "ai_retry_confirmation_required";
        if (!needsAIConfirmation) throw error;
        const confirmed = window.confirm(
          `${error.details.message}\n\n这一步可能产生新的 AI 调用费用，是否确认继续？`,
        );
        if (!confirmed) {
          if (result) result.textContent = "已取消 AI 重试，原失败 Job 和证据保持不变。";
          return;
        }
        const separator = endpoint.includes("?") ? "&" : "?";
        endpoint = `${endpoint}${separator}confirm_uncertain_ai=true`;
        if (result) result.textContent = "已确认，正在安全创建或恢复 AI 分析 Job...";
        data = await apiFetch(endpoint, { method: "POST" });
      }
      if (result) result.textContent = data.message || "处理完成，正在刷新页面...";
      if (button.dataset.endpoint.includes("/process/transcript")) {
        startTranscriptPolling(true);
        startTaskLiveStatusPolling(true);
        if (data.status === "completed") {
          window.setTimeout(() => window.location.reload(), 600);
        }
        return;
      }
      if (button.dataset.endpoint.includes("/process/auto-")) {
        if (result) result.textContent = data.message || "全自动流程已启动，状态会在当前页面自动更新。";
        startTaskLiveStatusPolling(true);
        return;
      }
      window.location.reload();
    } catch (error) {
      if (result) result.textContent = `处理失败：${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
}

function bindProcessActionButton(button) {
  if (!button || button.dataset.processActionBound === "true") return;
  button.dataset.processActionBound = "true";
  button.addEventListener("click", () => handleProcessAction(button));
}

document.querySelectorAll(".js-process-action").forEach((button) => {
  bindProcessActionButton(button);
});

const transcriptPanel = document.querySelector("#transcript-panel");
const transcriptProgress = document.querySelector("#transcript-progress");
const transcriptProgressMessage = document.querySelector("#transcript-progress-message");
const transcriptProgressPercent = document.querySelector("#transcript-progress-percent");
const transcriptProgressBar = document.querySelector("#transcript-progress-bar");
const transcriptProgressDetail = document.querySelector("#transcript-progress-detail");
const transcriptProgressRuntime = document.querySelector("#transcript-progress-runtime");
const transcriptPreviewBox = document.querySelector("#transcript-preview-box");
const cancelTranscriptButtons = document.querySelectorAll(".js-cancel-transcript");
const localTranscriptButton = document.querySelector("#local-transcript-button");
let transcriptPollingTimer = null;
let transcriptPollingStartedFromRunning = false;

function renderTranscriptPreview(preview, transcriptExists) {
  if (!transcriptPreviewBox) return;
  transcriptPreviewBox.replaceChildren();
  if (preview.length) {
    preview.forEach((line) => {
      const paragraph = document.createElement("p");
      const time = document.createElement("time");
      time.textContent = line.time;
      paragraph.append(time, document.createTextNode(line.text));
      transcriptPreviewBox.append(paragraph);
    });
    return;
  }

  const empty = document.createElement("p");
  empty.className = "empty-note";
  empty.textContent = transcriptExists
    ? "已生成转写文件，但当前文件里还没有真实语音转写内容。请查看上方进度或日志。"
    : "尚未生成转写文件。请先点击“提取音频”，再点击“生成转写 MD”。";
  transcriptPreviewBox.append(empty);
}

function renderTranscriptStatus(data) {
  if (!transcriptProgress || !data.progress || !Object.keys(data.progress).length) return;
  const progress = data.progress;
  const percent = Number(progress.percent || 0);
  transcriptProgress.hidden = false;
  transcriptProgress.dataset.status = progress.status || "running";
  transcriptProgressMessage.textContent = progress.message || "转写进度";
  transcriptProgressPercent.textContent = `${percent}%`;
  transcriptProgressBar.style.width = `${percent}%`;

  if (progress.total_chunks) {
    transcriptProgressDetail.textContent = `转写进度：${progress.current_chunk || 0}/${progress.total_chunks}，约 ${percent}%`;
  } else {
    transcriptProgressDetail.textContent = "转写进度：正在准备分段";
  }

  const runtimeParts = [progress.provider_label, progress.model, progress.device, progress.compute_type].filter(Boolean);
  transcriptProgressRuntime.textContent = runtimeParts.length
    ? `当前转写配置：${runtimeParts.join(" / ")}`
    : "";

  updateCancelTranscriptButtons(progress.status || data.task_status);
  renderTranscriptPreview(data.preview || [], data.transcript_exists);
  updateWorkflowButtons(data);
}

let transcriptRequestInFlight = false;
async function pollTranscriptStatus() {
  if (!transcriptPanel || document.hidden || transcriptRequestInFlight) return;
  if (transcriptPollingTimer) window.clearTimeout(transcriptPollingTimer);
  transcriptPollingTimer = null;
  transcriptRequestInFlight = true;
  let retry = false;
  try {
    const data = await apiFetch(`/api/tasks/${transcriptPanel.dataset.taskId}/transcript-status`);
    renderTranscriptStatus(data);
    retry = ["running", "cancelling"].includes(data.progress?.status);
    transcriptPollingStartedFromRunning = retry;
  } catch (error) {
    retry = true;
    if (transcriptProgressMessage) transcriptProgressMessage.textContent = `状态读取中断，将自动重试：${error.message}`;
  } finally {
    transcriptRequestInFlight = false;
    if (retry && !document.hidden) transcriptPollingTimer = window.setTimeout(pollTranscriptStatus, 5000);
  }
}

function startTranscriptPolling(runImmediately = false) {
  if (!transcriptPanel) return;
  if (transcriptPollingTimer) {
    window.clearTimeout(transcriptPollingTimer);
    transcriptPollingTimer = null;
  }
  transcriptPollingStartedFromRunning = true;
  if (runImmediately) {
    pollTranscriptStatus().catch(() => {});
    return;
  }
  transcriptPollingTimer = window.setTimeout(pollTranscriptStatus, 5000);
}

if (transcriptPanel) {
  pollTranscriptStatus().catch(() => {});
}

function updateWorkflowButtons(data) {
  const startButton = document.querySelector("#start-workflow-button");
  if (!startButton) return;
  const progressStatus = data.progress?.status || "";
  const canRetryWithLocal = Boolean(data.local_retry_available);
  const retryLabel = data.offline_only ? "重新本地转写" : "重新远程转写";
  if (localTranscriptButton) {
    localTranscriptButton.hidden = !canRetryWithLocal;
  }
  if (data.transcript_exists) {
    startButton.textContent = "转写已完成";
    startButton.disabled = true;
    if (localTranscriptButton) localTranscriptButton.hidden = true;
    return;
  }
  if (progressStatus === "running" || data.task_status === "transcribing") {
    startButton.textContent = "转写处理中";
    startButton.disabled = true;
    if (localTranscriptButton) localTranscriptButton.hidden = true;
    updateCancelTranscriptButtons(progressStatus || "running");
    return;
  }
  if (progressStatus === "failed") {
    startButton.textContent = retryLabel;
    startButton.disabled = false;
    startButton.classList.add("js-process-action");
    startButton.dataset.endpoint = `/api/tasks/${transcriptPanel?.dataset.taskId}/process/transcript-workflow?force=true`;
    bindProcessActionButton(startButton);
    updateCancelTranscriptButtons("failed");
    return;
  }
  if (progressStatus === "cancelled" || progressStatus === "stale") {
    startButton.textContent = progressStatus === "stale" ? retryLabel : "重新生成转写";
    startButton.disabled = false;
    startButton.classList.add("js-process-action");
    startButton.dataset.endpoint = `/api/tasks/${transcriptPanel?.dataset.taskId}/process/transcript-workflow?force=true`;
    bindProcessActionButton(startButton);
    updateCancelTranscriptButtons(progressStatus);
  }
}

function updateCancelTranscriptButtons(status) {
  const shouldShow = status === "running" || status === "cancelling" || status === "transcribing";
  cancelTranscriptButtons.forEach((button) => {
    button.hidden = !shouldShow;
    button.disabled = status === "cancelling";
    button.textContent = status === "cancelling" ? "正在停止..." : "停止转写";
  });
}

cancelTranscriptButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("停止后不会删除已生成的旧转写文件。确认停止当前转写吗？")) {
      return;
    }
    const result = document.querySelector("#process-result");
    const taskId = button.dataset.taskId || transcriptPanel?.dataset.taskId;
    cancelTranscriptButtons.forEach((item) => {
      item.disabled = true;
      item.textContent = "正在停止...";
    });
    if (result) result.textContent = "正在请求停止转写...";

    try {
      const response = await fetch(`/api/tasks/${taskId}/process/transcript-cancel`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "停止转写失败");
      }
      if (result) result.textContent = data.message || "已请求停止转写。";
      startTranscriptPolling(true);
    } catch (error) {
      if (result) result.textContent = `停止转写失败：${error.message}`;
      updateCancelTranscriptButtons("running");
    }
  });
});

const aiAnalysisForm = document.querySelector("#ai-analysis-form");
const aiAnalysisProvider = document.querySelector("#ai-analysis-provider");
const saveAiPromptsButton = document.querySelector("#save-ai-prompts-button");
const aiProcessResult = document.querySelector("#ai-process-result");
const aiAnalysisSummary = document.querySelector("#ai-analysis-summary");
const aiCandidateCountPill = document.querySelector("#ai-candidate-count-pill");
const aiCandidateCountInput = document.querySelector("#ai-candidate-count-input");
const aiSelectionProfile = aiAnalysisForm
  ? aiAnalysisForm.dataset.selectionProfile || "general"
  : "general";
const aiFinalClipTarget = document.querySelector("#ai-final-clip-target");
const aiHighlightDensity = document.querySelector("#ai-highlight-density");
const aiHighlightTotalLimit = document.querySelector("#ai-highlight-total-limit");
const showAiHistoryButton = document.querySelector("#show-ai-history-button");
const refreshAiHistoryButton = document.querySelector("#refresh-ai-history-button");
const aiAnalysisHistory = document.querySelector("#ai-analysis-history");
const aiAnalysisHistoryList = document.querySelector("#ai-analysis-history-list");
const aiAnalysisProgress = document.querySelector("#ai-analysis-progress");
const aiAnalysisProgressMessage = document.querySelector("#ai-analysis-progress-message");
const aiAnalysisProgressPercent = document.querySelector("#ai-analysis-progress-percent");
const aiAnalysisProgressBar = document.querySelector("#ai-analysis-progress-bar");
const runtimeLogState = document.querySelector("#runtime-log-state");
const runtimeLogLines = document.querySelector("#runtime-log-lines");
const aiProcessButtons = Array.from(document.querySelectorAll(".js-ai-process-action"));
const aiAnalysisControls = Array.from(new Set([
  ...(aiAnalysisForm ? aiAnalysisForm.querySelectorAll("button, input, textarea, select") : []),
  ...aiProcessButtons,
  saveAiPromptsButton,
  aiCandidateCountInput,
  aiFinalClipTarget,
].filter(Boolean)));
const autoPipelineMonitor = document.querySelector("[data-auto-pipeline-monitor]");
const taskLiveOverview = document.querySelector("[data-task-live-overview]");
const taskLiveProgressBar = document.querySelector("[data-task-live-progress-bar]");
const taskLiveProgressNumber = document.querySelector("[data-task-live-progress-number]");
const taskLiveNote = document.querySelector("[data-task-live-note]");
const taskLiveUpdatedAt = document.querySelector("[data-task-live-updated-at]");
const taskLiveCandidateCount = document.querySelector("[data-task-live-candidate-count]");
const taskLiveOutputCount = document.querySelector("[data-task-live-output-count]");
const taskLiveActions = document.querySelector("[data-live-task-actions]");
const taskLiveOperation = document.querySelector("[data-task-live-operation]");
const taskLiveOperationLabel = document.querySelector("[data-task-live-operation-label]");
const taskLiveOperationProgress = document.querySelector("[data-task-live-operation-progress]");
const taskLiveOperationMessage = document.querySelector("[data-task-live-operation-message]");
let aiStatusPollingTimer = null;
let isAiAnalysisBusy = false;
let aiAnalysisControlStates = new Map();
let taskLiveStatusTimer = null;
let taskLiveForcedPollingUntil = 0;
let taskLiveStatusRequestInFlight = false;
const TASK_LIVE_STATUS_INTERVAL_MS = 3000;

function setAiAnalysisControlsDisabled(disabled) {
  if (disabled) {
    aiAnalysisControlStates = new Map(
      aiAnalysisControls.map((control) => [control, control.disabled]),
    );
    aiAnalysisControls.forEach((control) => { control.disabled = true; });
    return;
  }
  aiAnalysisControls.forEach((control) => {
    control.disabled = aiAnalysisControlStates.get(control) ?? false;
  });
  aiAnalysisControlStates.clear();
}

function summarizeErrorMessage(message, maxLength = 220) {
  const text = String(message || "").replace(/\s+/g, " ").trim();
  if (!text) return "操作失败，请查看任务日志。";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...（详细原因请查看任务日志）`;
}
let aiAnalysisRuns = [];

function readJsonScript(id, fallback) {
  const node = document.querySelector(`#${id}`);
  if (!node?.textContent?.trim()) return fallback;
  try {
    return JSON.parse(node.textContent);
  } catch {
    return fallback;
  }
}

function getSelectedPromptPresetCard() {
  if (!aiAnalysisForm) return null;
  const selected = aiAnalysisForm.querySelector("input[name='ai_prompt_preset_id']:checked");
  if (!selected) return null;
  return aiAnalysisForm.querySelector(`[data-prompt-preset-card][data-preset-id='${selected.value}']`);
}

function updatePromptPresetCards() {
  const selected = aiAnalysisForm?.querySelector("input[name='ai_prompt_preset_id']:checked");
  const selectedPresetId = selected?.value || "";
  document.querySelectorAll("[data-prompt-preset-tab]").forEach((tab) => {
    const radio = tab.querySelector("input[name='ai_prompt_preset_id']");
    const isActive = radio?.value === selectedPresetId;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  document.querySelectorAll("[data-prompt-preset-card]").forEach((card) => {
    const isActive = card.dataset.presetId === selectedPresetId;
    card.classList.toggle("active", isActive);
    card.hidden = !isActive;
  });
}

function formatClipDuration(seconds) {
  const totalSeconds = Number(seconds) || 0;
  const minutes = Math.floor(totalSeconds / 60);
  const restSeconds = totalSeconds % 60;
  if (minutes <= 0) return `${restSeconds} 秒`;
  return `${minutes} 分 ${String(restSeconds).padStart(2, "0")} 秒`;
}

function analysisPromptSource(data) {
  const slot = /^preset_0*(\d+)$/.exec(data.ai_prompt_preset_id || "")?.[1];
  const name = data.ai_prompt_preset_name || "来源未记录";
  const revision = data.prompt_version_number ? `第 ${data.prompt_version_number} 次` : "未记录";
  return `提示词方案：${slot ? `${slot} 号 · ` : ""}${name} · 内容修订：${revision}`;
}

function renderAiAnalysisSummary(data) {
  if (!aiAnalysisSummary) return;
  const clips = Array.isArray(data.clips) && data.clips.length ? data.clips : (data.clip_summaries || []);
  aiAnalysisSummary.hidden = false;
  aiAnalysisSummary.replaceChildren();

  const header = document.createElement("div");
  header.className = "ai-analysis-result-header";
  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Analysis Result";
  const title = document.createElement("h3");
  title.textContent = data.run_number ? `${data.title || `第 ${data.run_number} 次分析`} · AI 选取结果` : "本次 AI 选取结果";
  const promptSource = document.createElement("p");
  promptSource.className = "form-hint";
  promptSource.textContent = analysisPromptSource(data);
  titleWrap.append(eyebrow, title, promptSource);
  const source = document.createElement("span");
  source.className = "status-pill";
  source.textContent = data.provider_label && data.model ? `${data.provider_label} · 模型 ${data.model}` : "AI 分析完成";
  header.append(titleWrap, source);

  const message = document.createElement("p");
  message.className = "ai-analysis-result-message";
  message.textContent = data.failure_message || data.fallback_notice || data.analysis_summary || data.message || "AI 分析已完成。";

  const meta = document.createElement("div");
  meta.className = "ai-analysis-result-meta";
  const count = document.createElement("strong");
  count.textContent = `${clips.length} 条候选片段`;
  const provider = document.createElement("span");
  provider.textContent = data.fallback_notice ? "远程失败后已自动改用本地 AI" : `目标 ${data.requested_clip_count || clips.length || 0} 条`;
  meta.append(count, provider);

  const list = document.createElement("div");
  list.className = "ai-analysis-result-list";
  clips.forEach((clip, index) => {
    const item = document.createElement("article");
    item.className = "ai-analysis-result-item";
    const itemTitle = document.createElement("strong");
    itemTitle.textContent = `${String(index + 1).padStart(2, "0")} · ${clip.title || "未命名片段"}`;
    const itemMeta = document.createElement("span");
    itemMeta.textContent = `视频长度 ${formatClipDuration(clip.duration_seconds)} · ${clip.start_time || "--"} - ${clip.end_time || "--"}`;
    item.append(itemTitle, itemMeta);
    list.append(item);
  });

  const actions = document.createElement("div");
  actions.className = "button-row align-end";
  const reviewLink = document.createElement("a");
  reviewLink.className = "primary-button";
  reviewLink.href = data.review_url || `/tasks/${aiAnalysisForm?.dataset.taskId || ""}/clips/review`;
  reviewLink.textContent = data.analysis_incomplete ? "查看已保留的候选（待补齐分析）" : "去检查并生成切片";
  actions.append(reviewLink);

  aiAnalysisSummary.append(header, message, meta, list, actions);
}

function renderAiAnalysisHistory(runs) {
  if (!aiAnalysisHistoryList) return;
  aiAnalysisRuns = Array.isArray(runs) ? runs : [];
  aiAnalysisHistoryList.replaceChildren();

  if (!aiAnalysisRuns.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note";
    empty.textContent = "还没有历史分析结果。完成一次 AI 分析后，这里会自动出现记录。";
    aiAnalysisHistoryList.append(empty);
    return;
  }

  aiAnalysisRuns.forEach((run) => {
    const item = document.createElement("article");
    item.className = "ai-history-item";
    const main = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = `${run.title || `第 ${run.run_number} 次分析`} · ${run.clip_count || 0} 条`;
    const meta = document.createElement("span");
    meta.textContent = `${run.provider_label || "AI"} · ${run.model || "未知模型"} · ${analysisPromptSource(run)} · ${run.created_at || "未知时间"}`;
    const summary = document.createElement("p");
    summary.textContent = run.failure_message || run.fallback_notice || run.analysis_summary || "暂无整体总结。";
    main.append(title, meta, summary);
    if (run.challenger?.id) {
      const trialLink = document.createElement("a");
      trialLink.href = `/api/content-review/challengers/${encodeURIComponent(run.challenger.id)}`;
      trialLink.textContent = "Challenger 试验 · 查看来源版本";
      main.append(trialLink);
    }
    const visual = run.visual_signal || run.analysis_meta?.visual_signal;
    if (visual) {
      const link = document.createElement("a");
      link.href = `/tasks/${encodeURIComponent(aiAnalysisForm.dataset.taskId)}/visual-evidence?run_id=${encodeURIComponent(run.id)}`;
      const labels = { disabled: "关闭", completed: "完成", partial: "部分可用", unavailable: "不可用" };
      link.textContent = `视觉 ${labels[visual.status] || visual.status} · ${visual.verified_count || 0}/${visual.candidate_count || 0} 候选 · 查看证据`;
      main.append(link);
    }

    const restoreButton = document.createElement("button");
    restoreButton.className = "secondary-button compact-button";
    restoreButton.type = "button";
    restoreButton.dataset.restoreRunId = run.id;
    restoreButton.textContent = "恢复这次结果";
    item.append(main, restoreButton);
    aiAnalysisHistoryList.append(item);
  });
}

async function refreshAiAnalysisHistory() {
  if (!aiAnalysisForm) return;
  const taskId = aiAnalysisForm.dataset.taskId;
  const response = await fetch(`/api/tasks/${taskId}/ai-analysis-runs`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "读取历史分析失败");
  }
  renderAiAnalysisHistory(data.runs || []);
  if (data.latest) {
    renderAiAnalysisSummary(data.latest);
  }
}

async function saveTaskCandidateClipCount() {
  if (!aiAnalysisForm || !aiCandidateCountInput) return null;
  const taskId = aiAnalysisForm.dataset.taskId;
  const count = Number(aiCandidateCountInput.value || 12);
  if (!Number.isInteger(count) || count < 1 || count > 50) {
    throw new Error("候选片段数量必须是 1 到 50 之间的整数。");
  }
  const response = await fetch(`/api/tasks/${taskId}/candidate-clip-count`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_clip_count: count }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "候选片段数量保存失败");
  }
  return data;
}

async function saveTaskAiPromptSettings() {
  if (!aiAnalysisForm) return null;
  if (aiAnalysisForm.dataset.challengerId) return {message: "Challenger 的 Profile 与 Prompt 已冻结；已保存其他分析设置。"};
  const taskId = aiAnalysisForm.dataset.taskId;
  const selected = aiAnalysisForm.querySelector("input[name='ai_prompt_preset_id']:checked");
  if (!selected) {
    throw new Error("请选择一个 AI Prompt 方案");
  }

  const response = await fetch(`/api/tasks/${taskId}/ai-prompt-preset`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ai_prompt_preset_id: selected.value,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "AI Prompt 方案选择保存失败");
  }
  return data;
}

if (aiAnalysisForm) {
  aiAnalysisForm.querySelectorAll("input[name='ai_prompt_preset_id']").forEach((radio) => {
    radio.addEventListener("change", updatePromptPresetCards);
  });
  aiAnalysisForm.querySelectorAll("[data-prompt-preset-card] input[type='text']").forEach((input) => {
    input.addEventListener("input", () => {
      if (aiProcessResult) aiProcessResult.textContent = "Prompt 方案内容已修改，分析前会自动保存。";
    });
  });
  updatePromptPresetCards();
}

function taskSettingsSignature() {
  return aiAnalysisForm ? JSON.stringify(Array.from(aiAnalysisForm.elements).filter(node => node.name).map(node => [node.name, node.type === "checkbox" || node.type === "radio" ? node.checked : node.value])) : "";
}
let savedTaskSettings = taskSettingsSignature();
function markTaskSettingsSaved() { savedTaskSettings = taskSettingsSignature(); }
aiAnalysisForm?.addEventListener("input", () => { if (aiProcessResult) aiProcessResult.textContent = "有未保存的分析设置"; });
window.addEventListener("beforeunload", event => {
  if (taskSettingsSignature() === savedTaskSettings) return;
  event.preventDefault(); event.returnValue = "";
});

if (saveAiPromptsButton && aiAnalysisForm) {
  saveAiPromptsButton.addEventListener("click", async () => {
    if (isAiAnalysisBusy) return;
    const originalText = saveAiPromptsButton.textContent;
    saveAiPromptsButton.disabled = true;
    saveAiPromptsButton.textContent = "保存中...";
    if (aiProcessResult) aiProcessResult.textContent = "正在保存 AI Prompt 方案...";

    try {
      const data = await saveTaskAiPromptSettings();
      await saveTaskCandidateClipCount();
      await saveTaskSelectionSettings();
      markTaskSettingsSaved();
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI Prompt 方案已保存。";
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `保存失败：${error.message}`;
    } finally {
      saveAiPromptsButton.disabled = isAiAnalysisBusy;
      saveAiPromptsButton.textContent = originalText;
    }
  });
}

aiProcessButtons.forEach((button) => {
  button.addEventListener("click", async () => {
    if (!aiAnalysisForm || isAiAnalysisBusy) return;
    const originalText = button.textContent;
    const taskId = aiAnalysisForm.dataset.taskId;
    const provider = aiAnalysisProvider?.value || "";
    const selectedCard = getSelectedPromptPresetCard();
    const selectedPrompt = selectedCard?.querySelector("textarea")?.value.trim() || "";
    const selectedName = selectedCard?.querySelector("input[type='text']")?.value.trim() || "当前方案";
    if (!selectedPrompt) {
      if (aiProcessResult) aiProcessResult.textContent = "请先填写当前选中的 AI Prompt 方案。";
      return;
    }
    if (!provider) {
      const confirmed = window.confirm(`确认使用“${selectedName}”并沿用任务设置开始分析吗？\n\n历史任务未记录分析方式时使用当前默认设置；失败任务恢复仍沿用原记录。将消耗对应模型额度，并覆盖现有 AI 候选结果。`);
      if (!confirmed) return;
    } else if (provider === "codex") {
      const confirmed = window.confirm(`确认使用“${selectedName}”发起 Codex CLI 分析吗？\n\n这会消耗当前 Codex 套餐额度，并覆盖现有 AI 候选结果。`);
      if (!confirmed) return;
    } else if (provider === "remote") {
      const confirmed = window.confirm(`确认使用“${selectedName}”发起远程 AI 分析吗？\n\n这会重新生成候选片段，并覆盖当前已有的 AI 候选结果。`);
      if (!confirmed) return;
    }
    isAiAnalysisBusy = true;
    setAiAnalysisControlsDisabled(true);
    button.textContent = "分析中...";
    if (aiProcessResult) aiProcessResult.textContent = "正在保存 Prompt 方案并启动 AI 分析...";
    renderAiAnalysisProgress({
      status: "running",
      percent: 18,
      message: "正在保存 Prompt 方案并启动 AI 分析...",
    });
    startTaskLiveStatusPolling(true);

    try {
      await saveTaskAiPromptSettings();
      await saveTaskCandidateClipCount();
      await saveTaskSelectionSettings();
      markTaskSettingsSaved();
      pollAiAnalysisStatus(true).catch(() => {});
      const response = await fetch(`/api/tasks/${taskId}/process/ai${provider ? `?provider=${provider}` : ""}`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "AI 分析失败");
      }
      if (!data.job_id) throw new Error("AI 分析队列没有返回 job_id");
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI 分析已加入队列。";
      const completedJob = await waitForAiAnalysisJob(data.job_id);
      const result = completedJob.result_json || {};
      if (aiProcessResult) aiProcessResult.textContent = result.message || completedJob.message || "AI 分析完成。";
      await pollAiAnalysisStatus(false).catch(() => {});
      if (aiCandidateCountPill && Number.isFinite(Number(result.clip_count))) {
        aiCandidateCountPill.textContent = `${Number(result.clip_count)} 条候选`;
      }
      const historyResponse = await fetch(`/api/tasks/${taskId}/ai-analysis-runs`);
      const historyData = await historyResponse.json();
      if (!historyResponse.ok) throw new Error(historyData.detail || "读取 AI 分析历史失败");
      renderAiAnalysisSummary(historyData.latest || result);
      renderAiAnalysisHistory(historyData.runs || aiAnalysisRuns);
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `AI 分析失败：${summarizeErrorMessage(error.message)}`;
      await pollAiAnalysisStatus(false).catch(() => {});
    } finally {
      stopAiAnalysisStatusPolling();
      isAiAnalysisBusy = false;
      setAiAnalysisControlsDisabled(false);
      button.textContent = originalText;
    }
  });
});

if (aiAnalysisForm) {
  const latestAiAnalysis = readJsonScript("latest-ai-analysis-data", null);
  const initialAiAnalysisRuns = readJsonScript("ai-analysis-runs-data", []);
  renderAiAnalysisHistory(initialAiAnalysisRuns);
  if (latestAiAnalysis) {
    renderAiAnalysisSummary(latestAiAnalysis);
  }
  pollAiAnalysisStatus(false).catch(() => {});
}

if (showAiHistoryButton && aiAnalysisHistory) {
  showAiHistoryButton.addEventListener("click", () => {
    aiAnalysisHistory.hidden = !aiAnalysisHistory.hidden;
  });
}

if (refreshAiHistoryButton) {
  refreshAiHistoryButton.addEventListener("click", async () => {
    const originalText = refreshAiHistoryButton.textContent;
    refreshAiHistoryButton.disabled = true;
    refreshAiHistoryButton.textContent = "刷新中...";
    try {
      await refreshAiAnalysisHistory();
      if (aiProcessResult) aiProcessResult.textContent = "历史分析结果已刷新。";
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `刷新历史失败：${error.message}`;
    } finally {
      refreshAiHistoryButton.disabled = false;
      refreshAiHistoryButton.textContent = originalText;
    }
  });
}

if (aiAnalysisHistoryList) {
  aiAnalysisHistoryList.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-restore-run-id]");
    if (!button || !aiAnalysisForm) return;
    const runId = button.dataset.restoreRunId;
    const confirmed = window.confirm("确认恢复这次 AI 分析结果吗？\n\n当前片段审核页的候选片段会被这次历史结果覆盖。");
    if (!confirmed) return;

    const taskId = aiAnalysisForm.dataset.taskId;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "恢复中...";
    try {
      const response = await fetch(`/api/tasks/${taskId}/ai-analysis-runs/${runId}/restore`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "恢复失败");
      }
      if (aiProcessResult) aiProcessResult.textContent = data.message || "历史结果已恢复。";
      if (aiCandidateCountPill && Array.isArray(data.clips)) {
        aiCandidateCountPill.textContent = `${data.clips.length} 条候选`;
      }
      renderAiAnalysisSummary(data.restored_run || data.latest);
      renderAiAnalysisHistory(data.runs || aiAnalysisRuns);
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `恢复失败：${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
}

async function saveTaskSelectionSettings() {
  if (!aiAnalysisForm || !aiSelectionProfile || !aiFinalClipTarget) return null;
  const finalTarget = Number(aiFinalClipTarget.value || 5);
  if (!Number.isInteger(finalTarget) || finalTarget < 1 || finalTarget > 12) {
    throw new Error("最终启用目标必须是 1 到 12 之间的整数。");
  }
  const settingsPayload = {
    selection_profile: aiSelectionProfile,
    final_clip_target: finalTarget,
  };
  if (aiSelectionProfile === "long_live_talk") {
    settingsPayload.highlight_density_per_hour = Number(aiHighlightDensity?.value || 4);
    settingsPayload.highlight_total_limit = Number(aiHighlightTotalLimit?.value || 30);
  }
  const response = await fetch(`/api/tasks/${aiAnalysisForm.dataset.taskId}/selection-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settingsPayload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "选片设置保存失败");
  const visual = document.querySelector("#task-visual-enabled");
  if (visual) {
    const visualResponse = await fetch(`/api/tasks/${aiAnalysisForm.dataset.taskId}/visual-settings`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visual_enabled: visual.checked }),
    });
    const visualData = await visualResponse.json();
    if (!visualResponse.ok) throw new Error(visualData.detail || "视觉设置保存失败");
  }
  return data;
}

function renderAiAnalysisProgress(status) {
  if (!aiAnalysisProgress) return;
  const percent = Math.max(0, Math.min(100, Number(status.percent || 0)));
  aiAnalysisProgress.hidden = false;
  aiAnalysisProgress.dataset.status = status.status || "idle";
  if (aiAnalysisProgressMessage) {
    aiAnalysisProgressMessage.textContent = status.message || "AI 分析进度";
  }
  if (aiAnalysisProgressPercent) {
    aiAnalysisProgressPercent.textContent = `${percent}%`;
  }
  if (aiAnalysisProgressBar) {
    aiAnalysisProgressBar.style.width = `${percent}%`;
  }
}

function renderRuntimeLog(status) {
  if (runtimeLogState) {
    const labelMap = {
      idle: "待开始",
      running: "分析中",
      completed: "已完成",
      failed: "失败",
    };
    const runtimeStatus = status.runtime_status || status.status || "idle";
    runtimeLogState.textContent = status.runtime_status_label
      || labelMap[runtimeStatus]
      || status.status_label
      || status.task_status_label
      || "已刷新";
    runtimeLogState.dataset.status = runtimeStatus;
  }
  if (!runtimeLogLines) return;
  const lines = Array.isArray(status.log_lines) ? status.log_lines : [];
  runtimeLogLines.textContent = lines.length ? lines.join("\n") : "暂无运行日志。任务开始后，这里会自动刷新。";
  runtimeLogLines.scrollTop = runtimeLogLines.scrollHeight;
}

function formatTaskLiveRefreshTime() {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function renderTaskLiveActions(data) {
  if (!taskLiveActions) return;
  const actions = data.actions || {};
  let primaryAction = actions.primary || "none";
  if (primaryAction === "publish" && !actions.publish) {
    primaryAction = "none";
  }

  taskLiveActions.querySelectorAll("[data-live-primary-action]").forEach((node) => {
    node.hidden = node.dataset.livePrimaryAction !== primaryAction;
  });

  const reviewAction = taskLiveActions.querySelector("[data-live-review-action]");
  if (reviewAction) reviewAction.hidden = !actions.review || primaryAction === "review_outputs";
  const syncAction = taskLiveActions.querySelector("[data-live-sync-action]");
  if (syncAction) {
    syncAction.hidden = ["subtitle_review", "review_outputs"].includes(primaryAction) || Number(data.counts?.outputs || 0) <= 0;
  }
  const subtitleSkip = taskLiveActions.querySelector("[data-live-subtitle-skip]");
  if (subtitleSkip) subtitleSkip.hidden = primaryAction !== "subtitle_review" || actions.subtitle_skip === false;
}

async function waitForAiAnalysisJob(jobId) {
  while (true) {
    const response = await fetch(`/api/tasks/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "查询 AI 分析任务进度失败");
    renderAiAnalysisProgress({
      status: job.status,
      percent: Number(job.progress || 0),
      message: job.status === "failed"
        ? (job.error_message || job.message || "AI 分析失败")
        : (job.message || "AI 分析正在排队..."),
    });
    if (job.status === "completed") return job;
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error_message || job.message || "AI 分析任务未完成");
    }
    await wait(1000);
  }
}

function renderTaskLiveStatus(data) {
  const next = document.querySelector("[data-ui-task-primary-action]");
  if (next && data.ui?.primary_action) {
    const target = new URL(data.ui.primary_action.url, window.location.origin);
    if (next.dataset.returnTo) target.searchParams.set("return_to", next.dataset.returnTo);
    next.href = target.pathname + target.search + target.hash;
    next.textContent = data.ui.primary_action.label;
  }
  const message = document.querySelector("[data-ui-task-message]");
  if (message && data.ui) message.textContent = data.ui.message;
  const progress = Math.max(0, Math.min(100, Number(data.ui?.production_progress ?? data.progress ?? 0)));
  document.querySelectorAll("[data-task-live-status-label]").forEach((node) => {
    node.textContent = data.ui?.stage_label || data.status_label || data.status || "状态未知";
    node.dataset.status = data.runtime_status || (data.should_poll ? "running" : "completed");
  });
  const headerStatus = document.querySelector("[data-task-live-header-status]");
  if (headerStatus) headerStatus.textContent = data.ui?.stage_label || data.status_label || data.status || "状态未知";
  if (taskLiveProgressBar) taskLiveProgressBar.style.width = `${progress}%`;
  if (taskLiveProgressNumber) taskLiveProgressNumber.textContent = `${progress}%`;
  if (taskLiveUpdatedAt) taskLiveUpdatedAt.textContent = data.updated_at || "未知";

  const candidateCount = Number(data.counts?.candidates || 0);
  const outputCount = Number(data.counts?.outputs || 0);
  if (taskLiveCandidateCount) taskLiveCandidateCount.textContent = `${candidateCount} 条`;
  if (taskLiveOutputCount) taskLiveOutputCount.textContent = `${outputCount} 条`;
  if (aiCandidateCountPill) aiCandidateCountPill.textContent = `${candidateCount} 条候选`;

  const rawOperation = data.active_operation || {};
  const operation = data.ui && (!rawOperation.kind || ["task", "idle"].includes(rawOperation.kind))
    ? {...rawOperation, progress: data.ui.production_progress, label: data.ui.stage_label, message: data.ui.message}
    : rawOperation;
  const operationProgress = Math.max(0, Math.min(100, Number(operation.progress || 0)));
  if (taskLiveOperation) {
    taskLiveOperation.dataset.status = operation.status || "idle";
    const scope = taskLiveOperation.querySelector("div > span");
    if (scope) scope.textContent = operation.kind === "publish" ? "发布状态（独立记录）" : "当前制作操作";
  }
  if (taskLiveOperationLabel) {
    taskLiveOperationLabel.textContent = operation.label || data.task_status_label || "当前任务";
  }
  if (taskLiveOperationProgress) {
    taskLiveOperationProgress.hidden = operation.kind === "publish";
    taskLiveOperationProgress.textContent = `${operationProgress}%`;
  }
  if (taskLiveOperationMessage) {
    taskLiveOperationMessage.textContent = operation.message
      || `当前阶段：${data.task_status_label || data.status_label || "状态未知"}`;
  }

  const allowedStepStates = new Set(["done", "current", "pending", "warning"]);
  (data.ui?.workflow_steps || (Array.isArray(data.workflow_steps) ? data.workflow_steps : [])).forEach((step) => {
    const node = taskLiveOverview?.querySelector(`[data-task-live-step="${step.index}"]`);
    if (!node) return;
    node.classList.remove("done", "current", "pending", "warning");
    node.classList.add(allowedStepStates.has(step.state) ? step.state : "pending");
    const number = node.querySelector("span");
    const label = node.querySelector("strong");
    if (number) number.textContent = step.index;
    if (label) label.textContent = step.name;
  });

  renderRuntimeLog(data);
  renderTaskLiveActions(data);
  if (autoPipelineMonitor) {
    autoPipelineMonitor.dataset.status = data.status || "";
    autoPipelineMonitor.dataset.running = data.should_poll ? "true" : "false";
  }
  if (taskLiveNote) {
    taskLiveNote.dataset.snapshotAt = data.snapshot_at || "";
    if (data.error_message) {
      taskLiveNote.dataset.state = "error";
      taskLiveNote.textContent = `流程已暂停：${summarizeErrorMessage(data.error_message)}`;
    } else if (data.should_poll) {
      taskLiveNote.dataset.state = "active";
      taskLiveNote.textContent = `自动刷新中 · ${formatTaskLiveRefreshTime()} 已获取最新状态`;
    } else {
      taskLiveNote.dataset.state = "completed";
      taskLiveNote.textContent = `状态已更新 · ${formatTaskLiveRefreshTime()}`;
    }
  }
}

function scheduleTaskLiveStatusPolling() {
  if (!autoPipelineMonitor || document.hidden) return;
  if (taskLiveStatusTimer) window.clearTimeout(taskLiveStatusTimer);
  taskLiveStatusTimer = window.setTimeout(() => {
    pollTaskLiveStatus().catch(() => {});
  }, TASK_LIVE_STATUS_INTERVAL_MS);
}

async function pollTaskLiveStatus() {
  if (!autoPipelineMonitor) return null;
  if (taskLiveStatusRequestInFlight) return null;
  const taskId = autoPipelineMonitor.dataset.taskId;
  if (!taskId) return null;
  if (taskLiveStatusTimer) {
    window.clearTimeout(taskLiveStatusTimer);
    taskLiveStatusTimer = null;
  }

  taskLiveStatusRequestInFlight = true;
  try {
    const data = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/live-status`);
    renderTaskLiveStatus(data);
    if (data.should_poll) taskLiveForcedPollingUntil = 0;
    if (data.should_poll || Date.now() < taskLiveForcedPollingUntil) {
      scheduleTaskLiveStatusPolling();
    }
    return data;
  } catch (error) {
    if (taskLiveNote) {
      taskLiveNote.dataset.state = "error";
      taskLiveNote.textContent = `自动更新暂时中断，正在重试：${summarizeErrorMessage(error.message)}`;
    }
    scheduleTaskLiveStatusPolling();
    return null;
  } finally {
    taskLiveStatusRequestInFlight = false;
  }
}

function startTaskLiveStatusPolling(forceRestart = false) {
  if (!autoPipelineMonitor) return;
  if (forceRestart) taskLiveForcedPollingUntil = Date.now() + 10000;
  pollTaskLiveStatus().catch(() => {});
}

if (autoPipelineMonitor) {
  startTaskLiveStatusPolling();
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (taskLiveStatusTimer) window.clearTimeout(taskLiveStatusTimer);
      taskLiveStatusTimer = null;
      return;
    }
    startTaskLiveStatusPolling();
  });
}

let aiStatusRequestInFlight = false;
async function pollAiAnalysisStatus(keepPolling = false) {
  if (!aiAnalysisForm || document.hidden || aiStatusRequestInFlight) return null;
  if (aiStatusPollingTimer) window.clearTimeout(aiStatusPollingTimer);
  aiStatusPollingTimer = null;
  aiStatusRequestInFlight = true;
  let retry = keepPolling;
  try {
    const data = await apiFetch(`/api/tasks/${aiAnalysisForm.dataset.taskId}/ai-analysis-status`);
    renderAiAnalysisProgress(data);
    retry = retry || data.is_running;
    return data;
  } catch (error) {
    retry = true;
    if (aiProcessResult) aiProcessResult.textContent = `状态读取中断，将自动重试：${error.message}`;
    return null;
  } finally {
    aiStatusRequestInFlight = false;
    if (retry && !document.hidden) aiStatusPollingTimer = window.setTimeout(() => pollAiAnalysisStatus(), 3000);
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (transcriptPollingTimer) window.clearTimeout(transcriptPollingTimer);
    if (aiStatusPollingTimer) window.clearTimeout(aiStatusPollingTimer);
  } else {
    pollTranscriptStatus();
    pollAiAnalysisStatus();
  }
});

function stopAiAnalysisStatusPolling() {
  if (!aiStatusPollingTimer) return;
  window.clearTimeout(aiStatusPollingTimer);
  aiStatusPollingTimer = null;
}


document.querySelectorAll("[data-live-subtitle-skip]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!window.confirm("确认跳过字幕并进入片段审核吗？审核保存后才会同步发送中心。")) return;
    const taskId = button.dataset.taskId;
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "正在进入审核...";
    try {
      const data = await apiFetch(`/api/subtitles/tasks/${encodeURIComponent(taskId)}/skip-to-review`, {
        method: "POST",
      });
      if (taskLiveNote) taskLiveNote.textContent = data.message || "已跳过字幕，正在进入片段审核";
      window.location.href = data.review_url || `/tasks/${encodeURIComponent(taskId)}/clips/review`;
    } catch (error) {
      window.alert(`跳过字幕失败：${summarizeErrorMessage(error.message)}`);
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});
