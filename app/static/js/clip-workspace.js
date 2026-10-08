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
const clipSelectAll = document.querySelector("[data-clip-select-all]");
const clipSelectAllLabel = document.querySelector("[data-clip-select-all-label]");
const clipSelectCount = document.querySelector("[data-clip-select-count]");
const syncReviewedClipsButton = document.querySelector("[data-sync-reviewed-clips='true']");
const cutJobProgress = document.querySelector("#cut-job-progress");
const cutJobStatus = document.querySelector("#cut-job-status");
const cutJobPercent = document.querySelector("#cut-job-percent");
const cutJobProgressBar = document.querySelector("#cut-job-progress-bar");
const cutJobMessage = document.querySelector("#cut-job-message");
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
let isCutJobActive = false;
let isClipSaveActive = false;
let isReviewHandoffActive = false;
let savedClipReviewPayload = clipReviewForm ? collectClipReviewPayload() : [];
let savedEnabledClipCount = Number(clipReviewForm?.dataset.enabledCount || 0);

const cutJobStatusLabels = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

function renderCutJobProgress(job, fallbackMessage = "") {
  if (!cutJobProgress || !job) return;
  const status = job.status || "queued";
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  cutJobProgress.hidden = false;
  cutJobProgress.dataset.status = status;
  if (cutJobStatus) {
    cutJobStatus.textContent = `${status}（${cutJobStatusLabels[status] || "处理中"}）`;
  }
  if (cutJobPercent) cutJobPercent.textContent = `${progress}%`;
  if (cutJobProgressBar) cutJobProgressBar.style.width = `${progress}%`;
  if (cutJobMessage) {
    cutJobMessage.textContent = status === "failed"
      ? (job.error_message || job.message || "切片任务失败")
      : (job.queue_hint || job.message || fallbackMessage || "正在等待切片任务更新...");
  }
}


