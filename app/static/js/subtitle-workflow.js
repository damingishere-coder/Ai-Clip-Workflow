const subtitleStyleForm = document.querySelector("#subtitle-style-form");
const subtitleStyleResult = document.querySelector("#subtitle-style-result");

if (subtitleStyleForm) {
  subtitleStyleForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submitButton = subtitleStyleForm.querySelector("button[type='submit']");
    const formData = new FormData(subtitleStyleForm);
    const payload = Object.fromEntries(formData.entries());
    payload.font_size = Number(payload.font_size || 42);
    payload.outline_width = Number(payload.outline_width || 3);
    payload.shadow_depth = Number(payload.shadow_depth || 1);
    payload.safe_area_percent = Number(payload.safe_area_percent || 5);
    payload.speaker_styles = {
      主播: { font_color: payload.speaker_host_color || "#ffffff" },
      嘉宾: { font_color: payload.speaker_guest_color || "#ffd60a" },
    };
    delete payload.speaker_host_color;
    delete payload.speaker_guest_color;
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
    button.textContent = "正在入队...";
    if (statusNode) statusNode.textContent = "字幕排队中";
    if (errorNode) errorNode.textContent = "";

    try {
      const response = await fetch(`/api/tasks/${taskId}/output-clips/${outputId}/subtitles`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "自动加字幕失败");
      }
      if (errorNode) errorNode.textContent = data.message || "字幕任务已加入队列。";
      button.textContent = "后台烧录中";
      await pollSubtitleWorkflowJob(data.job_id, statusNode, errorNode);
      window.location.reload();
    } catch (error) {
      if (statusNode) statusNode.textContent = "字幕失败";
      if (errorNode) errorNode.textContent = `自动加字幕失败：${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

async function pollSubtitleWorkflowJob(jobId, statusNode, messageNode) {
  if (!jobId) throw new Error("后台没有返回字幕 job id");
  while (true) {
    const response = await fetch(`/api/tasks/jobs/${encodeURIComponent(jobId)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || "读取字幕任务状态失败");
    if (statusNode) statusNode.textContent = `${job.status_label || job.status} · ${Number(job.progress || 0)}%`;
    if (messageNode) messageNode.textContent = job.message || "字幕任务处理中";
    if (job.status === "completed") return job;
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error_message || job.message || "字幕任务未完成");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1500));
  }
}

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
