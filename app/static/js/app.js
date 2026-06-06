const newTaskForm = document.querySelector("#new-task-form");

if (newTaskForm) {
  newTaskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const result = document.querySelector("#new-task-result");
    const submitButton = newTaskForm.querySelector("button[type='submit']");
    const formData = new FormData(newTaskForm);
    const payload = Object.fromEntries(formData.entries());
    const videoFileInput = document.querySelector("#video-file-input");

    submitButton.disabled = true;
    result.textContent = "正在创建任务...";

    try {
      if (!videoFileInput.files.length) {
        throw new Error("请选择要上传的视频文件");
      }
      const uploadData = new FormData();
      uploadData.append("task_name", payload.task_name || "");
      uploadData.append("platform", payload.platform || "general");
      uploadData.append("max_clip_duration", payload.max_clip_duration || "5");
      uploadData.append("candidate_clip_count", payload.candidate_clip_count || "5");
      uploadData.append("ai_preference", "");
      uploadData.append("video_file", videoFileInput.files[0]);
      const response = await fetch("/api/tasks/upload", {
        method: "POST",
        body: uploadData,
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "任务创建失败");
      }
      result.textContent = `${data.message} 正在进入详情页...`;
      window.location.href = data.detail_url;
    } catch (error) {
      result.textContent = `任务创建失败：${error.message}`;
    } finally {
      submitButton.disabled = false;
    }
  });
}

const videoFileInput = document.querySelector("#video-file-input");
const videoFileName = document.querySelector("#video-file-name");

