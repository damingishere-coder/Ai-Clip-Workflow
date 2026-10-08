document.querySelectorAll("[data-sync-publish-task]").forEach((button) => {
  button.addEventListener("click", async () => {
    const taskId = button.dataset.taskId;
    if (!taskId) return;
    const originalText = button.textContent;
    const preferSubtitled = button.dataset.preferSubtitled === "true";
    button.disabled = true;
    button.textContent = "正在同步...";
    try {
      const syncReviewedClips = button.dataset.syncReviewedClips === "true";
      let data;
      if (syncReviewedClips) {
        const clips = collectClipReviewPayload();
        if (!clips.some((clip) => clip.enabled)) {
          throw new Error("请至少启用一条候选片段后再同步发送中心");
        }
        button.textContent = "正在保存并生成...";
        showClipReviewMessage("正在保存审核选择；如选择有变化，将自动生成最新切片并同步...", "info");
        data = await window.apiFetch(
          `/api/tasks/${encodeURIComponent(taskId)}/clips/sync-publish`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ clips }),
          },
        );
      } else {
        data = await window.apiFetch(
          `/api/publish/tasks/${encodeURIComponent(taskId)}/sync?prefer_subtitled=${preferSubtitled ? "true" : "false"}`,
          { method: "POST" },
        );
      }
      const summary = document.querySelector("[data-publish-link-summary]");
      if (summary) {
        const heading = document.createElement("strong");
        const detail = document.createElement("span");
        heading.textContent = `发送中心关联：${data.link_state?.label || "同步完成"}`;
        detail.textContent = data.message || "";
        summary.replaceChildren(heading, detail);
      }
      showClipReviewMessage(data.message || "发送中心同步完成。", data.status === "partial" ? "error" : "success");
      if (!document.querySelector("#process-result")) {
        window.alert(data.message || "发送中心同步完成。");
      }
      if (syncReviewedClips && data.status !== "partial") {
        savedClipReviewPayload = collectClipReviewPayload();
        updateClipReviewActionState();
        const params = new URLSearchParams({
          task_id: taskId,
          tab: "content",
          publish_message: data.message || "发送中心同步完成。",
        });
        window.location.assign(`/publish?${params.toString()}`);
      }
    } catch (error) {
      const message = `同步发送中心失败：${error.message}`;
      showClipReviewMessage(message, "error");
      if (!document.querySelector("#process-result")) window.alert(message);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});

document.querySelectorAll(".js-hide-task").forEach((button) => {
  button.addEventListener("click", async () => {
    const taskTitle = button.dataset.taskTitle || "这条任务";
    const confirmed = await window.StudioUI.confirm({title: "永久删除制作任务", confirmLabel: "永久删除", danger: true, message: `确认永久删除“${taskTitle}”吗？\n\n系统会永久删除 E 盘任务目录内的原片副本、音频、转写、切片、字幕、封面和发布包，删除后无法恢复。\n\n任务目录外的原始视频不会被删除。`});
    if (!confirmed) return;

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "删除中...";

    try {
      const response = await fetch(`/api/tasks/${button.dataset.taskId}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "永久删除失败");
      }
      const externalNotice = data.external_source_preserved ? "\n\n任务目录外的原始视频已保留。" : "";
      window.alert(`${data.message || "任务已永久删除。"}${externalNotice}`);
      window.location.reload();
    } catch (error) {
      window.alert(`永久删除失败：${error.message}`);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
});
