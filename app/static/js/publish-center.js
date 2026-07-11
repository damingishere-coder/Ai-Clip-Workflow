const publishCenterRoot = document.querySelector("[data-center-panel]");

if (publishCenterRoot) {
  const selectedJobIds = new Set();
  const messageNode = document.querySelector("#send-center-message");
  const selectionBar = document.querySelector("[data-selection-bar]");
  const selectedCountNode = document.querySelector("[data-selected-count]");
  const drawer = document.querySelector("[data-schedule-drawer]");
  const drawerBackdrop = document.querySelector("[data-schedule-backdrop]");
  const drawerCount = document.querySelector("[data-drawer-count]");
  const scheduleForm = document.querySelector("[data-schedule-form]");
  const previewList = document.querySelector("[data-schedule-preview]");
  const confirmScheduleButton = document.querySelector("[data-confirm-schedule]");
  const timezoneName = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  let latestPreviewSignature = "";

  function showMessage(message, tone = "info") {
    if (!messageNode) return;
    messageNode.hidden = false;
    messageNode.textContent = message;
    messageNode.classList.toggle("tone-red", tone === "error");
    messageNode.classList.toggle("tone-blue", tone !== "error");
  }

  function localDatetimeValue(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function updateSelectionUi() {
    document.querySelectorAll("[data-publish-select]").forEach((checkbox) => {
      checkbox.checked = selectedJobIds.has(checkbox.value);
    });
    const count = selectedJobIds.size;
    if (selectedCountNode) selectedCountNode.textContent = String(count);
    if (drawerCount) drawerCount.textContent = String(count);
    if (selectionBar) selectionBar.hidden = count === 0;
  }

  function selectedRows() {
    return Array.from(selectedJobIds)
      .map((jobId) => document.querySelector(`[data-publish-row][data-job-id="${CSS.escape(jobId)}"]`))
      .filter(Boolean);
  }

  function statusLabel(status) {
    return {
      WAITING: "等待安排",
      SCHEDULED: "已排期",
      PUBLISHING: "发布中",
      PUBLISHED: "已发布",
      EXPORTED: "已导出发布包",
      FAILED: "发送失败",
      CANCELLED: "已取消",
      NEED_REVIEW: "需人工复核",
    }[status] || status;
  }

  function updateRowFromJob(job) {
    if (!job?.id) return;
    document.querySelectorAll(`[data-publish-row][data-job-id="${CSS.escape(job.id)}"]`).forEach((row) => {
      const status = String(job.status || row.dataset.status || "").toUpperCase();
      row.dataset.status = status;
      const statusNode = row.querySelector("[data-row-status]");
      if (statusNode) statusNode.textContent = job.status_label || statusLabel(status);
      const titleNode = row.querySelector("[data-row-title]");
      if (titleNode && job.title) titleNode.textContent = job.title;
      const timeNode = row.querySelector("[data-row-schedule]");
      if (timeNode) {
        const utcValue = job.scheduled_at_utc || job.scheduled_at || "";
        timeNode.dataset.utc = utcValue;
        timeNode.textContent = job.scheduled_at_display || (utcValue ? new Date(utcValue).toLocaleString() : "未排期");
      }
    });
  }

  function schedulePayload(action = "apply") {
    const preset = String(scheduleForm?.elements.interval_preset?.value || "180");
    const intervalMinutes = preset === "custom"
      ? Number(scheduleForm?.elements.interval_minutes?.value || 180)
      : Number(preset);
    return {
      job_ids: Array.from(selectedJobIds),
      action,
      start_at_local: String(scheduleForm?.elements.start_at_local?.value || ""),
      timezone: timezoneName,
      interval_minutes: intervalMinutes,
      daily_start_time: String(scheduleForm?.elements.daily_start_time?.value || "09:00"),
      daily_end_time: String(scheduleForm?.elements.daily_end_time?.value || "21:00"),
    };
  }

  function previewSignature(payload) {
    return JSON.stringify(payload);
  }

  function invalidatePreview() {
    latestPreviewSignature = "";
    if (confirmScheduleButton) confirmScheduleButton.disabled = true;
  }

  function openDrawer() {
    if (!selectedJobIds.size) return;
    drawer.hidden = false;
    drawerBackdrop.hidden = false;
    document.body.classList.add("has-schedule-drawer");
    updateSelectionUi();
  }

  function closeDrawer() {
    drawer.hidden = true;
    drawerBackdrop.hidden = true;
    document.body.classList.remove("has-schedule-drawer");
  }

  document.querySelectorAll("[data-center-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.centerTab;
      document.querySelectorAll("[data-center-tab]").forEach((item) => item.classList.toggle("active", item === button));
      document.querySelectorAll("[data-center-panel]").forEach((panel) => {
        const active = panel.dataset.centerPanel === tab;
        panel.hidden = !active;
        panel.classList.toggle("active", active);
      });
    });
  });

  document.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-publish-select]");
    if (checkbox) {
      if (checkbox.checked) selectedJobIds.add(checkbox.value);
      else selectedJobIds.delete(checkbox.value);
      updateSelectionUi();
    }
    if (event.target.closest("[data-schedule-form]")) invalidatePreview();
  });

  document.addEventListener("click", async (event) => {
    const toggleEditor = event.target.closest("[data-toggle-publish-editor]");
    if (toggleEditor) {
      const editor = toggleEditor.closest("[data-publish-row]")?.querySelector("[data-publish-editor]");
      if (editor) {
        editor.hidden = !editor.hidden;
        toggleEditor.textContent = editor.hidden ? "展开编辑" : "收起编辑";
      }
      return;
    }

    const publishNowButton = event.target.closest("[data-publish-now]");
    if (publishNowButton) {
      const row = publishNowButton.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      if (!jobId) return;
      publishNowButton.disabled = true;
      publishNowButton.textContent = "发送中…";
      try {
        const result = await window.apiFetch(`/api/publish/jobs/${jobId}/publish-now`, { method: "POST" });
        updateRowFromJob(result.job || { id: jobId, status: result.status?.toUpperCase() });
        selectedJobIds.delete(jobId);
        updateSelectionUi();
        showMessage(result.message || "单条任务已执行。", result.status === "failed" ? "error" : "success");
      } catch (error) {
        showMessage(`立即发送失败：${error.message}`, "error");
      } finally {
        publishNowButton.disabled = false;
        publishNowButton.textContent = "立即发送";
      }
      return;
    }

    const clearScheduleButton = event.target.closest("[data-clear-schedule]");
    if (clearScheduleButton) {
      const row = clearScheduleButton.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      if (!jobId) return;
      try {
        const data = await window.apiFetch("/api/publish/jobs/schedule-batch", {
          method: "PATCH",
          body: JSON.stringify({ job_ids: [jobId], action: "clear", timezone: timezoneName }),
        });
        updateRowFromJob(data.jobs?.[0]);
        row.querySelector("[data-row-schedule]").textContent = "未排期";
        showMessage("已取消排期，任务回到等待安排状态。", "success");
      } catch (error) {
        showMessage(`取消排期失败：${error.message}`, "error");
      }
    }
  });

  document.querySelectorAll("[data-publish-editor]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const row = form.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      const resultNode = form.querySelector("[data-editor-result]");
      const payload = {
        title: String(form.elements.title.value || "").trim(),
        description: String(form.elements.description.value || "").trim(),
        tags: String(form.elements.tags.value || "").trim(),
        visibility: String(form.elements.visibility.value || "public"),
        cover_file_path: String(form.elements.cover_file_path.value || ""),
        cover_time_seconds: Number(form.elements.cover_time_seconds.value || 0),
        allow_download: Boolean(form.elements.allow_download.checked),
        bilibili_tid: String(form.elements.bilibili_tid.value || "娱乐"),
        bilibili_copyright: String(form.elements.bilibili_copyright.value || "original"),
        bilibili_source: String(form.elements.bilibili_source.value || ""),
      };
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/send-content`, { method: "PATCH", body: JSON.stringify(payload) });
        updateRowFromJob(data.job);
        if (resultNode) resultNode.textContent = "已保存";
      } catch (error) {
        if (resultNode) resultNode.textContent = `保存失败：${error.message}`;
      }
    });
  });

  document.querySelector("[data-open-schedule-drawer]")?.addEventListener("click", openDrawer);
  document.querySelector("[data-close-schedule-drawer]")?.addEventListener("click", closeDrawer);
  drawerBackdrop?.addEventListener("click", closeDrawer);
  document.querySelector("[data-clear-selection]")?.addEventListener("click", () => {
    selectedJobIds.clear();
    updateSelectionUi();
  });
  document.querySelector("[data-expand-selected]")?.addEventListener("click", () => {
    selectedRows().forEach((row) => {
      const editor = row.querySelector("[data-publish-editor]");
      if (editor) editor.hidden = false;
    });
  });

  document.querySelector("[data-send-selected]")?.addEventListener("click", async () => {
    const ids = Array.from(selectedJobIds);
    for (const jobId of ids) {
      try {
        const result = await window.apiFetch(`/api/publish/jobs/${jobId}/publish-now`, { method: "POST" });
        updateRowFromJob(result.job || { id: jobId, status: result.status?.toUpperCase() });
      } catch (error) {
        showMessage(`任务 ${jobId} 发送失败：${error.message}`, "error");
        break;
      }
    }
    selectedJobIds.clear();
    updateSelectionUi();
  });

  scheduleForm?.elements.interval_preset?.addEventListener("change", () => {
    const custom = scheduleForm.elements.interval_preset.value === "custom";
    document.querySelector("[data-custom-interval]").hidden = !custom;
  });

  document.querySelector("[data-preview-schedule]")?.addEventListener("click", async () => {
    const payload = schedulePayload("apply");
    if (!payload.start_at_local) {
      showMessage("请先选择起始时间。", "error");
      return;
    }
    try {
      const data = await window.apiFetch("/api/publish/schedules/preview", { method: "POST", body: JSON.stringify(payload) });
      previewList.innerHTML = "";
      (data.schedule || []).forEach((item) => {
        const row = document.querySelector(`[data-publish-row][data-job-id="${CSS.escape(item.job_id)}"]`);
        const line = document.createElement("div");
        line.innerHTML = `<strong>${row?.querySelector("[data-row-title]")?.textContent || item.job_id}</strong><time>${item.scheduled_at_local_display}</time>`;
        previewList.appendChild(line);
      });
      latestPreviewSignature = previewSignature(payload);
      confirmScheduleButton.disabled = false;
    } catch (error) {
      showMessage(`排期预览失败：${error.message}`, "error");
    }
  });

  scheduleForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = schedulePayload("apply");
    if (latestPreviewSignature !== previewSignature(payload)) {
      showMessage("排期参数已变化，请重新预览后再确认。", "error");
      return;
    }
    try {
      const data = await window.apiFetch("/api/publish/jobs/schedule-batch", { method: "PATCH", body: JSON.stringify(payload) });
      (data.jobs || []).forEach(updateRowFromJob);
      (data.schedule || []).forEach((item) => {
        const row = document.querySelector(`[data-publish-row][data-job-id="${CSS.escape(item.job_id)}"]`);
        const timeNode = row?.querySelector("[data-row-schedule]");
        if (timeNode) timeNode.textContent = item.scheduled_at_local_display;
      });
      showMessage(data.message || "排期已保存。", "success");
      selectedJobIds.clear();
      updateSelectionUi();
      closeDrawer();
    } catch (error) {
      showMessage(`排期保存失败：${error.message}`, "error");
    }
  });

  document.querySelector("[data-supplement-publish-jobs]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const data = await window.apiFetch("/api/publish/queue/refresh?use_ai=false", { method: "POST" });
      showMessage(data.message || "缺失任务已补充。", "success");
    } catch (error) {
      showMessage(`补充任务失败：${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  });

  if (scheduleForm?.elements.start_at_local) {
    scheduleForm.elements.start_at_local.value = localDatetimeValue(new Date(Date.now() + 10 * 60 * 1000));
  }
  document.querySelector("[data-current-timezone]").textContent = timezoneName;
  updateSelectionUi();
}
