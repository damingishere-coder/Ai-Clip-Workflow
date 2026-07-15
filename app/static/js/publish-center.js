const publishCenterRoot = document.querySelector("[data-center-panel]");

if (publishCenterRoot) {
  const APP_TIMEZONE = "Asia/Shanghai";
  const selectedJobIds = new Set();
  const messageNode = document.querySelector("#send-center-message");
  const selectionBar = document.querySelector("[data-selection-bar]");
  const selectedCountNode = document.querySelector("[data-selected-count]");
  const drawer = document.querySelector("[data-schedule-drawer]");
  const drawerBackdrop = document.querySelector("[data-schedule-backdrop]");
  const accountDrawer = document.querySelector("[data-account-drawer]");
  const accountBackdrop = document.querySelector("[data-account-backdrop]");
  const drawerCount = document.querySelector("[data-drawer-count]");
  const scheduleForm = document.querySelector("[data-schedule-form]");
  const previewList = document.querySelector("[data-schedule-preview]");
  const confirmScheduleButton = document.querySelector("[data-confirm-schedule]");
  const historyFilter = document.querySelector("[data-history-filter]");
  let latestPreviewSignature = "";
  let latestPreviewItems = [];

  function showMessage(message, tone = "info") {
    if (!messageNode) return;
    messageNode.hidden = false;
    messageNode.textContent = message;
    messageNode.classList.toggle("tone-red", tone === "error");
    messageNode.classList.toggle("tone-blue", tone !== "error");
  }

  function beijingDatetimeValue(timestamp) {
    return new Date(timestamp + 8 * 60 * 60 * 1000).toISOString().slice(0, 16);
  }

  function beijingInputToTimestamp(value) {
    return Date.parse(`${value}:00+08:00`);
  }

  function formatBeijingTimestamp(value) {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: APP_TIMEZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(value));
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}`;
  }

  function statusLabel(status) {
    return {
      DRAFT: "草稿", WAITING: "等待安排", SCHEDULED: "已排期", PUBLISHING: "发送中",
      PUBLISHED: "已发布", EXPORTED: "已导出发布包", FAILED: "发送失败",
      CANCELLED: "已取消", NEED_REVIEW: "需人工复核",
    }[status] || status;
  }

  function accountStatusLabel(status) {
    return status === "normal" ? "正常" : (status === "invalid" ? "登录失效" : "需要重新登录");
  }

  function appendAccountRow(account) {
    const list = document.querySelector("[data-account-list]");
    if (!list || list.querySelector(`[data-account-id="${CSS.escape(account.id)}"]`)) return;
    const article = document.createElement("article");
    article.dataset.accountRow = "";
    article.dataset.accountId = account.id;
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = account.account_name;
    const platform = document.createElement("small");
    platform.textContent = account.platform_label;
    identity.append(name, platform);
    const status = document.createElement("span");
    status.className = "status-pill tone-amber";
    status.dataset.accountStatus = "";
    status.textContent = accountStatusLabel(account.login_status);
    const actions = document.createElement("div");
    actions.className = "button-row";
    const login = document.createElement("button");
    login.type = "button"; login.className = "secondary-button"; login.dataset.accountLogin = ""; login.textContent = "登录 / 重新登录";
    const check = document.createElement("button");
    check.type = "button"; check.className = "text-button"; check.dataset.accountCheck = ""; check.textContent = "检查状态";
    actions.append(login, check);
    article.append(identity, status, actions);
    list.append(article);
  }

  function sectionAllows(section, status) {
    if (section === "content") return ["DRAFT", "WAITING", "SCHEDULED"].includes(status);
    if (section === "schedule") return ["WAITING", "SCHEDULED"].includes(status);
    if (section === "history") return !["DRAFT", "WAITING", "SCHEDULED"].includes(status);
    return true;
  }

  function applyHistoryFilter() {
    const filter = String(historyFilter?.value || "all");
    document.querySelectorAll('[data-publish-row][data-section="history"]').forEach((row) => {
      const allowed = sectionAllows("history", row.dataset.status || "");
      row.hidden = !allowed || (filter !== "all" && row.dataset.status !== filter);
    });
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

  function switchTab(tab) {
    document.querySelectorAll("[data-center-tab]").forEach((button) => {
      button.classList.toggle("active", button.dataset.centerTab === tab);
    });
    document.querySelectorAll("[data-center-panel]").forEach((panel) => {
      const active = panel.dataset.centerPanel === tab;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
  }

  function updateRowFromJob(job) {
    if (!job?.id) return;
    document.querySelectorAll(`[data-publish-row][data-job-id="${CSS.escape(job.id)}"]`).forEach((row) => {
      const status = String(job.status || row.dataset.status || "").toUpperCase();
      row.dataset.status = status;
      if (job.account_id !== undefined) row.dataset.accountId = job.account_id || "";
      row.hidden = !sectionAllows(row.dataset.section, status);
      const statusNode = row.querySelector("[data-row-status]");
      if (statusNode) statusNode.textContent = job.status_label || statusLabel(status);
      const titleNode = row.querySelector("[data-row-title]");
      if (titleNode && job.title) titleNode.textContent = job.title;
      const platformNode = row.querySelector("[data-row-platform]");
      if (platformNode && job.platform_label) platformNode.textContent = job.platform_label;
      const accountNode = row.querySelector("[data-row-account]");
      if (accountNode && job.account_name !== undefined) accountNode.textContent = job.account_name || "未选择";
      const readyNode = row.querySelector("[data-content-ready]");
      if (readyNode && job.content_complete !== undefined) {
        readyNode.textContent = job.content_complete ? "内容完整" : `缺少：${(job.missing_fields || []).join("、")}`;
        readyNode.classList.toggle("tone-green", Boolean(job.content_complete));
        readyNode.classList.toggle("tone-amber", !job.content_complete);
      }
      const timeNode = row.querySelector("[data-row-schedule]");
      if (timeNode) {
        const utcValue = job.scheduled_at_utc || job.scheduled_at || "";
        timeNode.dataset.utc = utcValue;
        timeNode.textContent = job.scheduled_at_display || (utcValue ? formatBeijingTimestamp(utcValue) : "未排期");
      }
    });
    if (["PUBLISHED", "EXPORTED", "CANCELLED", "PUBLISHING", "NEED_REVIEW", "FAILED"].includes(String(job.status || ""))) {
      selectedJobIds.delete(job.id);
      updateSelectionUi();
    }
    applyHistoryFilter();
  }

  function cloneRowsForRetry(sourceId, job) {
    document.querySelectorAll(`[data-publish-row][data-job-id="${CSS.escape(sourceId)}"]`).forEach((source) => {
      const clone = source.cloneNode(true);
      clone.dataset.jobId = job.id;
      clone.querySelectorAll("[data-publish-select]").forEach((checkbox) => { checkbox.value = job.id; checkbox.checked = false; });
      source.parentElement.appendChild(clone);
    });
    updateRowFromJob(job);
  }

  function schedulePayload(action = "apply") {
    const preset = String(scheduleForm?.elements.interval_preset?.value || "180");
    return {
      job_ids: Array.from(selectedJobIds),
      action,
      start_at_local: String(scheduleForm?.elements.start_at_local?.value || ""),
      timezone: APP_TIMEZONE,
      interval_minutes: preset === "custom" ? Number(scheduleForm?.elements.interval_minutes?.value || 180) : Number(preset),
      daily_start_time: String(scheduleForm?.elements.daily_start_time?.value || "09:00"),
      daily_end_time: String(scheduleForm?.elements.daily_end_time?.value || "21:00"),
    };
  }

  function previewSignature(payload) {
    return JSON.stringify({ ...payload, confirmed_schedule: undefined });
  }

  function invalidatePreview() {
    latestPreviewSignature = "";
    latestPreviewItems = [];
    if (confirmScheduleButton) confirmScheduleButton.disabled = true;
  }

  function openDrawer() {
    if (!selectedJobIds.size) {
      showMessage("请先选择至少一条任务。", "error");
      return;
    }
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

  function filterAccountOptions(select, platform) {
    if (!select) return;
    Array.from(select.options).forEach((option) => {
      const optionPlatform = option.dataset.accountPlatform || "";
      option.hidden = Boolean(optionPlatform && optionPlatform !== platform);
      option.disabled = Boolean(optionPlatform && optionPlatform !== platform);
    });
    const chosen = select.selectedOptions[0];
    if (chosen?.disabled) select.value = "";
  }

  function syncPlatformFields(form) {
    if (!form) return;
    const platform = String(form.elements.platform?.value || "douyin");
    const bilibiliFields = form.querySelector("[data-bilibili-fields]");
    if (bilibiliFields) bilibiliFields.hidden = platform !== "bilibili";
    filterAccountOptions(form.elements.account_id, platform);
    const repost = String(form.elements.bilibili_copyright?.value || "original") === "repost";
    const source = form.querySelector("[data-repost-source]");
    if (source) source.hidden = platform !== "bilibili" || !repost;
  }

  async function refreshJobs() {
    try {
      const data = await window.apiFetch("/api/publish/jobs");
      (data.jobs || []).forEach(updateRowFromJob);
    } catch (_error) {
      // 后台轮询失败不遮挡用户正在编辑的内容。
    }
  }

  document.querySelectorAll("[data-center-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.centerTab));
  });
  historyFilter?.addEventListener("change", applyHistoryFilter);

  document.addEventListener("change", (event) => {
    const checkbox = event.target.closest("[data-publish-select]");
    if (checkbox) {
      if (checkbox.checked) selectedJobIds.add(checkbox.value); else selectedJobIds.delete(checkbox.value);
      updateSelectionUi();
    }
    const form = event.target.closest("[data-publish-editor]");
    if (form && (event.target.matches("[data-platform-select]") || event.target.matches("[data-copyright-select]"))) syncPlatformFields(form);
    if (event.target.closest("[data-schedule-form]")) invalidatePreview();
    if (event.target.matches("[data-batch-platform]")) filterAccountOptions(document.querySelector("[data-batch-account]"), event.target.value);
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-publish-editor]");
    if (form) {
      event.preventDefault();
      const row = form.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      const resultNode = form.querySelector("[data-editor-result]");
      const platform = String(form.elements.platform.value || "douyin");
      const publishMode = String(form.elements.publish_mode.value || "local_browser");
      const target = { platform, account_id: String(form.elements.account_id.value || ""), publish_mode: publishMode };
      const content = {
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
        await window.apiFetch(`/api/publish/jobs/${jobId}/target`, { method: "PATCH", body: JSON.stringify(target) });
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/send-content`, { method: "PATCH", body: JSON.stringify(content) });
        updateRowFromJob(data.job);
        if (resultNode) resultNode.textContent = "已保存";
      } catch (error) {
        if (resultNode) resultNode.textContent = `保存失败：${error.message}`;
      }
      return;
    }

    const accountForm = event.target.closest("[data-account-create]");
    if (accountForm) {
      event.preventDefault();
      const resultNode = accountForm.querySelector("[data-account-form-result]");
      try {
        const data = await window.apiFetch("/api/publish/accounts", {
          method: "POST",
          body: JSON.stringify({ platform: accountForm.elements.platform.value, account_name: accountForm.elements.account_name.value }),
        });
        const account = data.account;
        document.querySelectorAll("[data-account-select], [data-batch-account]").forEach((select) => {
          const option = new Option(`${account.account_name} · ${account.platform_label} · 需登录`, account.id);
          option.dataset.accountPlatform = account.platform;
          select.add(option);
        });
        appendAccountRow(account);
        accountForm.reset();
        if (resultNode) resultNode.textContent = "账号已保存，请点击登录 / 重新登录。";
        showMessage("账号记录已保存；系统没有保存账号密码。", "success");
      } catch (error) {
        if (resultNode) resultNode.textContent = `保存失败：${error.message}`;
      }
    }
  });

  document.addEventListener("click", async (event) => {
    const publishNowButton = event.target.closest("[data-publish-now]");
    if (publishNowButton) {
      const jobId = publishNowButton.closest("[data-publish-row]")?.dataset.jobId;
      if (!jobId || !window.confirm("确认立即发送？任务会先进入 SCHEDULED，再由统一调度器执行真实投稿。")) return;
      publishNowButton.disabled = true;
      try {
        const result = await window.apiFetch(`/api/publish/jobs/${jobId}/publish-now`, { method: "POST" });
        updateRowFromJob(result.job || { id: jobId, status: "SCHEDULED" });
        showMessage("任务已按当前北京时间加入统一调度器。", "success");
      } catch (error) {
        showMessage(`立即发送失败：${error.message}`, "error");
      } finally { publishNowButton.disabled = false; }
      return;
    }

    const clearButton = event.target.closest("[data-clear-schedule]");
    if (clearButton) {
      const jobId = clearButton.closest("[data-publish-row]")?.dataset.jobId;
      try {
        const data = await window.apiFetch("/api/publish/jobs/schedule-batch", { method: "PATCH", body: JSON.stringify({ job_ids: [jobId], action: "clear", timezone: APP_TIMEZONE }) });
        (data.jobs || []).forEach(updateRowFromJob);
        showMessage(data.message, "success");
      } catch (error) { showMessage(`清除排期失败：${error.message}`, "error"); }
      return;
    }

    const cancelButton = event.target.closest("[data-cancel-job]");
    if (cancelButton) {
      const jobId = cancelButton.closest("[data-publish-row]")?.dataset.jobId;
      if (!window.confirm("确认取消这条发布任务？")) return;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/cancel`, { method: "POST" });
        updateRowFromJob(data.job);
      } catch (error) { showMessage(`取消失败：${error.message}`, "error"); }
      return;
    }

    const metadataButton = event.target.closest("[data-generate-metadata]");
    if (metadataButton) {
      const row = metadataButton.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      metadataButton.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/metadata?use_ai=true`, { method: "POST" });
        const form = row.querySelector("[data-publish-editor]");
        form.elements.title.value = data.job.title || "";
        form.elements.description.value = data.job.description || "";
        form.elements.tags.value = data.job.tags || "";
        updateRowFromJob(data.job);
      } catch (error) { showMessage(`AI 补齐失败：${error.message}`, "error"); }
      finally { metadataButton.disabled = false; }
      return;
    }

    const coverButton = event.target.closest("[data-generate-cover]");
    if (coverButton) {
      const row = coverButton.closest("[data-publish-row]");
      const form = row?.querySelector("[data-publish-editor]");
      const current = Number(form?.elements.cover_time_seconds?.value || 0);
      const rawSeconds = window.prompt("请输入要作为封面的画面秒数（例如 3.5）：", String(current));
      if (rawSeconds === null) return;
      const seconds = Number(rawSeconds);
      if (!Number.isFinite(seconds) || seconds < 0) { showMessage("封面秒数必须是大于或等于 0 的数字。", "error"); return; }
      coverButton.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${row.dataset.jobId}/cover`, {
          method: "POST",
          body: JSON.stringify({
            task_id: coverButton.dataset.taskId,
            output_clip_id: coverButton.dataset.clipId,
            video_source: coverButton.dataset.videoSource || "original",
            title: String(form.elements.title.value || "发布封面"),
            cover_time_seconds: seconds,
          }),
        });
        form.elements.cover_file_path.value = data.cover_file_path || "";
        form.elements.cover_time_seconds.value = String(seconds);
        const preview = row.querySelector("[data-cover-preview]");
        if (preview && data.cover_media_url) { preview.src = data.cover_media_url; preview.hidden = false; }
        updateRowFromJob(data.job);
        showMessage(data.message || "封面已生成。", "success");
      } catch (error) { showMessage(`封面生成失败：${error.message}`, "error"); }
      finally { coverButton.disabled = false; }
      return;
    }

    const addPlan = event.target.closest("[data-add-to-plan]");
    if (addPlan) {
      const jobId = addPlan.closest("[data-publish-row]")?.dataset.jobId;
      selectedJobIds.add(jobId);
      updateSelectionUi();
      switchTab("schedule");
      openDrawer();
      return;
    }

    const retryButton = event.target.closest("[data-retry-job]");
    if (retryButton) {
      const sourceId = retryButton.closest("[data-publish-row]")?.dataset.jobId;
      if (!window.confirm("确认重新发布？系统会保留原失败记录，并创建一条新的任务。")) return;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${sourceId}/retry`, { method: "POST", body: "{}" });
        cloneRowsForRetry(sourceId, data.job);
        showMessage(`已创建重试任务 ${data.job_id}，并交给统一调度器。`, "success");
      } catch (error) { showMessage(`重试失败：${error.message}`, "error"); }
      return;
    }

    const markPublished = event.target.closest("[data-mark-published]");
    if (markPublished) {
      const row = markPublished.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      const platformUrl = window.prompt("请粘贴已经人工核对过的平台作品链接。没有链接不能标记成功：", "");
      if (!platformUrl || !window.confirm("确认该链接对应本任务，并将任务标记为已发布？")) return;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/mark-published`, { method: "POST", body: JSON.stringify({ platform_url: platformUrl }) });
        updateRowFromJob(data.job || { id: jobId, status: "PUBLISHED", platform_url: platformUrl });
      } catch (error) { showMessage(`标记失败：${error.message}`, "error"); }
      return;
    }

    const markFailed = event.target.closest("[data-mark-failed]");
    if (markFailed) {
      const jobId = markFailed.closest("[data-publish-row]")?.dataset.jobId;
      if (!window.confirm("请先在平台确认没有发布成功。确认将本任务标记为失败？")) return;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/mark-failed`, { method: "POST" });
        updateRowFromJob(data.job || { id: jobId, status: "FAILED" });
      } catch (error) { showMessage(`标记失败：${error.message}`, "error"); }
      return;
    }

    const openCreator = event.target.closest("[data-open-creator]");
    if (openCreator) {
      const accountId = openCreator.closest("[data-publish-row]")?.dataset.accountId;
      if (!accountId) { showMessage("这条任务没有发布账号。", "error"); return; }
      try {
        const data = await window.apiFetch(`/api/publish/accounts/${accountId}/open-center`, { method: "POST" });
        showMessage(data.message || "已打开创作者中心。", "success");
      } catch (error) { showMessage(`打开失败：${error.message}`, "error"); }
      return;
    }

    const viewEvents = event.target.closest("[data-view-events]");
    if (viewEvents) {
      const jobId = viewEvents.closest("[data-publish-row]")?.dataset.jobId;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/events`);
        const lines = (data.events || []).map((item) => `${item.occurred_at} · ${item.from_status || "—"} → ${item.to_status || "—"} · ${item.message || item.event_type}`);
        window.alert(lines.join("\n") || "暂无执行事件。");
      } catch (error) { showMessage(`读取执行详情失败：${error.message}`, "error"); }
      return;
    }

    const accountAction = event.target.closest("[data-account-login], [data-account-check]");
    if (accountAction) {
      const accountRow = accountAction.closest("[data-account-row]");
      const accountId = accountRow?.dataset.accountId;
      const action = accountAction.matches("[data-account-login]") ? "login" : "check";
      accountAction.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/accounts/${accountId}/${action}`, { method: "POST" });
        const account = data.account || {};
        const statusNode = accountRow.querySelector("[data-account-status]");
        if (statusNode && action === "check") {
          statusNode.textContent = accountStatusLabel(account.login_status);
          statusNode.classList.toggle("tone-green", account.login_status === "normal");
          statusNode.classList.toggle("tone-red", account.login_status === "invalid");
          statusNode.classList.toggle("tone-amber", !["normal", "invalid"].includes(account.login_status));
        }
        showMessage(data.message || data.worker_result?.message || "账号操作已执行。", "success");
      } catch (error) { showMessage(`账号操作失败：${error.message}`, "error"); }
      finally { accountAction.disabled = false; }
    }
  });

  document.querySelector("[data-open-schedule-drawer]")?.addEventListener("click", openDrawer);
  document.querySelector("[data-close-schedule-drawer]")?.addEventListener("click", closeDrawer);
  drawerBackdrop?.addEventListener("click", closeDrawer);
  document.querySelector("[data-clear-selection]")?.addEventListener("click", () => { selectedJobIds.clear(); updateSelectionUi(); });

  document.querySelector("[data-open-account-drawer]")?.addEventListener("click", () => { accountDrawer.hidden = false; accountBackdrop.hidden = false; document.body.classList.add("has-schedule-drawer"); });
  function closeAccountDrawer() { accountDrawer.hidden = true; accountBackdrop.hidden = true; document.body.classList.remove("has-schedule-drawer"); }
  document.querySelector("[data-close-account-drawer]")?.addEventListener("click", closeAccountDrawer);
  accountBackdrop?.addEventListener("click", closeAccountDrawer);

  document.querySelector("[data-send-selected]")?.addEventListener("click", async () => {
    const ids = Array.from(selectedJobIds);
    if (!ids.length || !window.confirm(`确认立即发送 ${ids.length} 条任务？`)) return;
    for (const jobId of ids) {
      try {
        const result = await window.apiFetch(`/api/publish/jobs/${jobId}/publish-now`, { method: "POST" });
        updateRowFromJob(result.job || { id: jobId, status: "SCHEDULED" });
      } catch (error) { showMessage(`任务 ${jobId} 入队失败：${error.message}`, "error"); return; }
    }
    selectedJobIds.clear(); updateSelectionUi();
    showMessage("所选任务均已进入统一调度器。", "success");
  });

  document.querySelector("[data-apply-batch-target]")?.addEventListener("click", async () => {
    const payload = {
      job_ids: Array.from(selectedJobIds),
      platform: document.querySelector("[data-batch-platform]").value,
      account_id: document.querySelector("[data-batch-account]").value,
      publish_mode: "local_browser",
    };
    try {
      const data = await window.apiFetch("/api/publish/jobs/target-batch", { method: "PATCH", body: JSON.stringify(payload) });
      (data.jobs || []).forEach(updateRowFromJob);
      showMessage(`已更新 ${data.updated_count} 条任务的平台和账号。`, "success");
    } catch (error) { showMessage(`批量设置失败：${error.message}`, "error"); }
  });

  document.querySelector("[data-batch-ai]")?.addEventListener("click", async () => {
    for (const jobId of Array.from(selectedJobIds)) {
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/metadata?use_ai=true`, { method: "POST" });
        updateRowFromJob(data.job);
      } catch (error) { showMessage(`AI 补齐任务 ${jobId} 失败：${error.message}`, "error"); return; }
    }
    showMessage("所选任务的 AI 标题和简介已补齐。", "success");
  });

  scheduleForm?.elements.interval_preset?.addEventListener("change", () => {
    document.querySelector("[data-custom-interval]").hidden = scheduleForm.elements.interval_preset.value !== "custom";
  });

  document.querySelector("[data-preview-schedule]")?.addEventListener("click", async () => {
    const payload = schedulePayload("apply");
    if (!payload.start_at_local || beijingInputToTimestamp(payload.start_at_local) <= Date.now()) {
      showMessage("请选择晚于当前时间的北京时间。", "error"); return;
    }
    try {
      const data = await window.apiFetch("/api/publish/schedules/preview", { method: "POST", body: JSON.stringify(payload) });
      previewList.innerHTML = "";
      latestPreviewItems = data.schedule || [];
      latestPreviewItems.forEach((item, index) => {
        const row = document.querySelector(`[data-publish-row][data-job-id="${CSS.escape(item.job_id)}"]`);
        const line = document.createElement("div");
        line.innerHTML = `<strong>第 ${index + 1} 条：${row?.querySelector("[data-row-title]")?.textContent || item.job_id}</strong><time>${item.scheduled_at_local_display}</time>`;
        previewList.appendChild(line);
      });
      latestPreviewSignature = previewSignature(payload);
      confirmScheduleButton.disabled = false;
    } catch (error) { showMessage(`排期预览失败：${error.message}`, "error"); }
  });

  scheduleForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = schedulePayload("apply");
    if (latestPreviewSignature !== previewSignature(payload) || !latestPreviewItems.length) {
      showMessage("排期参数已变化，请重新预览。", "error"); return;
    }
    payload.confirmed_schedule = latestPreviewItems;
    try {
      const data = await window.apiFetch("/api/publish/jobs/schedule-batch", { method: "PATCH", body: JSON.stringify(payload) });
      (data.jobs || []).forEach(updateRowFromJob);
      const saved = (data.schedule || []).map((item, index) => `第 ${index + 1} 条：${item.scheduled_at_local_display}`).join("；");
      showMessage(`${data.message} ${saved}`, "success");
      selectedJobIds.clear(); updateSelectionUi(); closeDrawer(); invalidatePreview();
    } catch (error) { showMessage(`排期保存失败：${error.message}`, "error"); }
  });

  document.querySelector("[data-supplement-publish-jobs]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget; button.disabled = true;
    try {
      const data = await window.apiFetch("/api/publish/queue/refresh?use_ai=false", { method: "POST" });
      showMessage(data.message || "缺失任务已补充，请稍后查看内容准备区。", "success");
    } catch (error) { showMessage(`补充任务失败：${error.message}`, "error"); }
    finally { button.disabled = false; }
  });

  document.querySelectorAll("[data-publish-editor]").forEach(syncPlatformFields);
  filterAccountOptions(document.querySelector("[data-batch-account]"), document.querySelector("[data-batch-platform]")?.value || "douyin");
  if (scheduleForm?.elements.start_at_local) scheduleForm.elements.start_at_local.value = beijingDatetimeValue(Date.now() + 10 * 60 * 1000);
  updateSelectionUi(); applyHistoryFilter();
  window.setInterval(refreshJobs, 5000);
}