async function waitForCutJob(jobId) {
  while (true) {
    const response = await fetch(`/api/tasks/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) {
      throw new Error(job.detail || "查询切片任务进度失败");
    }
    renderCutJobProgress(job);
    if (job.status === "completed") return job;
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error_message || job.message || "切片任务未完成");
    }
    await wait(1000);
  }
}

async function showCompletedCut(jobId) {
  const completedJob = await waitForCutJob(jobId);
  const syncMessage = completedJob.result_json?.publish_sync?.message
    ? ` ${completedJob.result_json.publish_sync.message}` : "";
  showClipReviewMessage(
    `${completedJob.result_json?.message || completedJob.message || "切片生成完成。"}${syncMessage} 正在刷新切片结果...`,
    completedJob.result_json?.publish_sync?.status === "partial" ? "error" : "success",
  );
  window.setTimeout(() => window.location.reload(), 700);
}

async function restoreCutProgress() {
  if (!generateClipsButton || !clipReviewForm) return;
  isCutJobActive = true;
  updateClipReviewActionState();
  const originalText = generateClipsButton.textContent;
  try {
    const response = await fetch(`/api/tasks/${clipReviewForm.dataset.taskId}/jobs`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "读取切片进度失败");
    const job = (data.jobs || []).find(item => item.job_type === "video_cut" && ["queued", "running"].includes(item.status));
    if (job) {
      generateClipsButton.textContent = "切片处理中...";
      renderCutJobProgress(job);
      showClipReviewMessage("切片正在后台继续，可以离开页面处理其他任务。", "info");
      await showCompletedCut(job.id);
    }
  } catch (error) {
    showClipReviewMessage(`读取切片进度失败：${error.message}`, "error");
  } finally {
    isCutJobActive = false;
    updateClipReviewActionState();
    generateClipsButton.textContent = originalText;
  }
}

function getClipReviewCards() {
  return Array.from(document.querySelectorAll("[data-clip-card]"));
}

function getClipEnableCheckboxes() {
  return getClipReviewCards()
    .map((card) => card.querySelector("[name='enabled']"))
    .filter(Boolean);
}

function updateClipSelectAllUi() {
  const checkboxes = getClipEnableCheckboxes();
  const enabledCount = checkboxes.filter((checkbox) => checkbox.checked).length;
  const allEnabled = checkboxes.length > 0 && enabledCount === checkboxes.length;
  if (clipSelectAll) {
    clipSelectAll.disabled = checkboxes.length === 0 || isCutJobActive;
    clipSelectAll.checked = allEnabled;
    clipSelectAll.indeterminate = enabledCount > 0 && !allEnabled;
  }
  if (clipSelectAllLabel) clipSelectAllLabel.textContent = allEnabled ? "取消全选" : "全选当前列表";
  if (clipSelectCount) clipSelectCount.textContent = `已启用 ${enabledCount} / ${checkboxes.length} 条`;
}

function updateClipReviewActionState() {
  const hasCards = getClipReviewCards().length > 0;
  const busy = isCutJobActive || isClipSaveActive || isReviewHandoffActive;
  const payload = clipReviewForm ? collectClipReviewPayload() : [];
  const dirty = JSON.stringify(payload) !== JSON.stringify(savedClipReviewPayload);
  const enabledCount = savedEnabledClipCount - savedClipReviewPayload.filter(item => item.enabled).length
    + payload.filter(item => item.enabled).length;
  if (saveClipsButton) saveClipsButton.disabled = !hasCards || busy;
  if (generateClipsButton) generateClipsButton.disabled = !hasCards || busy || enabledCount === 0;
  if (syncReviewedClipsButton) syncReviewedClipsButton.disabled = !hasCards || busy;
  clipReviewForm?.querySelectorAll("input, textarea, [data-delete-trigger], [data-source-monitor-trigger], [data-reject-reason]").forEach(node => { node.disabled = busy; });
  clipFilterForm?.querySelectorAll("select").forEach(node => { node.disabled = busy || dirty; });
  updateClipSelectAllUi();
  if (clipSelectAll) clipSelectAll.disabled = !hasCards || busy;
  if (clipReviewForm) {
    Object.assign(clipReviewForm.dataset, {dirty: String(dirty), busy: String(busy), enabledCount: String(enabledCount)});
    document.dispatchEvent(new CustomEvent("clip-review-state"));
  }
}

document.addEventListener("production-review-busy", event => {
  isReviewHandoffActive = event.detail.busy;
  updateClipReviewActionState();
});

function collectClipReviewPayload() {
  const cards = getClipReviewCards();
  return cards.map((card) => {
    const enabled = card.querySelector("[name='enabled']").checked;
    return {
      id: card.dataset.clipId,
      title: card.querySelector("[name='title']").value.trim(),
      start_time: card.querySelector("[name='start_time']").value.trim(),
      end_time: card.querySelector("[name='end_time']").value.trim(),
      enabled,
      summary: card.querySelector("[name='summary']").value.trim(),
      feedback_reason_code: enabled ? null : (card.dataset.feedbackReason || null),
    };
  });
}

async function persistClipReviewChanges() {
  if (!clipReviewForm) throw new Error("当前页面没有可保存的候选片段");
  const taskId = clipReviewForm.dataset.taskId;
  const payload = collectClipReviewPayload();
  const response = await fetch(`/api/tasks/${taskId}/clips/batch-update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clips: payload }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "保存失败");
  savedClipReviewPayload = payload;
  savedEnabledClipCount = data.clips.filter(item => item.enabled).length;
  updateClipReviewActionState();
  document.dispatchEvent(new CustomEvent("clip-review-saved"));
  return data;
}

async function deleteClipCard(card, button) {
  if (!clipReviewForm || !card || isCutJobActive || isClipSaveActive || isReviewHandoffActive) return;
  const confirmed = await window.StudioUI.confirm({title: "删除候选片段", message: "删除后本任务不再使用这个候选片段，原始素材保留。确认删除当前候选吗？", confirmLabel: "删除候选", danger: true});
  if (!confirmed) return;
  const taskId = clipReviewForm.dataset.taskId;
  const clipId = card.dataset.clipId;
  const originalText = button?.textContent || "";
  isClipSaveActive = true;
  updateClipReviewActionState();
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
    savedEnabledClipCount -= Number(!!savedClipReviewPayload.find(item => item.id === clipId)?.enabled);
    savedClipReviewPayload = savedClipReviewPayload.filter(item => item.id !== clipId);
    card.remove();
    updateClipReviewActionState();
    document.dispatchEvent(new CustomEvent("clip-review-saved"));
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
  } finally {
    isClipSaveActive = false;
    updateClipReviewActionState();
  }
}

function syncRejectReasonVisibility(card) {
  if (!card) return;
  const enabledInput = card.querySelector("[name='enabled']");
  const rejectFeedback = card.querySelector("[data-reject-feedback]");
  if (rejectFeedback && enabledInput) rejectFeedback.hidden = enabledInput.checked;
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
  updateClipReviewActionState();
  toggleSourceMonitor(false);
}

document.querySelectorAll("[data-clip-card] input[name='start_time'], [data-clip-card] input[name='end_time']").forEach((input) => {
  input.addEventListener("change", () => updateCardTimeDataset(input.closest("[data-clip-card]")));
});

