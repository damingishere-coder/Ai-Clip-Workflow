// Shared helpers are initialized by base.html, independently of this bundle.

const newTaskForm = document.querySelector("#new-task-form");
const newTaskAutoMode = newTaskForm?.querySelector("input[name='auto_mode']");
const newTaskSubmitButton = document.querySelector("#new-task-submit-button");
const selectionProfileInput = document.querySelector("#selection-profile");
const longLiveSettings = document.querySelector("#long-live-settings");

function updateNewTaskSubmitLabel() {
  if (!newTaskSubmitButton) return;
  newTaskSubmitButton.textContent = newTaskAutoMode?.checked ? "创建并自动处理" : "创建任务";
}

if (newTaskAutoMode) {
  newTaskAutoMode.addEventListener("change", updateNewTaskSubmitLabel);
  updateNewTaskSubmitLabel();
}

if (selectionProfileInput && longLiveSettings) {
  const profiles = JSON.parse(document.querySelector("#content-profile-options")?.textContent || "[]");
  const updateLongLiveSettings = () => {
    longLiveSettings.hidden = selectionProfileInput.value !== "long_live_talk";
    const profile = profiles.find(item => item.id === selectionProfileInput.value);
    if (!profile) return;
    const promptInput = document.querySelector("#new-task-prompt");
    if (promptInput) promptInput.value = newTaskForm.dataset.trialPreset || profile.prompt_preset_id;
    const durationInput = newTaskForm.querySelector('[name="max_clip_duration"]');
    const poolInput = newTaskForm.querySelector('[name="candidate_clip_count"]');
    const targetInput = newTaskForm.querySelector('[name="final_clip_target"]');
    const isContent = profile.analyzer_key === "content";
    durationInput.max = isContent ? String(Math.floor(profile.duration.max_seconds / 60)) : "60";
    durationInput.value = isContent ? durationInput.max : "10";
    for (const option of poolInput.options) option.disabled = Number(option.value) > profile.selection.candidate_pool_max;
    poolInput.value = String(profile.selection.candidate_pool_default);
    if (!poolInput.value) poolInput.value = "12";
    targetInput.value = String(Math.min(12, profile.selection.final_target_default));
    const trialNotice = ["interview_story", "knowledge_opinion"].includes(profile.id)
      ? "试用模板：真实内容质量待日常使用验证，请审核后再使用成片。" : "";
    document.querySelector("#selection-profile-hint").textContent = `${trialNotice}${profile.description} 推荐场景：${profile.recommended_scenes.join("、")}。`;
    const lo = profile.duration.recommended_min_seconds;
    const hi = profile.duration.recommended_max_seconds;
    document.querySelector("#profile-duration-hint").textContent = lo
      ? `推荐 ${lo}–${hi} 秒；模板硬边界 ${profile.duration.min_seconds}–${profile.duration.max_seconds} 秒。`
      : "通用模式按任务时长上限选片，保留完整表达。";
  };
  selectionProfileInput.addEventListener("change", updateLongLiveSettings);
  updateLongLiveSettings();
}

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
      if (!payload.selection_profile) throw new Error("请选择选片模式");
      if (!videoFileInput.files.length) throw new Error("请选择要上传的视频文件");
      const uploadData = new FormData();
      for (const key of [
        "task_name", "platform", "max_clip_duration", "candidate_clip_count",
        "selection_profile", "final_clip_target", "ai_prompt_preset_id", "ai_provider",
        "subtitle_strategy",
      ]) uploadData.append(key, payload[key] || "");
      if (payload.challenger_id) {
        uploadData.append("challenger_id", payload.challenger_id);
        uploadData.append("challenger_sha256", payload.challenger_sha256 || "");
        uploadData.append("confirm_challenger", payload.confirm_challenger === "true" ? "true" : "false");
      }
      if (payload.selection_profile === "long_live_talk") {
        uploadData.append("highlight_density_per_hour", payload.highlight_density_per_hour || "4");
        uploadData.append("highlight_total_limit", payload.highlight_total_limit || "30");
      }
      uploadData.append("ai_preference", "");
      uploadData.append("auto_mode", payload.auto_mode === "true" ? "true" : "false");
      uploadData.append("visual_enabled", payload.visual_enabled === "true" ? "true" : "false");
      uploadData.append("auto_metadata_use_ai", "false");
      uploadData.append("video_file", videoFileInput.files[0]);
      const data = await apiFetch("/api/tasks/upload", { method: "POST", body: uploadData });
      result.textContent = `${data.message}${payload.auto_mode === "true" ? " 全自动流水线已启动。" : ""} 正在进入详情页...`;
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
