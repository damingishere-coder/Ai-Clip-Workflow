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
      window.location.reload();
    } catch (error) {
      if (result) result.textContent = `处理失败：${error.message}`;
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

const aiAnalysisForm = document.querySelector("#ai-analysis-form");
const saveAiPreferenceButton = document.querySelector("#save-ai-preference-button");
const aiProcessResult = document.querySelector("#ai-process-result");

async function saveTaskAiPreference() {
  if (!aiAnalysisForm) return null;
  const taskId = aiAnalysisForm.dataset.taskId;
  const formData = new FormData(aiAnalysisForm);
  const response = await fetch(`/api/tasks/${taskId}/ai-preference`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ai_preference: formData.get("ai_preference") || "",
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "AI 偏好保存失败");
  }
  return data;
}

if (saveAiPreferenceButton && aiAnalysisForm) {
  saveAiPreferenceButton.addEventListener("click", async () => {
    const originalText = saveAiPreferenceButton.textContent;
    saveAiPreferenceButton.disabled = true;
    saveAiPreferenceButton.textContent = "保存中...";
    if (aiProcessResult) aiProcessResult.textContent = "正在保存 AI 偏好...";

    try {
      const data = await saveTaskAiPreference();
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI 偏好已保存。";
    } catch (error) {
      if (aiProcessResult) aiProcessResult.textContent = `保存失败：${error.message}`;
    } finally {
      saveAiPreferenceButton.disabled = false;
      saveAiPreferenceButton.textContent = originalText;
    }
  });
}

document.querySelectorAll(".js-ai-process-action").forEach((button) => {
  button.addEventListener("click", async () => {
    const originalText = button.textContent;
    const taskId = aiAnalysisForm.dataset.taskId;
    const provider = button.dataset.provider || "remote";
    button.disabled = true;
    button.textContent = "分析中...";
    if (aiProcessResult) aiProcessResult.textContent = "正在保存偏好并启动 AI 分析...";

    try {
      await saveTaskAiPreference();
      const response = await fetch(`/api/tasks/${taskId}/process/ai?provider=${provider}`, {
        method: "POST",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "AI 分析失败");
      }
      if (aiProcessResult) aiProcessResult.textContent = data.message || "AI 分析完成，正在刷新页面...";
      window.location.reload();
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