if (videoFileInput && videoFileName) {
  videoFileInput.addEventListener("change", () => {
    const file = videoFileInput.files[0];
    videoFileName.textContent = file ? `已选择：${file.name}` : "尚未选择视频文件。";
  });
}

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
      const response = await fetch(button.dataset.endpoint, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "处理失败");
      }
      if (result) result.textContent = data.message || "处理完成，正在刷新页面...";
      if (button.dataset.endpoint.includes("/process/transcript")) {
        startTranscriptPolling(true);
        if (data.status === "completed") {
          window.setTimeout(() => window.location.reload(), 600);
        }
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

async function pollTranscriptStatus() {
  if (!transcriptPanel) return;
  const taskId = transcriptPanel.dataset.taskId;
  const response = await fetch(`/api/tasks/${taskId}/transcript-status`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "读取转写进度失败");
  }
  renderTranscriptStatus(data);
  const status = data.progress?.status;
  if (status === "running" || status === "cancelling") {
    transcriptPollingStartedFromRunning = true;
    transcriptPollingTimer = window.setTimeout(pollTranscriptStatus, 5000);
  } else if (status === "completed") {
    transcriptPollingTimer = null;
    transcriptPollingStartedFromRunning = false;
  } else {
    transcriptPollingTimer = null;
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
    startButton.textContent = "重新远程转写";
    startButton.disabled = false;
    startButton.classList.add("js-process-action");
    startButton.dataset.endpoint = `/api/tasks/${transcriptPanel?.dataset.taskId}/process/transcript-workflow?force=true`;
    bindProcessActionButton(startButton);
    updateCancelTranscriptButtons("failed");
    return;
  }
  if (progressStatus === "cancelled" || progressStatus === "stale") {
    startButton.textContent = progressStatus === "stale" ? "重新远程转写" : "重新生成转写";
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
const saveAiPromptsButton = document.querySelector("#save-ai-prompts-button");
const aiProcessResult = document.querySelector("#ai-process-result");
const aiAnalysisSummary = document.querySelector("#ai-analysis-summary");
const aiCandidateCountPill = document.querySelector("#ai-candidate-count-pill");
const aiCandidateCountInput = document.querySelector("#ai-candidate-count-input");
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
let aiStatusPollingTimer = null;

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
  titleWrap.append(eyebrow, title);
  const source = document.createElement("span");
  source.className = "status-pill";
  source.textContent = data.provider_label && data.model ? `${data.provider_label} · 模型 ${data.model}` : "AI 分析完成";
  header.append(titleWrap, source);

  const message = document.createElement("p");
  message.className = "ai-analysis-result-message";
  message.textContent = data.fallback_notice || data.analysis_summary || data.message || "AI 分析已完成。";

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
  reviewLink.textContent = "去检查并生成切片";
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
    empty.textContent = "还没有历史分析结果。完成一次 DeepSeek AI 分析或本地 AI 分析后，这里会自动出现记录。";
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
    meta.textContent = `${run.provider_label || "AI"} · ${run.model || "未知模型"} · ${run.ai_prompt_preset_name || "Prompt 方案"} · ${run.created_at || "未知时间"}`;
    const summary = document.createElement("p");
    summary.textContent = run.fallback_notice || run.analysis_summary || "暂无整体总结。";
    main.append(title, meta, summary);

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
  const count = Number(aiCandidateCountInput.value || 5);
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
  const taskId = aiAnalysisForm.dataset.taskId;
  const selected = aiAnalysisForm.querySelector("input[name='ai_prompt_preset_id']:checked");
  if (!selected) {
    throw new Error("请选择一个 AI Prompt 方案");
  }

  const cards = Array.from(aiAnalysisForm.querySelectorAll("[data-prompt-preset-card]"));
  await Promise.all(
    cards.map(async (card) => {
      const presetId = card.dataset.presetId;
      const nameInput = card.querySelector(`[name='preset_name_${presetId}']`);
      const promptInput = card.querySelector(`[name='preset_prompt_${presetId}']`);
      const presetResponse = await fetch(`/api/ai-prompt-presets/${presetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: nameInput?.value || `${card.dataset.presetSlot || ""}号方案`,
          prompt_text: promptInput?.value || "",
        }),
      });
      const presetData = await presetResponse.json();
      if (!presetResponse.ok) {
        throw new Error(presetData.detail || "AI Prompt 方案保存失败");
      }
      return presetData;
    })
  );

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

if (saveAiPromptsButton && aiAnalysisForm) {
  saveAiPromptsButton.addEventListener("click", async () => {
    const originalText = saveAiPromptsButton.textContent;
    saveAiPromptsButton.disabled = true;
    saveAiPromptsButton.textContent = "保存中...";
    if (aiProcessResult) aiProcessResult.textContent = "正在保存 AI Prompt 方案...";

    try {
      const data = await saveTaskAiPromptSettings();
      await saveTaskCandidateClipCount();
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI Prompt 方案已保存。";
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `保存失败：${error.message}`;
    } finally {
      saveAiPromptsButton.disabled = false;
      saveAiPromptsButton.textContent = originalText;
    }
  });
}

document.querySelectorAll(".js-ai-process-action").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!aiAnalysisForm) return;
    const originalText = button.textContent;
    const taskId = aiAnalysisForm.dataset.taskId;
    const provider = button.dataset.provider || "remote";
    const selectedCard = getSelectedPromptPresetCard();
    const selectedPrompt = selectedCard?.querySelector("textarea")?.value.trim() || "";
    const selectedName = selectedCard?.querySelector("input[type='text']")?.value.trim() || "当前方案";
    if (!selectedPrompt) {
      if (aiProcessResult) aiProcessResult.textContent = "请先填写当前选中的 AI Prompt 方案。";
      return;
    }
    if (provider === "remote") {
      const confirmed = window.confirm(`确认使用“${selectedName}”发起 DeepSeek AI 分析吗？\n\n这会重新生成候选片段，并覆盖当前已有的 AI 候选结果。`);
      if (!confirmed) return;
    }
    button.disabled = true;
    button.textContent = "分析中...";
    if (aiProcessResult) aiProcessResult.textContent = "正在保存 Prompt 方案并启动 AI 分析...";
    renderAiAnalysisProgress({
      status: "running",
      percent: 18,
      message: "正在保存 Prompt 方案并启动 AI 分析...",
    });
    renderRuntimeLog({
      status: "running",
      log_lines: ["正在启动 AI 分析，请稍等..."],
    });

    try {
      await saveTaskAiPromptSettings();
      await saveTaskCandidateClipCount();
      pollAiAnalysisStatus(true).catch(() => {});
      const response = await fetch(`/api/tasks/${taskId}/process/ai?provider=${provider}`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "AI 分析失败");
      }
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI 分析完成。";
      await pollAiAnalysisStatus(false).catch(() => {});
      if (aiCandidateCountPill && Array.isArray(data.clips)) {
        aiCandidateCountPill.textContent = `${data.clips.length} 条候选`;
      }
      renderAiAnalysisSummary(data.analysis_run || data);
      renderAiAnalysisHistory(data.runs || aiAnalysisRuns);
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `AI 分析失败：${summarizeErrorMessage(error.message)}`;
      await pollAiAnalysisStatus(false).catch(() => {});
    } finally {
      stopAiAnalysisStatusPolling();
      button.disabled = false;
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

const clipFilterForm = document.querySelector("#clip-filter-form");
if (clipFilterForm) {
  clipFilterForm.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", () => clipFilterForm.submit());
  });
}

const clipReviewForm = document.querySelector("#clip-review-form");
const saveClipsButton = document.querySelector("#save-clips-button");
const generateClipsButton = document.querySelector("#generate-clips-button");
const clipReviewMessage = document.querySelector("#clip-review-message");
const clipPreviewVideo = document.querySelector("#clip-preview-video");
const clipPreviewDock = document.querySelector("#clip-preview-dock");
const clipPreviewCaption = document.querySelector("#clip-preview-caption");
const clipTranscriptDrawer = document.querySelector("#clip-transcript-drawer");
const clipTranscriptTitle = document.querySelector("#clip-transcript-title");
const clipTranscriptTime = document.querySelector("#clip-transcript-time");
const clipTranscriptBody = document.querySelector("#clip-transcript-body");
const closeTranscriptDrawerButton = document.querySelector("#close-transcript-drawer");
const sourceMonitorModal = document.querySelector("#source-monitor-modal");
const closeSourceMonitorButton = document.querySelector("#close-source-monitor");
const cancelSourceMonitorButton = document.querySelector("#cancel-source-monitor");
const applySourceMonitorButton = document.querySelector("#apply-source-monitor");
const sourceMonitorVideo = document.querySelector("#source-monitor-video");
const sourceMonitorTrack = document.querySelector("#source-monitor-track");
const sourceMonitorSlider = document.querySelector("#source-monitor-slider");
const sourceMonitorPlayhead = document.querySelector("#source-monitor-playhead");
const sourceMonitorCurrent = document.querySelector("#source-monitor-current");
const sourceMonitorDuration = document.querySelector("#source-monitor-duration");
const sourceMonitorZoom = document.querySelector("#source-monitor-zoom");
const sourceMonitorWindowStart = document.querySelector("#source-monitor-window-start");
const sourceMonitorWindowEnd = document.querySelector("#source-monitor-window-end");
const sourceMonitorInTime = document.querySelector("#source-monitor-in-time");
const sourceMonitorOutTime = document.querySelector("#source-monitor-out-time");
const sourceMonitorMessage = document.querySelector("#source-monitor-message");
let activePreviewEndSeconds = null;
let activeSourceMonitor = null;
let isSyncingSourceSlider = false;

function showClipReviewMessage(message, tone = "info") {
  if (!clipReviewMessage) return;
  clipReviewMessage.hidden = false;
  clipReviewMessage.textContent = message;
  clipReviewMessage.dataset.tone = tone;
}

function getClipReviewCards() {
  return Array.from(document.querySelectorAll("[data-clip-card]"));
}

function updateClipReviewActionState() {
  const hasCards = getClipReviewCards().length > 0;
  if (saveClipsButton) saveClipsButton.disabled = !hasCards;
  if (generateClipsButton) generateClipsButton.disabled = !hasCards;
}

function collectClipReviewPayload() {
  const cards = getClipReviewCards();
  return cards.map((card) => ({
    id: card.dataset.clipId,
    title: card.querySelector("[name='title']").value.trim(),
    start_time: card.querySelector("[name='start_time']").value.trim(),
    end_time: card.querySelector("[name='end_time']").value.trim(),
    enabled: card.querySelector("[name='enabled']").checked,
    summary: card.querySelector("[name='summary']").value.trim(),
  }));
}

async function deleteClipCard(card, button) {
  if (!clipReviewForm || !card) return;
  const taskId = clipReviewForm.dataset.taskId;
  const clipId = card.dataset.clipId;
  const originalText = button?.textContent || "";
  if (button) {
    button.disabled = true;
    button.textContent = "\u5220\u9664\u4e2d...";
  }
  card.classList.add("is-removing");
  showClipReviewMessage("\u6b63\u5728\u5220\u9664\u8fd9\u6761\u5019\u9009\u7247\u6bb5...", "info");

  try {
    const response = await fetch(`/api/tasks/${taskId}/clips/${clipId}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "\u5220\u9664\u5931\u8d25");
    }
    if (activeSourceMonitor?.card === card) {
      toggleSourceMonitor(false);
    }
    card.remove();
    updateClipReviewActionState();
    closeTranscriptDrawer();
    showClipReviewMessage(
      data.message || "\u5df2\u5220\u9664\u8be5\u5019\u9009\u7247\u6bb5\uff0c\u540e\u7eed\u751f\u6210\u5207\u7247\u4e0d\u4f1a\u518d\u4f7f\u7528\u5b83\u3002",
      "success",
    );
  } catch (error) {
    card.classList.remove("is-removing");
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
    showClipReviewMessage(`\u5220\u9664\u5931\u8d25\uff1a${error.message}`, "error");
  }
}

function timeTextToSeconds(value) {
  const parts = String(value || "")
    .trim()
    .split(":")
    .map((part) => Number(part));
  if (![2, 3].includes(parts.length) || parts.some((part) => !Number.isFinite(part))) {
    return 0;
  }
  if (parts.length === 2) {
    return parts[0] * 60 + parts[1];
  }
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}

function secondsToTimeText(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const restSeconds = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(restSeconds).padStart(2, "0")}`;
}

function formatTimecode(seconds) {
  return `${secondsToTimeText(seconds)}:00`;
}

function updateCardTimeDataset(card) {
  if (!card) return;
  card.dataset.startSeconds = String(timeTextToSeconds(card.querySelector("[name='start_time']")?.value));
  card.dataset.endSeconds = String(timeTextToSeconds(card.querySelector("[name='end_time']")?.value));
}

function setSourceMonitorMessage(message, tone = "info") {
  if (!sourceMonitorMessage) return;
  sourceMonitorMessage.textContent = message;
  sourceMonitorMessage.dataset.tone = tone;
}

function setSourceMonitorControlsDisabled(disabled) {
  document.querySelectorAll("[data-source-action], [data-source-step]").forEach((button) => {
    button.disabled = disabled;
  });
  if (applySourceMonitorButton) applySourceMonitorButton.disabled = disabled;
}

function getReviewMaxClipSeconds() {
  const value = Number(clipReviewForm?.dataset.maxClipSeconds || 0);
  return Number.isFinite(value) && value > 0 ? value : 300;
}

function getSourceVideoDuration() {
  const videoDuration = Number(sourceMonitorVideo?.duration || 0);
  if (Number.isFinite(videoDuration) && videoDuration > 0) return videoDuration;
  if (!activeSourceMonitor) return 1;
  return Math.max(activeSourceMonitor.endSeconds + 60, activeSourceMonitor.startSeconds + 61);
}

function clampSeconds(value, min, max) {
  return Math.max(min, Math.min(Number(value) || 0, max));
}

function getSourceWindowRange() {
  if (!activeSourceMonitor) return { start: 0, end: 1, span: 1 };
  const duration = getSourceVideoDuration();
  const selected = Math.max(1, activeSourceMonitor.endSeconds - activeSourceMonitor.startSeconds);
  const midpoint = (activeSourceMonitor.startSeconds + activeSourceMonitor.endSeconds) / 2;
  const zoom = sourceMonitorZoom?.value || "fit";
  const spanMap = {
    fit: Math.max(120, selected * 4),
    half: Math.max(60, selected * 2),
    quarter: Math.max(30, selected * 1.25),
  };
  const span = Math.min(duration, spanMap[zoom] || spanMap.fit);
  const start = clampSeconds(midpoint - span / 2, 0, Math.max(0, duration - span));
  return { start, end: start + span, span };
}

function normalizeSourceRange(startSeconds, endSeconds, anchor = "end") {
  const duration = getSourceVideoDuration();
  const maxClipSeconds = getReviewMaxClipSeconds();
  const minGap = 1;
  let start = clampSeconds(startSeconds, 0, Math.max(0, duration - minGap));
  let end = clampSeconds(endSeconds, start + minGap, duration);
  let adjusted = false;

  if (end - start > maxClipSeconds) {
    adjusted = true;
    if (anchor === "start") {
      end = Math.min(duration, start + maxClipSeconds);
    } else if (anchor === "end") {
      start = Math.max(0, end - maxClipSeconds);
    } else {
      end = Math.min(duration, start + maxClipSeconds);
    }
  }

  if (end <= start) {
    adjusted = true;
    if (anchor === "end") {
      start = Math.max(0, end - minGap);
    } else {
      end = Math.min(duration, start + minGap);
    }
  }

  return { start, end, adjusted };
}

function updateSourceMonitorReadout(view = getSourceWindowRange()) {
  if (!activeSourceMonitor) return;
  const duration = getSourceVideoDuration();
  const currentSeconds = Number(sourceMonitorVideo?.currentTime || activeSourceMonitor.startSeconds);
  if (sourceMonitorCurrent) sourceMonitorCurrent.textContent = formatTimecode(currentSeconds);
  if (sourceMonitorDuration) sourceMonitorDuration.textContent = `片段 ${secondsToTimeText(activeSourceMonitor.endSeconds - activeSourceMonitor.startSeconds)}`;
  if (sourceMonitorWindowStart) sourceMonitorWindowStart.textContent = secondsToTimeText(view.start);
  if (sourceMonitorWindowEnd) sourceMonitorWindowEnd.textContent = secondsToTimeText(Math.min(duration, view.end));
  if (sourceMonitorInTime) sourceMonitorInTime.textContent = `入点 ${secondsToTimeText(activeSourceMonitor.startSeconds)}`;
  if (sourceMonitorOutTime) sourceMonitorOutTime.textContent = `出点 ${secondsToTimeText(activeSourceMonitor.endSeconds)}`;
}

function updateSourceMonitorPlayhead(view = getSourceWindowRange()) {
  if (!activeSourceMonitor || !sourceMonitorPlayhead) return;
  const currentSeconds = Number(sourceMonitorVideo?.currentTime || activeSourceMonitor.startSeconds);
  const ratio = (clampSeconds(currentSeconds, view.start, view.end) - view.start) / Math.max(1, view.span);
  const leftPadding = 18;
  const rightPadding = 18;
  const trackWidth = sourceMonitorTrack?.clientWidth || 1;
  const usableWidth = Math.max(1, trackWidth - leftPadding - rightPadding);
  sourceMonitorPlayhead.style.left = `${leftPadding + ratio * usableWidth}px`;
}

function handleSourceSliderUpdate(values, handle, unencoded) {
  if (!activeSourceMonitor || isSyncingSourceSlider) return;
  const start = Number(unencoded?.[0] ?? values?.[0]);
  const end = Number(unencoded?.[1] ?? values?.[1]);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  activeSourceMonitor.startSeconds = Math.round(start);
  activeSourceMonitor.endSeconds = Math.round(end);
  updateSourceMonitorReadout();
  updateSourceMonitorPlayhead();
}

function syncSourceSliderOptions(view = getSourceWindowRange()) {
  if (!activeSourceMonitor || !sourceMonitorSlider) return false;
  if (!window.noUiSlider) {
    sourceMonitorSlider.dataset.disabled = "true";
    setSourceMonitorMessage("剪辑滑块组件加载失败，请刷新页面后再试。", "error");
    setSourceMonitorControlsDisabled(true);
    return false;
  }
  sourceMonitorSlider.dataset.disabled = "false";
  setSourceMonitorControlsDisabled(false);

  const options = {
    start: [activeSourceMonitor.startSeconds, activeSourceMonitor.endSeconds],
    connect: [false, true, false],
    behaviour: "tap-drag",
    step: Number(activeSourceMonitor.stepSeconds || 1),
    margin: 1,
    limit: getReviewMaxClipSeconds(),
    range: {
      min: view.start,
      max: Math.max(view.start + 1, view.end),
    },
    pips: {
      mode: "count",
      values: 9,
      density: 4,
    },
  };

  isSyncingSourceSlider = true;
  if (sourceMonitorSlider.noUiSlider) {
    sourceMonitorSlider.noUiSlider.updateOptions(options, false);
  } else {
    window.noUiSlider.create(sourceMonitorSlider, options);
    sourceMonitorSlider.noUiSlider.on("update", handleSourceSliderUpdate);
    sourceMonitorSlider.noUiSlider.on("slide", (values, handle, unencoded) => {
      if (!sourceMonitorVideo || !activeSourceMonitor) return;
      const nextTime = Number(unencoded?.[handle] ?? values?.[handle]);
      if (Number.isFinite(nextTime)) sourceMonitorVideo.currentTime = clampSeconds(nextTime, 0, getSourceVideoDuration());
    });
    sourceMonitorSlider.noUiSlider.on("change", () => {
      setSourceMonitorMessage("已更新入点 / 出点。点击“应用到片段”后，再保存修改。", "success");
    });
  }
  sourceMonitorSlider.noUiSlider.set([activeSourceMonitor.startSeconds, activeSourceMonitor.endSeconds]);
  isSyncingSourceSlider = false;
  return true;
}

function renderSourceMonitor() {
  if (!activeSourceMonitor) return;
  const view = getSourceWindowRange();
  syncSourceSliderOptions(view);
  updateSourceMonitorReadout(view);
  updateSourceMonitorPlayhead(view);
}

function updateSourceMonitorRange(startSeconds, endSeconds, anchor = "end", seekTo = null) {
  if (!activeSourceMonitor) return;
  const normalized = normalizeSourceRange(startSeconds, endSeconds, anchor);
  activeSourceMonitor.startSeconds = normalized.start;
  activeSourceMonitor.endSeconds = normalized.end;
  renderSourceMonitor();
  if (sourceMonitorVideo && seekTo) {
    sourceMonitorVideo.currentTime = seekTo === "out" ? activeSourceMonitor.endSeconds : activeSourceMonitor.startSeconds;
  }
  if (normalized.adjusted) {
    setSourceMonitorMessage(`已按任务限制自动收紧范围，单条最长 ${Math.round(getReviewMaxClipSeconds() / 60)} 分钟。`, "info");
  }
}

function setSourceMonitorTime(seconds) {
  if (!sourceMonitorVideo) return;
  sourceMonitorVideo.currentTime = clampSeconds(seconds, 0, getSourceVideoDuration());
  renderSourceMonitor();
}

function toggleSourceMonitor(show) {
  if (!sourceMonitorModal) return;
  if (show) {
    sourceMonitorModal.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    return;
  }
  sourceMonitorModal.setAttribute("hidden", "");
  document.body.style.overflow = "";
  activeSourceMonitor = null;
  isSyncingSourceSlider = false;
  if (sourceMonitorVideo) sourceMonitorVideo.pause();
}

function openSourceMonitor(card) {
  if (!card || !sourceMonitorVideo) {
    showClipReviewMessage("没有找到源视频，暂时无法打开源监视器。", "error");
    return;
  }
  updateCardTimeDataset(card);
  const startSeconds = Number(card.dataset.startSeconds || 0);
  const endSeconds = Number(card.dataset.endSeconds || startSeconds + 1);
  activeSourceMonitor = {
    card,
    startSeconds,
    endSeconds,
    isPreviewing: false,
    stepSeconds: 1,
  };
  const normalized = normalizeSourceRange(startSeconds, endSeconds, "end");
  activeSourceMonitor = {
    card,
    startSeconds: normalized.start,
    endSeconds: normalized.end,
    isPreviewing: false,
    stepSeconds: 1,
  };
  sourceMonitorVideo.currentTime = activeSourceMonitor.startSeconds;
  if (sourceMonitorZoom) sourceMonitorZoom.value = "fit";
  document.querySelectorAll("[data-source-step]").forEach((button) => {
    button.classList.toggle("active", button.dataset.sourceStep === "1");
  });
  setSourceMonitorMessage("拖动蓝色范围左右手柄，或播放到合适位置后设置入点 / 出点。");
  toggleSourceMonitor(true);
  renderSourceMonitor();
}

function applySourceMonitorToCard() {
  if (!activeSourceMonitor?.card) return;
  const card = activeSourceMonitor.card;
  const startInput = card.querySelector("[name='start_time']");
  const endInput = card.querySelector("[name='end_time']");
  if (startInput) startInput.value = secondsToTimeText(activeSourceMonitor.startSeconds);
  if (endInput) endInput.value = secondsToTimeText(activeSourceMonitor.endSeconds);
  updateCardTimeDataset(card);
  const durationPill = card.querySelector(".status-pill");
  if (durationPill) durationPill.textContent = `${Math.round(activeSourceMonitor.endSeconds - activeSourceMonitor.startSeconds)} 秒`;
  showClipReviewMessage("已应用新的入点 / 出点。确认无误后，请点击右侧“保存修改”写入数据库。", "success");
  toggleSourceMonitor(false);
}

document.querySelectorAll("[data-clip-card] input[name='start_time'], [data-clip-card] input[name='end_time']").forEach((input) => {
  input.addEventListener("change", () => updateCardTimeDataset(input.closest("[data-clip-card]")));
});

if (saveClipsButton && clipReviewForm) {
  saveClipsButton.addEventListener("click", async () => {
    const taskId = clipReviewForm.dataset.taskId;
    const originalText = saveClipsButton.textContent;
    saveClipsButton.disabled = true;
    saveClipsButton.textContent = "正在保存...";
    showClipReviewMessage("正在保存候选片段修改...", "info");

    try {
      const response = await fetch(`/api/tasks/${taskId}/clips/batch-update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ clips: collectClipReviewPayload() }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "保存失败");
      }
      showClipReviewMessage(data.message || "保存成功。", "success");
    } catch (error) {
      showClipReviewMessage(`保存失败：${error.message}`, "error");
    } finally {
      saveClipsButton.disabled = false;
      saveClipsButton.textContent = originalText;
    }
  });
}

