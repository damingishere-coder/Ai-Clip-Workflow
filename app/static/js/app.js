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
      uploadData.append("candidate_clip_count", payload.candidate_clip_count || "8");
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

  const runtimeParts = [progress.model, progress.device, progress.compute_type].filter(Boolean);
  transcriptProgressRuntime.textContent = runtimeParts.length
    ? `当前转写配置：${runtimeParts.join(" / ")}`
    : "";

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
  if (status === "running") {
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
  if (data.transcript_exists) {
    startButton.textContent = "转写已完成";
    startButton.disabled = true;
    return;
  }
  if (data.progress?.status === "running" || data.task_status === "transcribing") {
    startButton.textContent = "转写处理中";
    startButton.disabled = true;
  }
}

const aiAnalysisForm = document.querySelector("#ai-analysis-form");
const saveAiPromptsButton = document.querySelector("#save-ai-prompts-button");
const aiProcessResult = document.querySelector("#ai-process-result");
const aiAnalysisSummary = document.querySelector("#ai-analysis-summary");
const aiCandidateCountPill = document.querySelector("#ai-candidate-count-pill");

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
  const clips = Array.isArray(data.clips) ? data.clips : [];
  aiAnalysisSummary.hidden = false;
  aiAnalysisSummary.replaceChildren();

  const header = document.createElement("div");
  header.className = "ai-analysis-result-header";
  const titleWrap = document.createElement("div");
  const eyebrow = document.createElement("p");
  eyebrow.className = "eyebrow";
  eyebrow.textContent = "Analysis Result";
  const title = document.createElement("h3");
  title.textContent = "本次 AI 选取结果";
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
  provider.textContent = data.fallback_notice ? "远程失败后已自动改用本地 AI" : "已按当前 Prompt 生成候选内容";
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

    try {
      await saveTaskAiPromptSettings();
      const response = await fetch(`/api/tasks/${taskId}/process/ai?provider=${provider}`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "AI 分析失败");
      }
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI 分析完成。";
      if (aiCandidateCountPill && Array.isArray(data.clips)) {
        aiCandidateCountPill.textContent = `${data.clips.length} 条候选`;
      }
      renderAiAnalysisSummary(data);
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `AI 分析失败：${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

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
      showClipReviewMessage(data.message || "当前待视频切割模块接入。", "success");
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