if (clipSelectAll) {
  clipSelectAll.addEventListener("change", () => {
    const shouldEnable = clipSelectAll.checked;
    getClipEnableCheckboxes().forEach((checkbox) => {
      checkbox.checked = shouldEnable;
      syncRejectReasonVisibility(checkbox.closest("[data-clip-card]"));
    });
    updateClipReviewActionState();
    showClipReviewMessage(
      shouldEnable
        ? "已全选当前列表。确认无误后，请点击“保存修改”写入数据库。"
        : "已取消当前列表的全部选择。确认无误后，请点击“保存修改”写入数据库。",
      "info",
    );
  });
}

clipReviewForm?.addEventListener("change", (event) => {
  if (event.target.matches("[data-clip-card] input[name='enabled']")) {
    syncRejectReasonVisibility(event.target.closest("[data-clip-card]"));
    updateClipSelectAllUi();
  }
  updateClipReviewActionState();
});
clipReviewForm?.addEventListener("input", (event) => {
  // The bulk checkbox applies its selection in change; do not reset it before that event.
  if (event.target.matches("[data-clip-card] input, [data-clip-card] textarea")) updateClipReviewActionState();
});

updateClipSelectAllUi();

if (saveClipsButton && clipReviewForm) {
  saveClipsButton.addEventListener("click", async () => {
    if (isCutJobActive || isClipSaveActive || isReviewHandoffActive) return;
    const taskId = clipReviewForm.dataset.taskId;
    const originalText = saveClipsButton.textContent;
    isClipSaveActive = true;
    updateClipReviewActionState();
    saveClipsButton.textContent = "正在保存...";
    showClipReviewMessage("正在保存候选片段修改...", "info");

    try {
      const data = await persistClipReviewChanges();
      showClipReviewMessage(data.message || "保存成功。", "success");
    } catch (error) {
      showClipReviewMessage(`保存失败：${error.message}`, "error");
    } finally {
      isClipSaveActive = false;
      updateClipReviewActionState();
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
  previewTarget.scrollIntoView({ behavior: preferredScrollBehavior(), block: "start", inline: "nearest" });
}

let excerptRequest = null;
function closeTranscriptDrawer() {
  excerptRequest?.abort();
  excerptRequest = null;
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
  excerptRequest?.abort();
  const request = new AbortController();
  excerptRequest = request;
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
    const response = await fetch(`/api/tasks/${taskId}/clips/${clipId}/transcript-excerpt?${params.toString()}`, {signal: request.signal});
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "读取转写失败");
    }
    if (excerptRequest !== request || request.signal.aborted) return;
    if (clipTranscriptTitle) clipTranscriptTitle.textContent = data.title || "片段转写";
    if (clipTranscriptTime) clipTranscriptTime.textContent = `${data.start_time} - ${data.end_time}`;
    renderTranscriptRows(data.rows || []);
  } catch (error) {
    if (excerptRequest !== request || request.signal.aborted) return;
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

document.querySelectorAll("[data-reject-reason]").forEach((button) => {
  button.addEventListener("click", () => {
    const card = button.closest("[data-clip-card]");
    if (!card) return;
    const selected = card.dataset.feedbackReason === button.dataset.rejectReason;
    card.dataset.feedbackReason = selected ? "" : button.dataset.rejectReason;
    card.querySelectorAll("[data-reject-reason]").forEach((item) => {
      const active = !selected && item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", active ? "true" : "false");
    });
    showClipReviewMessage("淘汰原因已暂存；点击“保存修改”后统一写入。", "info");
    updateClipReviewActionState();
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
    if (isCutJobActive || isClipSaveActive || isReviewHandoffActive) return;
    const originalText = generateClipsButton.textContent;
    isCutJobActive = true;
    updateClipReviewActionState();
    generateClipsButton.textContent = "正在确认...";
    showClipReviewMessage("正在保存当前审核选择...", "info");

    try {
      await persistClipReviewChanges();
      generateClipsButton.textContent = "切片处理中...";
      showClipReviewMessage("审核选择已保存，正在创建切片后台任务...", "info");
      const response = await fetch(generateClipsButton.dataset.endpoint, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "生成切片请求失败");
      }
      renderCutJobProgress(data.job, data.message);
      showClipReviewMessage(data.message || "切片任务已加入后台队列。", "info");
      await showCompletedCut(data.job_id);
    } catch (error) {
      showClipReviewMessage(`生成切片失败：${error.message}`, "error");
    } finally {
      isCutJobActive = false;
      updateClipReviewActionState();
      generateClipsButton.textContent = originalText;
    }
  });
  restoreCutProgress();
}