function playClipPreview(card) {
  if (!clipPreviewVideo || !card) return;
  updateCardTimeDataset(card);
  const startSeconds = Number(card.dataset.startSeconds || 0);
  const endSeconds = Number(card.dataset.endSeconds || 0);
  activePreviewEndSeconds = Number.isFinite(endSeconds) && endSeconds > startSeconds ? endSeconds : null;
  clipPreviewVideo.currentTime = Math.max(0, startSeconds);
  clipPreviewVideo.play().catch(() => {});
  if (clipPreviewCaption) {
    const title = card.dataset.title || "当前片段";
    clipPreviewCaption.textContent = `${title}：从 ${card.querySelector("[name='start_time']")?.value || ""} 播放到 ${card.querySelector("[name='end_time']")?.value || ""}`;
  }
  ensureClipPreviewVisible();
}

function ensureClipPreviewVisible() {
  const previewTarget = clipPreviewDock || clipPreviewVideo;
  if (!previewTarget) return;
  const rect = previewTarget.getBoundingClientRect();
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
  const isVisible = rect.top >= 0 && rect.top < viewportHeight * 0.72 && rect.bottom > Math.min(120, viewportHeight);
  if (isVisible) return;
  previewTarget.scrollIntoView({ behavior: "smooth", block: "start", inline: "nearest" });
}

