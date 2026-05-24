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
      uploadData.append("max_clip_duration", payload.max_clip_duration || "2");
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

document.querySelectorAll(".js-process-action").forEach((button) => {
  button.addEventListener("click", async () => {
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
  });
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
  const transcriptStartButtons = document.querySelectorAll(".js-transcript-start");
  if (!startButton && !transcriptStartButtons.length) return;
  if (data.transcript_exists) {
    if (startButton) {
      startButton.textContent = "转写已完成";
      startButton.disabled = true;
    }
    return;
  }
  if (data.progress?.status === "running" || data.task_status === "transcribing") {
    if (startButton) {
      startButton.textContent = "转写处理中";
      startButton.disabled = true;
    }
    transcriptStartButtons.forEach((button) => {
      button.disabled = true;
    });
    updateCancelTranscriptButtons(data.progress?.status || "running");
    return;
  }
  if (data.progress?.status === "cancelled") {
    transcriptStartButtons.forEach((button) => {
      button.disabled = false;
    });
    updateCancelTranscriptButtons("cancelled");
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
  reviewLink.textContent = "转到片段审核";
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
const clipPreviewCaption = document.querySelector("#clip-preview-caption");
const clipTranscriptDrawer = document.querySelector("#clip-transcript-drawer");
const clipTranscriptTitle = document.querySelector("#clip-transcript-title");
const clipTranscriptTime = document.querySelector("#clip-transcript-time");
const clipTranscriptBody = document.querySelector("#clip-transcript-body");
const closeTranscriptDrawerButton = document.querySelector("#close-transcript-drawer");
let activePreviewEndSeconds = null;

function showClipReviewMessage(message, tone = "info") {
  if (!clipReviewMessage) return;
  clipReviewMessage.hidden = false;
  clipReviewMessage.textContent = message;
  clipReviewMessage.dataset.tone = tone;
}

function collectClipReviewPayload() {
  const cards = Array.from(document.querySelectorAll("[data-clip-card]"));
  return cards.map((card) => ({
    id: card.dataset.clipId,
    title: card.querySelector("[name='title']").value.trim(),
    start_time: card.querySelector("[name='start_time']").value.trim(),
    end_time: card.querySelector("[name='end_time']").value.trim(),
    enabled: card.querySelector("[name='enabled']").checked,
    summary: card.querySelector("[name='summary']").value.trim(),
  }));
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

function updateCardTimeDataset(card) {
  if (!card) return;
  card.dataset.startSeconds = String(timeTextToSeconds(card.querySelector("[name='start_time']")?.value));
  card.dataset.endSeconds = String(timeTextToSeconds(card.querySelector("[name='end_time']")?.value));
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
    const response = await fetch(`/api/tasks/${taskId}/clips/${clipId}/transcript-excerpt`);
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
    const confirmed = window.confirm(`确认隐藏“${taskTitle}”吗？\n\n这只会从列表隐藏任务，不会删除原视频、切片文件和任务目录。`);
    if (!confirmed) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "隐藏中...";

    try {
      const response = await fetch(`/api/tasks/${button.dataset.taskId}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "隐藏失败");
      }
      window.alert(data.message || "任务已隐藏。");
      window.location.reload();
    } catch (error) {
      window.alert(`隐藏失败：${error.message}`);
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
const trimWindow = document.querySelector("#trim-window");

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
}

document.querySelectorAll("[data-cut-edit-trigger]").forEach((button) => {
  button.addEventListener("click", () => {
    const card = button.closest("[data-subtitle-output-card]");
    const sourceVideo = card?.querySelector(":scope > video");
    if (!card || !cutEditVideo || !sourceVideo) return;
    cutEditVideo.src = sourceVideo.currentSrc || sourceVideo.src;
    if (cutEditCaption) {
      cutEditCaption.textContent = card.querySelector(".subtitle-output-body strong")?.textContent || "当前切片";
    }
    toggleCutEditModal(true);
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
    cutEditVideo.currentTime = Math.max(0, cutEditVideo.currentTime || 0);
    cutEditVideo.play().catch(() => {});
  });
}

if (trimWindow) {
  let dragging = false;
  let startX = 0;
  let startLeft = 18;
  trimWindow.addEventListener("pointerdown", (event) => {
    dragging = true;
    startX = event.clientX;
    startLeft = parseFloat(trimWindow.style.left || "18");
    trimWindow.setPointerCapture(event.pointerId);
  });
  trimWindow.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const parentWidth = trimWindow.parentElement?.clientWidth || 1;
    const deltaPercent = ((event.clientX - startX) / parentWidth) * 100;
    const nextLeft = Math.min(48, Math.max(4, startLeft + deltaPercent));
    trimWindow.style.left = `${nextLeft}%`;
    if (cutEditVideo?.duration) {
      cutEditVideo.currentTime = Math.max(0, (nextLeft / 100) * cutEditVideo.duration);
    }
  });
  trimWindow.addEventListener("pointerup", () => {
    dragging = false;
  });
}

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