function closeTranscriptDrawer() {
  if (!clipTranscriptDrawer) return;
  clipTranscriptDrawer.hidden = true;
  document.body.classList.remove("transcript-drawer-open");
}

function renderTranscriptRows(rows) {
  if (!clipTranscriptBody) return;
  clipTranscriptBody.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note";
    empty.textContent = "这一段暂时没有匹配到逐句转写。可以先查看完整转写，或重新生成转写后再试。";
    clipTranscriptBody.append(empty);
    return;
  }
  rows.forEach((row) => {
    const item = document.createElement("article");
    item.className = "transcript-line";
    const time = document.createElement("time");
    time.textContent = `${row.start_time} - ${row.end_time}`;
    const text = document.createElement("p");
    text.textContent = row.text;
    item.append(time, text);
    clipTranscriptBody.append(item);
  });
}

async function openTranscriptDrawer(card) {
  if (!clipTranscriptDrawer || !clipReviewForm || !card) return;
  updateCardTimeDataset(card);
  const taskId = clipReviewForm.dataset.taskId;
  const clipId = card.dataset.clipId;
  clipTranscriptDrawer.hidden = false;
  document.body.classList.add("transcript-drawer-open");
  if (clipTranscriptTitle) clipTranscriptTitle.textContent = card.dataset.title || "片段转写";
  if (clipTranscriptTime) {
    const startTime = card.querySelector("[name='start_time']")?.value || "";
    const endTime = card.querySelector("[name='end_time']")?.value || "";
    clipTranscriptTime.textContent = `${startTime} - ${endTime}`;
  }
  if (clipTranscriptBody) {
    clipTranscriptBody.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "empty-note";
    loading.textContent = "正在读取这一段转写...";
    clipTranscriptBody.append(loading);
  }
  try {
    const startTime = card.querySelector("[name='start_time']")?.value || "";
    const endTime = card.querySelector("[name='end_time']")?.value || "";
    const params = new URLSearchParams({ start_time: startTime, end_time: endTime });
    const response = await fetch(`/api/tasks/${taskId}/clips/${clipId}/transcript-excerpt?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "读取转写失败");
    }
    if (clipTranscriptTitle) clipTranscriptTitle.textContent = data.title || "片段转写";
    if (clipTranscriptTime) clipTranscriptTime.textContent = `${data.start_time} - ${data.end_time}`;
    renderTranscriptRows(data.rows || []);
  } catch (error) {
    if (clipTranscriptBody) {
      clipTranscriptBody.replaceChildren();
      const failed = document.createElement("p");
      failed.className = "empty-note";
      failed.textContent = `读取失败：${error.message}`;
      clipTranscriptBody.append(failed);
    }
  }
}

document.querySelectorAll("[data-preview-trigger]").forEach((button) => {
  button.addEventListener("click", () => {
    playClipPreview(button.closest("[data-clip-card]"));
  });
});

document.querySelectorAll("[data-transcript-trigger]").forEach((button) => {
  button.addEventListener("click", () => {
    openTranscriptDrawer(button.closest("[data-clip-card]"));
  });
});

document.querySelectorAll("[data-source-monitor-trigger]").forEach((button) => {
  button.addEventListener("click", () => {
    openSourceMonitor(button.closest("[data-clip-card]"));
  });
});

document.querySelectorAll("[data-delete-trigger]").forEach((button) => {
  button.addEventListener("click", () => {
    deleteClipCard(button.closest("[data-clip-card]"), button);
  });
});

if (closeTranscriptDrawerButton) {
  closeTranscriptDrawerButton.addEventListener("click", closeTranscriptDrawer);
}

if (clipPreviewVideo) {
  clipPreviewVideo.addEventListener("timeupdate", () => {
    if (activePreviewEndSeconds !== null && clipPreviewVideo.currentTime >= activePreviewEndSeconds) {
      clipPreviewVideo.pause();
      activePreviewEndSeconds = null;
    }
  });
}

if (closeSourceMonitorButton) {
  closeSourceMonitorButton.addEventListener("click", () => toggleSourceMonitor(false));
}

if (cancelSourceMonitorButton) {
  cancelSourceMonitorButton.addEventListener("click", () => toggleSourceMonitor(false));
}

if (applySourceMonitorButton) {
  applySourceMonitorButton.addEventListener("click", applySourceMonitorToCard);
}

if (sourceMonitorModal) {
  sourceMonitorModal.addEventListener("click", (event) => {
    if (event.target === sourceMonitorModal) toggleSourceMonitor(false);
  });
}

if (sourceMonitorZoom) {
  sourceMonitorZoom.addEventListener("change", renderSourceMonitor);
}

document.querySelectorAll("[data-source-step]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!activeSourceMonitor) return;
    document.querySelectorAll("[data-source-step]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    activeSourceMonitor.stepSeconds = Number(button.dataset.sourceStep || 1);
    renderSourceMonitor();
  });
});

document.querySelectorAll("[data-source-action]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!activeSourceMonitor || !sourceMonitorVideo) return;
    const action = button.dataset.sourceAction;
    const current = Number(sourceMonitorVideo.currentTime || activeSourceMonitor.startSeconds);
    const step = Number(activeSourceMonitor.stepSeconds || 1);
    if (action === "mark-in") updateSourceMonitorRange(current, activeSourceMonitor.endSeconds, "start", "in");
    if (action === "mark-out") updateSourceMonitorRange(activeSourceMonitor.startSeconds, current, "end", "out");
    if (action === "jump-in") setSourceMonitorTime(activeSourceMonitor.startSeconds);
    if (action === "jump-out") setSourceMonitorTime(activeSourceMonitor.endSeconds);
    if (action === "preview") {
      activeSourceMonitor.isPreviewing = true;
      setSourceMonitorTime(activeSourceMonitor.startSeconds);
      sourceMonitorVideo.play().catch(() => {});
    }
    if (action === "in-back") updateSourceMonitorRange(activeSourceMonitor.startSeconds - step, activeSourceMonitor.endSeconds, "start", "in");
    if (action === "in-forward") updateSourceMonitorRange(activeSourceMonitor.startSeconds + step, activeSourceMonitor.endSeconds, "start", "in");
    if (action === "out-back") updateSourceMonitorRange(activeSourceMonitor.startSeconds, activeSourceMonitor.endSeconds - step, "end", "out");
    if (action === "out-forward") updateSourceMonitorRange(activeSourceMonitor.startSeconds, activeSourceMonitor.endSeconds + step, "end", "out");
  });
});

if (sourceMonitorVideo) {
  sourceMonitorVideo.addEventListener("loadedmetadata", renderSourceMonitor);
  sourceMonitorVideo.addEventListener("timeupdate", () => {
    if (!activeSourceMonitor) return;
    if (activeSourceMonitor.isPreviewing && sourceMonitorVideo.currentTime >= activeSourceMonitor.endSeconds) {
      sourceMonitorVideo.pause();
      activeSourceMonitor.isPreviewing = false;
      setSourceMonitorTime(activeSourceMonitor.endSeconds);
      setSourceMonitorMessage("当前片段预览已到出点。", "success");
      return;
    }
    renderSourceMonitor();
  });
}

window.addEventListener("resize", () => {
  if (activeSourceMonitor) renderSourceMonitor();
});

if (generateClipsButton) {
  generateClipsButton.addEventListener("click", async () => {
    const originalText = generateClipsButton.textContent;
    generateClipsButton.disabled = true;
    generateClipsButton.textContent = "正在确认...";
    showClipReviewMessage("正在请求生成切片...", "info");

    try {
      const response = await fetch(generateClipsButton.dataset.endpoint, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "生成切片请求失败");
      }
      showClipReviewMessage(`${data.message || "切片生成完成。"} 可进入“字幕推送”继续加字幕、打码和发布配置。`, "success");
    } catch (error) {
      showClipReviewMessage(`生成切片失败：${error.message}`, "error");
    } finally {
      generateClipsButton.disabled = false;
      generateClipsButton.textContent = originalText;
    }
  });
}

document.querySelectorAll(".js-hide-task").forEach((button) => {
  button.addEventListener("click", async () => {
    const taskTitle = button.dataset.taskTitle || "这条任务";
    const confirmed = window.confirm(`确认把“${taskTitle}”移入 E 盘回收站吗？\n\n这会从列表隐藏任务，并把对应项目文件夹移动到 E:\\直播间切片工作流存储\\_回收站，不会删除原视频、切片文件和任务目录。`);
    if (!confirmed) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "移动中...";

    try {
      const response = await fetch(`/api/tasks/${button.dataset.taskId}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "移入回收站失败");
      }
      window.alert(data.message || "任务已移入回收站。");
      window.location.reload();
    } catch (error) {
      window.alert(`移入回收站失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

const subtitleStyleForm = document.querySelector("#subtitle-style-form");
const subtitleStyleResult = document.querySelector("#subtitle-style-result");

if (subtitleStyleForm) {
  subtitleStyleForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = subtitleStyleForm.querySelector("button[type='submit']");
    const formData = new FormData(subtitleStyleForm);
    const payload = Object.fromEntries(formData.entries());
    payload.font_size = Number(payload.font_size || 42);
    payload.shadow_enabled = Boolean(subtitleStyleForm.elements.shadow_enabled?.checked);
    if (submitButton) submitButton.disabled = true;
    if (subtitleStyleResult) subtitleStyleResult.textContent = "正在保存字幕样式...";

    try {
      const response = await fetch("/api/tasks/subtitle-style", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "字幕样式保存失败");
      }
      if (subtitleStyleResult) subtitleStyleResult.textContent = data.message || "字幕样式已保存。";
    } catch (error) {
      if (subtitleStyleResult) subtitleStyleResult.textContent = `保存失败：${error.message}`;
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
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
    runtimeLogState.textContent = labelMap[status.status] || status.task_status_label || "已刷新";
    runtimeLogState.dataset.status = status.status || "idle";
  }
  if (!runtimeLogLines) return;
  const lines = Array.isArray(status.log_lines) ? status.log_lines : [];
  runtimeLogLines.textContent = lines.length ? lines.join("\n") : "暂无运行日志。点击 AI 分析后，这里会自动刷新。";
  runtimeLogLines.scrollTop = runtimeLogLines.scrollHeight;
}

async function pollAiAnalysisStatus(keepPolling = false) {
  if (!aiAnalysisForm) return null;
  const taskId = aiAnalysisForm.dataset.taskId;
  const response = await fetch(`/api/tasks/${taskId}/ai-analysis-status`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "读取 AI 分析状态失败");
  }
  renderAiAnalysisProgress(data);
  renderRuntimeLog(data);
  if (aiStatusPollingTimer) {
    window.clearTimeout(aiStatusPollingTimer);
    aiStatusPollingTimer = null;
  }
  if (keepPolling || data.is_running) {
    aiStatusPollingTimer = window.setTimeout(() => {
      pollAiAnalysisStatus(false).catch(() => {});
    }, 3000);
  }
  return data;
}

function stopAiAnalysisStatusPolling() {
  if (!aiStatusPollingTimer) return;
  window.clearTimeout(aiStatusPollingTimer);
  aiStatusPollingTimer = null;
}

document.querySelectorAll("[data-render-subtitle]").forEach((button) => {
  button.addEventListener("click", async () => {
    const card = button.closest("[data-subtitle-output-card]");
    if (!card) return;
    const taskId = card.dataset.taskId;
    const outputId = card.dataset.outputId;
    const statusNode = card.querySelector("[data-subtitle-status]");
    const errorNode = card.querySelector("[data-subtitle-error]");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "加字幕中...";
    if (statusNode) statusNode.textContent = "字幕生成中";
    if (errorNode) errorNode.textContent = "";

    try {
      const response = await fetch(`/api/tasks/${taskId}/output-clips/${outputId}/subtitles`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "自动加字幕失败");
      }
      if (statusNode) statusNode.textContent = data.output_clip?.subtitle_status_label || "已加字幕";
      if (errorNode) errorNode.textContent = data.message || "自动加字幕完成。";
      window.setTimeout(() => window.location.reload(), 700);
    } catch (error) {
      if (statusNode) statusNode.textContent = "字幕失败";
      if (errorNode) errorNode.textContent = `自动加字幕失败：${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

const cutEditModal = document.querySelector("#cut-edit-modal");
const closeCutEditButton = document.querySelector("#close-cut-edit");
const cutEditVideo = document.querySelector("#cut-edit-video");
const cutEditCaption = document.querySelector("#cut-edit-caption");
const playCutPreviewButton = document.querySelector("#play-cut-preview");
const saveCutEditButton = document.querySelector("#save-cut-edit");
const cutEditSaveMessage = document.querySelector("#cut-edit-save-message");
const trimFrameTrack = document.querySelector("#trim-frame-track");
const trimSlider = document.querySelector("#trim-slider");
const trimStripPlayButton = document.querySelector("#trim-strip-play");
const trimPlayhead = document.querySelector("#trim-playhead");
const trimStartInput = document.querySelector("#trim-start-input");
const trimEndInput = document.querySelector("#trim-end-input");
const trimDurationLabel = document.querySelector("#trim-duration-label");
const trimStartPosition = document.querySelector("#trim-start-position");
const trimCurrentPosition = document.querySelector("#trim-current-position");
const trimEndPosition = document.querySelector("#trim-end-position");
const setTrimStartButton = document.querySelector("#set-trim-start");
const setTrimEndButton = document.querySelector("#set-trim-end");
let activeTrimState = null;
let isSyncingTrimSlider = false;

function setCutEditMessage(message, tone = "info") {
  if (!cutEditSaveMessage) return;
  cutEditSaveMessage.textContent = message;
  cutEditSaveMessage.dataset.tone = tone;
}

function getTrimDuration() {
  if (!activeTrimState) return 1;
  const videoDuration = Number(cutEditVideo?.duration || 0);
  return Math.max(1, videoDuration || activeTrimState.durationSeconds || activeTrimState.endSeconds || 1);
}

function clampTrimSeconds(value, min, max) {
  return Math.max(min, Math.min(Number(value) || 0, max));
}

function normalizeTrimRange(startSeconds, endSeconds) {
  const duration = getTrimDuration();
  const minGap = Math.min(1, Math.max(0.1, duration / 10));
  let start = clampTrimSeconds(startSeconds, 0, Math.max(0, duration - minGap));
  let end = clampTrimSeconds(endSeconds, start + minGap, duration);
  if (end - start < minGap) {
    if (end >= duration) {
      start = Math.max(0, duration - minGap);
      end = duration;
    } else {
      end = Math.min(duration, start + minGap);
    }
  }
  return { start, end };
}

function getTrimSliderInstance() {
  return trimSlider?.noUiSlider || null;
}

function updateTrimPlayhead() {
  if (!activeTrimState || !trimPlayhead || !trimFrameTrack) return;
  const duration = getTrimDuration();
  const currentSeconds = clampTrimSeconds(cutEditVideo?.currentTime || activeTrimState.startSeconds, 0, duration);
  const playWidth = trimStripPlayButton?.offsetWidth || 72;
  const trackWidth = trimFrameTrack.clientWidth || 1;
  const usableWidth = Math.max(1, trackWidth - playWidth);
  trimPlayhead.style.left = `${playWidth + (currentSeconds / duration) * usableWidth}px`;
}

function updateTrimReadout() {
  if (!activeTrimState) return;
  const currentSeconds = clampTrimSeconds(cutEditVideo?.currentTime || activeTrimState.startSeconds, 0, getTrimDuration());
  if (trimStartInput) trimStartInput.value = secondsToTimeText(activeTrimState.startSeconds);
  if (trimEndInput) trimEndInput.value = secondsToTimeText(activeTrimState.endSeconds);
  if (trimDurationLabel) trimDurationLabel.textContent = `选中 ${secondsToTimeText(activeTrimState.endSeconds - activeTrimState.startSeconds)}`;
  if (trimStartPosition) trimStartPosition.textContent = secondsToTimeText(activeTrimState.startSeconds);
  if (trimCurrentPosition) trimCurrentPosition.textContent = secondsToTimeText(currentSeconds);
  if (trimEndPosition) trimEndPosition.textContent = secondsToTimeText(activeTrimState.endSeconds);
  updateTrimPlayhead();
}

function handleTrimSliderUpdate(values, handle, unencoded) {
  if (!activeTrimState || isSyncingTrimSlider) return;
  const start = Number(unencoded?.[0] ?? values?.[0]);
  const end = Number(unencoded?.[1] ?? values?.[1]);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  const normalized = normalizeTrimRange(start, end);
  activeTrimState.startSeconds = Math.round(normalized.start);
  activeTrimState.endSeconds = Math.round(normalized.end);
  updateTrimReadout();
}

function syncTrimSliderOptions() {
  if (!activeTrimState || !trimSlider) return false;
  if (!window.noUiSlider) {
    trimSlider.dataset.disabled = "true";
    setCutEditMessage("剪切滑块组件加载失败，请刷新页面后再试。", "error");
    return false;
  }

  const duration = getTrimDuration();
  const normalized = normalizeTrimRange(activeTrimState.startSeconds, activeTrimState.endSeconds);
  activeTrimState.startSeconds = Math.round(normalized.start);
  activeTrimState.endSeconds = Math.round(normalized.end);
  activeTrimState.durationSeconds = duration;
  trimSlider.dataset.disabled = "false";

  const options = {
    start: [activeTrimState.startSeconds, activeTrimState.endSeconds],
    connect: [false, true, false],
    behaviour: "tap-drag",
    step: 1,
    margin: Math.min(1, Math.max(0.1, duration / 10)),
    range: {
      min: 0,
      max: duration,
    },
  };

  isSyncingTrimSlider = true;
  if (trimSlider.noUiSlider) {
    trimSlider.noUiSlider.updateOptions(options, false);
  } else {
    window.noUiSlider.create(trimSlider, options);
    trimSlider.noUiSlider.on("update", handleTrimSliderUpdate);
    trimSlider.noUiSlider.on("slide", (values, handle, unencoded) => {
      if (!cutEditVideo || !activeTrimState) return;
      const nextTime = Number(unencoded?.[handle] ?? values?.[handle]);
      if (Number.isFinite(nextTime)) cutEditVideo.currentTime = clampTrimSeconds(nextTime, 0, getTrimDuration());
    });
    trimSlider.noUiSlider.on("change", () => {
      setCutEditMessage("已更新这个片段里的入点 / 出点，点击保存后会写回片段审核。", "success");
    });
  }
  trimSlider.noUiSlider.set([activeTrimState.startSeconds, activeTrimState.endSeconds]);
  isSyncingTrimSlider = false;
  updateTrimReadout();
  return true;
}

function renderTrimEditor() {
  syncTrimSliderOptions();
}

function updateTrimRange(startSeconds, endSeconds, seekTo = "start") {
  if (!activeTrimState) return;
  const normalized = normalizeTrimRange(startSeconds, endSeconds);
  activeTrimState.startSeconds = Math.round(normalized.start);
  activeTrimState.endSeconds = Math.round(normalized.end);
  const slider = getTrimSliderInstance();
  if (slider) slider.set([activeTrimState.startSeconds, activeTrimState.endSeconds]);
  updateTrimReadout();
  if (cutEditVideo) {
    cutEditVideo.currentTime = seekTo === "end" ? activeTrimState.endSeconds : activeTrimState.startSeconds;
  }
}

function toggleCutEditModal(show) {
  if (!cutEditModal) return;
  if (show) {
    cutEditModal.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    return;
  }
  cutEditModal.setAttribute("hidden", "");
  document.body.style.overflow = "";
  if (cutEditVideo) cutEditVideo.pause();
  activeTrimState = null;
  isSyncingTrimSlider = false;
}

document.querySelectorAll("[data-cut-edit-trigger]").forEach((button) => {
  button.addEventListener("click", () => {
    const card = button.closest("[data-subtitle-output-card]");
    const clipVideo = card?.querySelector(":scope > video");
    if (!card || !cutEditVideo || !clipVideo) return;
    const absoluteStartSeconds = Number(card.dataset.startSeconds || 0);
    const absoluteEndSeconds = Number(card.dataset.endSeconds || absoluteStartSeconds + 1);
    const outputBaseStartSeconds = Number(card.dataset.outputBaseStartSeconds || card.dataset.startSeconds || 0);
    const fallbackDuration = Math.max(
      1,
      Number(card.dataset.durationSeconds || 0),
      Number(clipVideo.duration || 0),
      absoluteEndSeconds - outputBaseStartSeconds
    );
    const localStartSeconds = clampTrimSeconds(absoluteStartSeconds - outputBaseStartSeconds, 0, fallbackDuration);
    const localEndSeconds = clampTrimSeconds(absoluteEndSeconds - outputBaseStartSeconds, localStartSeconds + 1, fallbackDuration);
    activeTrimState = {
      card,
      taskId: card.dataset.taskId || "",
      clipId: card.dataset.clipId || "",
      title: card.dataset.clipTitle || card.querySelector(".subtitle-output-body strong")?.textContent || "当前切片",
      summary: card.dataset.clipSummary || "",
      enabled: card.dataset.clipEnabled !== "0",
      outputBaseStartSeconds,
      durationSeconds: fallbackDuration,
      startSeconds: localStartSeconds,
      endSeconds: localEndSeconds,
    };
    cutEditVideo.src = card.dataset.outputMediaUrl || clipVideo.currentSrc || clipVideo.src;
    cutEditVideo.load();
    if (cutEditCaption) {
      cutEditCaption.textContent = activeTrimState.title;
    }
    setCutEditMessage("当前只裁这个已生成片段：拖动黄色左右把手调整入点和出点。", "info");
    toggleCutEditModal(true);
    renderTrimEditor();
    cutEditVideo.currentTime = Math.max(0, activeTrimState.startSeconds);
  });
});

if (closeCutEditButton) {
  closeCutEditButton.addEventListener("click", () => toggleCutEditModal(false));
}

if (cutEditModal) {
  cutEditModal.addEventListener("click", (event) => {
    if (event.target === cutEditModal) toggleCutEditModal(false);
  });
}

if (playCutPreviewButton && cutEditVideo) {
  playCutPreviewButton.addEventListener("click", () => {
    if (activeTrimState) {
      cutEditVideo.currentTime = Math.max(0, activeTrimState.startSeconds);
    }
    cutEditVideo.play().catch(() => {});
  });
}

if (trimStripPlayButton && cutEditVideo) {
  trimStripPlayButton.addEventListener("click", () => {
    if (activeTrimState) {
      cutEditVideo.currentTime = Math.max(0, activeTrimState.startSeconds);
    }
    cutEditVideo.play().catch(() => {});
  });
}

if (cutEditVideo) {
  cutEditVideo.addEventListener("loadedmetadata", () => {
    if (!activeTrimState) return;
    activeTrimState.durationSeconds = getTrimDuration();
    const normalized = normalizeTrimRange(activeTrimState.startSeconds, activeTrimState.endSeconds);
    activeTrimState.startSeconds = Math.round(normalized.start);
    activeTrimState.endSeconds = Math.round(normalized.end);
    cutEditVideo.currentTime = activeTrimState.startSeconds;
    renderTrimEditor();
  });
  cutEditVideo.addEventListener("timeupdate", () => {
    if (!activeTrimState) return;
    updateTrimReadout();
    if (cutEditVideo.currentTime >= activeTrimState.endSeconds) {
      cutEditVideo.pause();
      cutEditVideo.currentTime = activeTrimState.startSeconds;
    }
  });
}

if (trimStartInput) {
  trimStartInput.addEventListener("change", () => {
    updateTrimRange(timeTextToSeconds(trimStartInput.value), activeTrimState?.endSeconds || 1, "start");
  });
}

if (trimEndInput) {
  trimEndInput.addEventListener("change", () => {
    updateTrimRange(activeTrimState?.startSeconds || 0, timeTextToSeconds(trimEndInput.value), "end");
  });
}

if (setTrimStartButton && cutEditVideo) {
  setTrimStartButton.addEventListener("click", () => {
    updateTrimRange(cutEditVideo.currentTime, activeTrimState?.endSeconds || cutEditVideo.currentTime + 1, "start");
  });
}

if (setTrimEndButton && cutEditVideo) {
  setTrimEndButton.addEventListener("click", () => {
    updateTrimRange(activeTrimState?.startSeconds || 0, cutEditVideo.currentTime, "end");
  });
}

if (saveCutEditButton) {
  saveCutEditButton.addEventListener("click", async () => {
    if (!activeTrimState?.taskId || !activeTrimState?.clipId) {
      setCutEditMessage("这条切片没有关联到候选片段，不能直接保存时间。", "error");
      return;
    }
    const originalText = saveCutEditButton.textContent;
    saveCutEditButton.disabled = true;
    saveCutEditButton.textContent = "保存中...";
    setCutEditMessage("正在保存这个片段内的入点和出点...", "info");
    const absoluteStartSeconds = Math.round(activeTrimState.outputBaseStartSeconds + activeTrimState.startSeconds);
    const absoluteEndSeconds = Math.round(activeTrimState.outputBaseStartSeconds + activeTrimState.endSeconds);
    try {
      const response = await fetch(`/api/tasks/${activeTrimState.taskId}/clips/${activeTrimState.clipId}/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: activeTrimState.title,
          start_time: secondsToTimeText(absoluteStartSeconds),
          end_time: secondsToTimeText(absoluteEndSeconds),
          enabled: activeTrimState.enabled,
          summary: activeTrimState.summary,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "保存失败");
      }
      activeTrimState.card.dataset.startSeconds = String(absoluteStartSeconds);
      activeTrimState.card.dataset.endSeconds = String(absoluteEndSeconds);
      activeTrimState.card.dataset.durationSeconds = String(Math.max(1, absoluteEndSeconds - absoluteStartSeconds));
      activeTrimState.card.dataset.startTime = secondsToTimeText(absoluteStartSeconds);
      activeTrimState.card.dataset.endTime = secondsToTimeText(absoluteEndSeconds);
      setCutEditMessage("已保存。回到片段审核页重新生成切片后，会得到新的视频文件。", "success");
    } catch (error) {
      setCutEditMessage(`保存失败：${error.message}`, "error");
    } finally {
      saveCutEditButton.disabled = false;
      saveCutEditButton.textContent = originalText;
    }
  });
}

window.addEventListener("resize", () => {
  if (activeTrimState) updateTrimPlayhead();
});

document.querySelectorAll(".js-demo-toast").forEach((button) => {
  button.addEventListener("click", () => {
    window.alert(button.dataset.message || "这个功能已经预留入口，后续会继续接入。");
  });
});

const aiConfigModal = document.querySelector("#ai-config-modal");
const aiConfigForm = document.querySelector("#ai-config-form");
const aiConfigResult = document.querySelector("#ai-config-result");
const openAiConfigButton = document.querySelector("#open-ai-config");
const closeAiConfigButton = document.querySelector("#close-ai-config");
const cancelAiConfigButton = document.querySelector("#cancel-ai-config");

function toggleAiConfigModal(show) {
  if (!aiConfigModal) return;
  if (show) {
    aiConfigModal.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    closeAiConfigButton?.focus();
    return;
  }
  aiConfigModal.setAttribute("hidden", "");
  document.body.style.overflow = "";
}

if (openAiConfigButton) {
  openAiConfigButton.addEventListener("click", () => toggleAiConfigModal(true));
}

[closeAiConfigButton, cancelAiConfigButton].forEach((button) => {
  if (!button) return;
  button.addEventListener("click", () => toggleAiConfigModal(false));
});

if (aiConfigModal) {
  aiConfigModal.addEventListener("click", (event) => {
    if (event.target === aiConfigModal) {
      toggleAiConfigModal(false);
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && aiConfigModal && !aiConfigModal.hidden) {
    toggleAiConfigModal(false);
  }
});

if (aiConfigForm) {
  aiConfigForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = aiConfigForm.querySelector("button[type='submit']");
    const formData = new FormData(aiConfigForm);
    const payload = Object.fromEntries(formData.entries());
    payload.ai_request_timeout_seconds = Number(payload.ai_request_timeout_seconds || 120);
    payload.ai_local_health_timeout_seconds = Number(payload.ai_local_health_timeout_seconds || 30);

    submitButton.disabled = true;
    if (aiConfigResult) aiConfigResult.textContent = "正在保存 AI 配置...";

    try {
      const response = await fetch("/api/settings/ai", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "保存失败");
      }
      if (aiConfigResult) aiConfigResult.textContent = data.message || "保存成功。";
      window.setTimeout(() => {
        toggleAiConfigModal(false);
        window.location.reload();
      }, 500);
    } catch (error) {
      if (aiConfigResult) aiConfigResult.textContent = `保存失败：${error.message}`;
    } finally {
      submitButton.disabled = false;
    }
  });
}

document.querySelectorAll("[data-publish-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const platform = tab.dataset.publishTab;
    document.querySelectorAll("[data-publish-tab]").forEach((item) => {
      item.classList.toggle("active", item === tab);
    });
    document.querySelectorAll("[data-publish-panel]").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.publishPanel === platform);
    });
  });
});

function setPublishMessage(node, message, tone = "info") {
  if (!node) return;
  node.textContent = message;
  node.dataset.tone = tone;
}

function publishFormPayload(form, submitter = null) {
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());
  if (submitter?.dataset.publishMode) {
    payload.publish_mode = submitter.dataset.publishMode;
  }
  if (submitter?.dataset.batchMode) {
    payload.publish_mode = submitter.dataset.batchMode;
  }
  payload.allow_download = Boolean(form.elements.allow_download?.checked);
  payload.cover_time_seconds = Number(payload.cover_time_seconds || 0);
  return payload;
}

function setCoverPreview(form, coverUrl) {
  const preview = form.querySelector("[data-cover-preview]");
  const image = preview?.querySelector("img");
  const emptyText = preview?.querySelector("span");
  if (!preview || !image || !emptyText) return;
  if (coverUrl) {
    image.src = `${coverUrl}?t=${Date.now()}`;
    image.hidden = false;
    emptyText.hidden = true;
    preview.classList.add("has-cover");
    return;
  }
  image.removeAttribute("src");
  image.hidden = true;
  emptyText.hidden = false;
  preview.classList.remove("has-cover");
}

document.querySelectorAll("[data-publish-config-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const resultNode = form.querySelector("[data-publish-config-result]");
    const submitButton = form.querySelector("button[type='submit']");
    const payload = publishFormPayload(form);
    submitButton.disabled = true;
    setPublishMessage(resultNode, "正在保存平台配置...");

    try {
      const response = await fetch(`/api/publish/platforms/${form.dataset.platform}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "保存失败");
      }
      setPublishMessage(resultNode, data.message || "配置已保存。", "success");
    } catch (error) {
      setPublishMessage(resultNode, `保存失败：${error.message}`, "error");
    } finally {
      submitButton.disabled = false;
    }
  });
});

document.querySelectorAll("[data-test-publish-config]").forEach((button) => {
  button.addEventListener("click", async () => {
    const form = button.closest("[data-publish-config-form]");
    const resultNode = form?.querySelector("[data-publish-config-result]");
    button.disabled = true;
    setPublishMessage(resultNode, "正在检查配置...");

    try {
      const response = await fetch(`/api/publish/platforms/${button.dataset.platform}/test`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "检查失败");
      }
      setPublishMessage(resultNode, data.message || "配置检查完成。", data.status === "ok" ? "success" : "error");
    } catch (error) {
      setPublishMessage(resultNode, `检查失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  });
});

document.querySelectorAll("[data-douyin-oauth]").forEach((button) => {
  button.addEventListener("click", async () => {
    const form = button.closest("[data-publish-config-form]");
    const resultNode = form?.querySelector("[data-publish-config-result]");
    button.disabled = true;
    setPublishMessage(resultNode, "正在生成抖音授权链接...");

    try {
      const response = await fetch("/api/publish/douyin/oauth-url");
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "生成授权链接失败");
      }
      window.open(data.url, "_blank", "noopener,noreferrer");
      setPublishMessage(resultNode, "已打开抖音授权页。授权后会回到本地发布中心。", "success");
    } catch (error) {
      setPublishMessage(resultNode, `授权失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  });
});

document.querySelectorAll("[data-publish-account-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const resultNode = form.querySelector("[data-publish-account-result]");
    const submitButton = form.querySelector("button[type='submit']");
    const payload = publishFormPayload(form);
    submitButton.disabled = true;
    setPublishMessage(resultNode, "正在保存账号...");

    try {
      const response = await fetch("/api/publish/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "账号保存失败");
      }
      setPublishMessage(resultNode, data.message || "账号已保存，正在刷新...", "success");
      window.setTimeout(() => window.location.reload(), 600);
    } catch (error) {
      setPublishMessage(resultNode, `账号保存失败：${error.message}`, "error");
    } finally {
      submitButton.disabled = false;
    }
  });
});

document.querySelectorAll("[data-publish-job-form]").forEach((form) => {
  form.querySelectorAll("input[name='video_source']").forEach((input) => {
    input.addEventListener("change", () => {
      form.elements.cover_file_path.value = "";
      form.elements.cover_mode.value = "auto";
      setCoverPreview(form, "");
      setPublishMessage(form.querySelector("[data-cover-result]"), "视频版本已切换，请重新生成封面。");
    });
  });
});

document.querySelectorAll("[data-generate-cover]").forEach((button) => {
  button.addEventListener("click", async () => {
    const form = button.closest("[data-publish-job-form]");
    if (!form) return;
    const resultNode = form.querySelector("[data-cover-result]");
    const payload = publishFormPayload(form);
    payload.cover_mode = "time";
    if (!String(payload.title || "").trim()) {
      setPublishMessage(resultNode, "请先填写标题，封面会使用这个标题作为大字。", "error");
      return;
    }

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "生成中...";
    setPublishMessage(resultNode, "正在截取视频画面并生成封面...");

    try {
      const response = await fetch("/api/publish/covers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: payload.task_id,
          output_clip_id: payload.output_clip_id,
          video_source: payload.video_source,
          title: payload.title,
          cover_time_seconds: payload.cover_time_seconds,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "封面生成失败");
      }
      form.elements.cover_file_path.value = data.cover_file_path || "";
      form.elements.cover_mode.value = "time";
      setCoverPreview(form, data.cover_media_url);
      setPublishMessage(resultNode, data.message || "封面已生成。", "success");
    } catch (error) {
      setPublishMessage(resultNode, `封面生成失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll("[data-publish-job-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitter = event.submitter;
    const payload = publishFormPayload(form, submitter);
    const resultNode = form.querySelector("[data-publish-job-result]");
    const originalText = submitter?.textContent || "";

    if (payload.publish_mode === "api_publish") {
      const platformLabel = form.dataset.platform === "douyin" ? "抖音" : "B站";
      const confirmed = window.confirm(`确认发布到真实${platformLabel}平台吗？\n\n请确认账号、标题、视频版本和标签都已经检查过。`);
      if (!confirmed) return;
    }

    if (submitter) {
      submitter.disabled = true;
      submitter.textContent = "处理中...";
    }
    setPublishMessage(resultNode, "正在创建发布任务...");

    try {
      const response = await fetch("/api/publish/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "创建发布任务失败");
      }
      setPublishMessage(resultNode, data.message || "发布任务已创建。", data.status === "failed" ? "error" : "success");
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      setPublishMessage(resultNode, `创建失败：${error.message}`, "error");
    } finally {
      if (submitter) {
        submitter.disabled = false;
        submitter.textContent = originalText;
      }
    }
  });
});

document.querySelectorAll("[data-publish-batch-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitter = event.submitter;
    const platform = form.dataset.platform;
    const resultNode = form.querySelector("[data-publish-batch-result]");
    const selectedIds = Array.from(document.querySelectorAll(`[data-batch-output-id='${platform}']:checked`)).map(
      (item) => item.value
    );
    const payload = publishFormPayload(form, submitter);
    payload.platform = platform;
    payload.output_clip_ids = selectedIds;
    if (!selectedIds.length) {
      setPublishMessage(resultNode, "请先勾选至少一条切片。", "error");
      return;
    }

    const originalText = submitter?.textContent || "";
    if (submitter) {
      submitter.disabled = true;
      submitter.textContent = "处理中...";
    }
    setPublishMessage(resultNode, "正在批量创建发布任务...");

    try {
      const response = await fetch("/api/publish/jobs/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "批量创建失败");
      }
      setPublishMessage(resultNode, data.message || "批量任务已创建。", "success");
      window.setTimeout(() => window.location.reload(), 800);
    } catch (error) {
      setPublishMessage(resultNode, `批量创建失败：${error.message}`, "error");
    } finally {
      if (submitter) {
        submitter.disabled = false;
        submitter.textContent = originalText;
      }
    }
  });
});

document.querySelectorAll("[data-publish-job-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    const row = button.closest("[data-publish-job-id]");
    const jobId = row?.dataset.publishJobId;
    const action = button.dataset.publishJobAction;
    const endpointMap = {
      retry: `/api/publish/jobs/${jobId}/retry`,
      "mark-published": `/api/publish/jobs/${jobId}/mark-published`,
      "mark-failed": `/api/publish/jobs/${jobId}/mark-failed`,
      cancel: `/api/publish/jobs/${jobId}/cancel`,
    };
    if (!jobId || !endpointMap[action]) return;
    if (action === "retry" && !window.confirm("确认重试真实发布吗？")) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "处理中...";

    try {
      const response = await fetch(endpointMap[action], { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "操作失败");
      }
      window.location.reload();
    } catch (error) {
      window.alert(`操作失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

const sendCenterMessage = document.querySelector("#send-center-message");

function setSendCenterMessage(message, tone = "info") {
  if (!sendCenterMessage) return;
  sendCenterMessage.hidden = false;
  sendCenterMessage.textContent = message;
  sendCenterMessage.classList.toggle("tone-red", tone === "error");
  sendCenterMessage.classList.toggle("tone-blue", tone !== "error");
}

function sendJobPayload(form) {
  const formData = new FormData(form);
  return {
    title: String(formData.get("title") || "").trim(),
    description: String(formData.get("description") || "").trim(),
    tags: String(formData.get("tags") || "").trim(),
    visibility: String(formData.get("visibility") || "public"),
    cover_file_path: String(formData.get("cover_file_path") || "").trim(),
    cover_time_seconds: Number(formData.get("cover_time_seconds") || 0),
    allow_download: Boolean(form.elements.allow_download?.checked),
    bilibili_tid: String(formData.get("bilibili_tid") || "娱乐").trim(),
    bilibili_copyright: String(formData.get("bilibili_copyright") || "original"),
    bilibili_source: String(formData.get("bilibili_source") || "").trim(),
  };
}

function reloadSendCenter(delay = 900) {
  window.setTimeout(() => window.location.reload(), delay);
}

function activeSendJobIds() {
  return Array.from(document.querySelectorAll("[data-send-job-checkbox]:checked")).map((checkbox) => checkbox.value);
}

function updateSendFilter(filter) {
  document.querySelectorAll("[data-send-card]").forEach((card) => {
    const platform = card.dataset.platform;
    const status = card.dataset.status;
    const visible = filter === "all" || filter === platform || filter === status;
    card.classList.toggle("is-hidden", !visible);
  });
}

document.querySelectorAll("[data-send-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-send-filter]").forEach((item) => item.classList.toggle("active", item === button));
    updateSendFilter(button.dataset.sendFilter || "all");
  });
});

document.querySelectorAll("[data-refresh-send-queue]").forEach((button) => {
  button.addEventListener("click", async () => {
    const useAi = button.dataset.useAi === "true";
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = useAi ? "AI 生成中..." : "刷新中...";
    setSendCenterMessage(useAi ? "正在用 AI 补齐标题、#话题和简介，并自动选择封面帧..." : "正在从已完成切片刷新发送队列，并自动选择封面帧...");

    try {
      const response = await fetch(`/api/publish/queue/refresh?use_ai=${useAi ? "true" : "false"}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "刷新队列失败");
      }
      setSendCenterMessage(data.message || "发送队列已刷新，封面帧已自动选择。", "success");
      reloadSendCenter();
    } catch (error) {
      setSendCenterMessage(`刷新失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll("[data-send-job-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const jobId = form.dataset.jobId;
    const resultNode = form.querySelector("[data-send-job-result]");
    const submitter = event.submitter;
    if (!jobId) {
      setPublishMessage(resultNode, "这条切片还没有入队，请先刷新发送队列。", "error");
      return;
    }

    const originalText = submitter?.textContent || "";
    if (submitter) {
      submitter.disabled = true;
      submitter.textContent = "保存中...";
    }
    setPublishMessage(resultNode, "正在保存发送内容...");

    try {
      const response = await fetch(`/api/publish/jobs/${jobId}/send-content`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sendJobPayload(form)),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "保存失败");
      }
      setPublishMessage(resultNode, data.message || "发送内容已保存。", "success");
    } catch (error) {
      setPublishMessage(resultNode, `保存失败：${error.message}`, "error");
    } finally {
      if (submitter) {
        submitter.disabled = false;
        submitter.textContent = originalText;
      }
    }
  });
});

function applyCoverFrame(form, frame, button = null) {
  form.elements.cover_file_path.value = frame.cover_file_path || "";
  form.elements.cover_time_seconds.value = Number(frame.cover_time_seconds || 0);
  setCoverPreview(form, frame.cover_media_url || "");
  form.querySelectorAll("[data-cover-frame-option]").forEach((item) => item.classList.toggle("active", item === button));
}

function renderCoverFrames(form, frames) {
  const list = form.querySelector("[data-cover-frame-list]");
  if (!list) return;
  list.innerHTML = "";
  frames.forEach((frame, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cover-frame-option";
    button.dataset.coverFrameOption = "true";
    button.innerHTML = `<img src="${frame.cover_media_url}" alt="候选封面 ${index + 1}"><span>${Number(frame.cover_time_seconds || 0).toFixed(1)}s</span>`;
    button.addEventListener("click", () => applyCoverFrame(form, frame, button));
    list.appendChild(button);
    if (index === 0) {
      applyCoverFrame(form, frame, button);
    }
  });
}

document.querySelectorAll("[data-generate-cover-frames]").forEach((button) => {
  button.addEventListener("click", async () => {
    const form = button.closest("[data-send-job-form]");
    if (!form) return;
    const resultNode = form.querySelector("[data-send-job-result]");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "生成中...";
    setPublishMessage(resultNode, "正在从视频里截取候选封面帧...");

    try {
      const response = await fetch("/api/publish/covers/frames", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: form.dataset.taskId,
          output_clip_id: form.dataset.outputClipId,
          video_source: "original",
          title: form.elements.title?.value || "直播切片",
          frame_count: 4,
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "候选封面生成失败");
      }
      renderCoverFrames(form, data.frames || []);
      setPublishMessage(resultNode, data.message || "候选封面已生成，已先选中第一张；需要更换可点其他帧。", "success");
    } catch (error) {
      setPublishMessage(resultNode, `封面帧生成失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll("[data-regenerate-send-metadata]").forEach((button) => {
  button.addEventListener("click", async () => {
    const form = button.closest("[data-send-job-form]");
    const jobId = form?.dataset.jobId;
    const resultNode = form?.querySelector("[data-send-job-result]");
    if (!form || !jobId) return;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "生成中...";
    setPublishMessage(resultNode, "正在重新生成标题、#话题和简介...");

    try {
      const response = await fetch(`/api/publish/jobs/${jobId}/metadata?use_ai=true`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "重新生成失败");
      }
      const job = data.job || {};
      if (form.elements.title) form.elements.title.value = job.title || form.elements.title.value;
      if (form.elements.tags) form.elements.tags.value = job.tags || "";
      if (form.elements.description) form.elements.description.value = job.description || "";
      setPublishMessage(resultNode, data.message || "AI 元数据已更新。", "success");
    } catch (error) {
      setPublishMessage(resultNode, `重新生成失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll("[data-send-single-job]").forEach((button) => {
  button.addEventListener("click", async () => {
    const form = button.closest("[data-send-job-form]");
    const jobId = form?.dataset.jobId;
    if (!jobId) return;
    if (!window.confirm("确认开始发送这一条吗？请先确认 Chrome 已登录对应平台。")) return;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "发送中...";
    setSendCenterMessage("已提交单条发送任务，opencli 会使用 Chrome 登录态打开平台页面。");

    try {
      const response = await fetch(`/api/publish/jobs/${jobId}/send`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "发送启动失败");
      }
      setSendCenterMessage(data.message || "发送任务已开始。", "success");
      reloadSendCenter(1400);
    } catch (error) {
      setSendCenterMessage(`发送启动失败：${error.message}`, "error");
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll("[data-start-send-queue]").forEach((button) => {
  button.addEventListener("click", async () => {
    const selectedIds = activeSendJobIds();
    const label = selectedIds.length ? `${selectedIds.length} 条已勾选任务` : "全部待发送/失败任务";
    if (!window.confirm(`确认开始发送 ${label} 吗？\n\n请先确认 Chrome 已登录抖音创作者中心和 B站创作中心。`)) return;
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "启动中...";
    setSendCenterMessage("正在启动发送队列，一次只会执行一条任务。");

    try {
      const response = await fetch("/api/publish/send/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: selectedIds }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || "启动队列失败");
      }
      setSendCenterMessage(data.message || "发送队列已启动。", data.status === "busy" ? "error" : "success");
      reloadSendCenter(1400);
    } catch (error) {
      setSendCenterMessage(`启动队列失败：${error.message}`, "error");
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll("[data-send-select-all]").forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    const visibleCards = Array.from(document.querySelectorAll("[data-send-card]")).filter(
      (card) => !card.classList.contains("is-hidden")
    );
    visibleCards.forEach((card) => {
      const item = card.querySelector("[data-send-job-checkbox]");
      if (item && !item.disabled) item.checked = checkbox.checked;
    });
  });
});

if (document.querySelector("[data-send-card][data-status='publishing']")) {
  window.setTimeout(() => window.location.reload(), 5000);
}
