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
  const calendarNode = document.querySelector("[data-schedule-calendar]");
  const calendarTitle = document.querySelector("[data-calendar-title]");
  const scheduleEmpty = document.querySelector("[data-schedule-empty]");
  const platformListTitle = document.querySelector("[data-platform-list-title]");
  const schedulerHealthNode = document.querySelector("[data-scheduler-health]");
  let latestPreviewSignature = "";
  let latestPreviewItems = [];
  let activeSchedulePlatform = "douyin";
  let calendarMonth = currentBeijingMonth();
  let scheduleRefreshFrame = 0;
  let workerAvailable = schedulerHealthNode?.dataset.workerAvailable === "true";
  let workerMessage = document.querySelector("[data-worker-message]")?.textContent?.split(" · ")[0] || "Windows 发布 Worker 未连接";

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

  function beijingDateParts(value = new Date()) {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: APP_TIMEZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date(value));
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return { year: Number(values.year), month: Number(values.month), day: Number(values.day) };
  }

  function currentBeijingMonth() {
    const today = beijingDateParts();
    return { year: today.year, month: today.month };
  }

  function beijingDateKey(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const parts = beijingDateParts(date);
    return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
  }

  function scheduleRows() {
    return Array.from(document.querySelectorAll('[data-publish-row][data-section="schedule"]'));
  }

  function renderPlatformSchedule() {
    const rows = scheduleRows();
    ["douyin", "bilibili"].forEach((platform) => {
      const available = rows.filter((row) => sectionAllows("schedule", row.dataset.status || "") && row.dataset.platform === platform);
      const waiting = available.filter((row) => row.dataset.status === "WAITING").length;
      const scheduled = available.filter((row) => row.dataset.status === "SCHEDULED").length;
      const waitingNode = document.querySelector(`[data-platform-waiting="${platform}"]`);
      const scheduledNode = document.querySelector(`[data-platform-scheduled="${platform}"]`);
      if (waitingNode) waitingNode.textContent = String(waiting);
      if (scheduledNode) scheduledNode.textContent = String(scheduled);
    });
    document.querySelectorAll("[data-schedule-platform]").forEach((button) => {
      const active = button.dataset.schedulePlatform === activeSchedulePlatform;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    let visibleCount = 0;
    rows.forEach((row) => {
      const visible = sectionAllows("schedule", row.dataset.status || "") && row.dataset.platform === activeSchedulePlatform;
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    if (platformListTitle) platformListTitle.textContent = `${activeSchedulePlatform === "douyin" ? "抖音" : "B站"}任务清单`;
    if (scheduleEmpty) scheduleEmpty.hidden = visibleCount > 0;
  }

  function renderCalendar() {
    if (!calendarNode) return;
    const { year, month } = calendarMonth;
    if (calendarTitle) calendarTitle.textContent = `${year} 年 ${month} 月 · ${activeSchedulePlatform === "douyin" ? "抖音" : "B站"}`;
    const firstDay = new Date(Date.UTC(year, month - 1, 1));
    const mondayOffset = (firstDay.getUTCDay() + 6) % 7;
    const gridStart = new Date(Date.UTC(year, month - 1, 1 - mondayOffset));
    const todayParts = beijingDateParts();
    const todayKey = `${todayParts.year}-${String(todayParts.month).padStart(2, "0")}-${String(todayParts.day).padStart(2, "0")}`;
    const jobsByDate = new Map();
    scheduleRows().forEach((row) => {
      if (row.dataset.platform !== activeSchedulePlatform || row.dataset.status !== "SCHEDULED") return;
      const utcValue = row.querySelector("[data-row-schedule]")?.dataset.utc || "";
      const key = beijingDateKey(utcValue);
      if (!key) return;
      if (!jobsByDate.has(key)) jobsByDate.set(key, []);
      jobsByDate.get(key).push({
        id: row.dataset.jobId,
        title: row.querySelector("[data-row-title]")?.textContent?.trim() || "未命名任务",
        time: formatBeijingTimestamp(utcValue).slice(11),
      });
    });
    calendarNode.innerHTML = "";
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(gridStart.getTime() + index * 86400000);
      const cellYear = date.getUTCFullYear();
      const cellMonth = date.getUTCMonth() + 1;
      const cellDay = date.getUTCDate();
      const key = `${cellYear}-${String(cellMonth).padStart(2, "0")}-${String(cellDay).padStart(2, "0")}`;
      const jobs = jobsByDate.get(key) || [];
      const cell = document.createElement("div");
      cell.className = "publish-calendar-day";
      cell.setAttribute("role", "gridcell");
      cell.dataset.date = key;
      cell.classList.toggle("is-outside", cellMonth !== month);
      cell.classList.toggle("is-today", key === todayKey);
      const number = document.createElement("span");
      number.className = "calendar-day-number";
      number.textContent = String(cellDay);
      cell.appendChild(number);
      jobs.slice(0, 2).forEach((job) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "calendar-job-chip";
        chip.dataset.calendarJob = job.id;
        chip.title = `${job.time} ${job.title}`;
        chip.textContent = `${job.time} ${job.title}`;
        cell.appendChild(chip);
      });
      if (jobs.length > 2) {
        const more = document.createElement("small");
        more.className = "calendar-job-more";
        more.textContent = `另有 ${jobs.length - 2} 条`;
        cell.appendChild(more);
      }
      calendarNode.appendChild(cell);
    }
  }

  function refreshScheduleViews() {
    renderPlatformSchedule();
    renderCalendar();
  }

  function queueScheduleRefresh() {
    if (scheduleRefreshFrame) window.cancelAnimationFrame(scheduleRefreshFrame);
    scheduleRefreshFrame = window.requestAnimationFrame(() => {
      scheduleRefreshFrame = 0;
      refreshScheduleViews();
    });
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

  function rowReadiness(row) {
    try {
      return JSON.parse(row?.dataset.sendReadiness || "{}");
    } catch (_error) {
      return {};
    }
  }

  function effectiveReadiness(row) {
    const readiness = rowReadiness(row);
    if (readiness.requires_worker && !workerAvailable) {
      return {
        ...readiness,
        ready: false,
        dispatch_ready: false,
        can_auto_resolve: false,
        message: workerMessage || "Windows 发布 Worker 未连接",
        action: "start_worker",
      };
    }
    return readiness;
  }

  function setupActionLabel(action) {
    return {
      login_account: "打开登录窗口",
      create_account: "新增账号",
      select_account: "选择账号",
      complete_content: "完善内容",
      start_worker: "连接 Worker",
    }[action] || "完善发送配置";
  }

  function applyRowReadiness(row, nextReadiness = null) {
    if (!row) return;
    if (nextReadiness) row.dataset.sendReadiness = JSON.stringify(nextReadiness);
    const readiness = effectiveReadiness(row);
    if (!Object.keys(readiness).length) return;
    const message = row.querySelector("[data-readiness-message]");
    if (message) {
      message.textContent = readiness.message || "发布条件尚未满足";
      message.classList.toggle("is-ready", Boolean(readiness.ready || readiness.dispatch_ready));
      message.classList.toggle("is-blocked", !readiness.ready && !readiness.dispatch_ready);
    }
    const button = row.querySelector("[data-primary-send-action]");
    if (!button) return;
    button.removeAttribute("data-publish-now");
    button.removeAttribute("data-send-setup");
    button.removeAttribute("data-repair-job");
    button.hidden = false;
    button.disabled = false;
    const section = row.dataset.section;
    if (section === "schedule") {
      if (readiness.ready || readiness.can_auto_resolve) {
        button.dataset.publishNow = "";
        button.className = "primary-button";
        button.textContent = readiness.action === "export" ? "立即导出" : (readiness.can_auto_resolve ? "转换并发送" : "立即发送");
      } else {
        button.dataset.sendSetup = "";
        button.className = "secondary-button";
        button.textContent = setupActionLabel(readiness.action);
      }
      return;
    }
    if (section === "history" && row.dataset.status === "NEED_REVIEW" && readiness.repairable) {
      if (readiness.dispatch_ready || (readiness.action === "select_account" && row.querySelector("[data-repair-account-select]"))) {
        button.dataset.repairJob = "";
        button.className = "primary-button";
        button.textContent = readiness.action === "select_account" ? "选择后修复并发送" : "修复并发送";
      } else {
        button.dataset.sendSetup = "";
        button.className = "secondary-button";
        button.textContent = setupActionLabel(readiness.action);
      }
    } else {
      button.hidden = true;
    }
  }

  function applyReadinessError(error, row) {
    if (!error?.details || !row) return false;
    const current = rowReadiness(row);
    applyRowReadiness(row, {
      ...current,
      ...error.details,
      repairable: Boolean(current.repairable || error.details.repairable),
    });
    showMessage(error.details.message || error.message, "error");
    return true;
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
      if (job.platform) row.dataset.platform = job.platform;
      if (job.account_id !== undefined) row.dataset.accountId = job.account_id || "";
      if (job.send_readiness) row.dataset.sendReadiness = JSON.stringify(job.send_readiness);
      row.hidden = !sectionAllows(row.dataset.section, status);
      const statusNode = row.querySelector("[data-row-status]");
      if (statusNode) statusNode.textContent = job.status_label || statusLabel(status);
      const titleNode = row.querySelector("[data-row-title]");
      if (titleNode && job.title) titleNode.textContent = job.title;
      const platformNode = row.querySelector("[data-row-platform]");
      if (platformNode && job.platform_label) platformNode.textContent = job.platform_label;
      const accountNode = row.querySelector("[data-row-account]");
      if (accountNode && job.account_name !== undefined) accountNode.textContent = job.account_name || "未选择";
      const editor = row.querySelector("[data-publish-editor]");
      if (editor) {
        if (job.platform && editor.elements.platform) editor.elements.platform.value = job.platform;
        const resolvedAccountId = job.account_id || job.send_readiness?.resolved_account_id || "";
        if (job.account_id !== undefined && editor.elements.account_id) editor.elements.account_id.value = resolvedAccountId;
        const resolvedMode = job.send_readiness?.resolved_publish_mode || job.publish_mode || "";
        if (resolvedMode && editor.elements.publish_mode?.querySelector(`option[value="${CSS.escape(resolvedMode)}"]`)) {
          editor.elements.publish_mode.value = resolvedMode;
        }
        syncPlatformFields(editor);
      }
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
      applyRowReadiness(row);
    });
    if (["PUBLISHED", "EXPORTED", "CANCELLED", "PUBLISHING", "NEED_REVIEW", "FAILED"].includes(String(job.status || ""))) {
      selectedJobIds.delete(job.id);
      updateSelectionUi();
    }
    applyHistoryFilter();
    queueScheduleRefresh();
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

  function openAccountDrawer(platform = "") {
    if (!accountDrawer || !accountBackdrop) return;
    accountDrawer.hidden = false;
    accountBackdrop.hidden = false;
    document.body.classList.add("has-schedule-drawer");
    const form = accountDrawer.querySelector("[data-account-create]");
    if (platform && form?.elements.platform) form.elements.platform.value = platform;
  }

  function readinessAccountId(readiness) {
    if (readiness.resolved_account_id) return readiness.resolved_account_id;
    const loginIssue = (readiness.issues || []).find((issue) => issue.action === "login_account");
    return loginIssue?.account_id || "";
  }

  async function handleSendSetup(row) {
    const readiness = effectiveReadiness(row);
    if (readiness.action === "start_worker") {
      document.querySelector("[data-worker-help]")?.removeAttribute("hidden");
      document.querySelector("[data-scheduler-health]")?.scrollIntoView({ behavior: "smooth", block: "center" });
      await refreshSchedulerHealth(true);
      return;
    }
    if (readiness.action === "login_account") {
      const accountId = readinessAccountId(readiness);
      openAccountDrawer(row.dataset.platform || "");
      if (!accountId) {
        showMessage("没有找到需要登录的账号，请先在账号管理中选择账号。", "error");
        return;
      }
      try {
        const data = await window.apiFetch(`/api/publish/accounts/${accountId}/login`, { method: "POST" });
        showMessage(data.message || data.worker_result?.message || "登录窗口已打开，请在专属 Chrome 中完成登录。", "success");
        await refreshJobs();
      } catch (error) {
        document.querySelector("[data-worker-help]")?.removeAttribute("hidden");
        showMessage(`打开登录窗口失败：${error.message}`, "error");
      }
      return;
    }
    if (readiness.action === "create_account") {
      openAccountDrawer(row.dataset.platform || "");
      const nameInput = accountDrawer?.querySelector('[data-account-create] input[name="account_name"]');
      nameInput?.focus();
      showMessage("请先创建对应平台账号；系统不会保存账号密码。", "error");
      return;
    }
    if (readiness.action === "select_account") {
      const repairSelect = row.querySelector("[data-repair-account-select]");
      if (repairSelect) {
        repairSelect.focus();
        showMessage("请选择同平台账号，再点击“修复并发送”。", "error");
        return;
      }
      switchTab("content");
      const editorRow = document.querySelector(`[data-publish-row][data-section="content"][data-job-id="${CSS.escape(row.dataset.jobId)}"]`);
      editorRow?.scrollIntoView({ behavior: "smooth", block: "center" });
      editorRow?.querySelector("[data-account-select]")?.focus();
      showMessage("请在内容准备中选择本次使用的同平台账号并保存。", "error");
      return;
    }
    if (readiness.action === "complete_content") {
      switchTab("content");
      const editorRow = document.querySelector(`[data-publish-row][data-section="content"][data-job-id="${CSS.escape(row.dataset.jobId)}"]`);
      if (editorRow && !editorRow.hidden) {
        editorRow.scrollIntoView({ behavior: "smooth", block: "center" });
        editorRow.querySelector("input, textarea, select")?.focus();
      }
      showMessage(readiness.message || "请先补齐发布内容并保存。", "error");
    }
  }

  async function refreshJobs() {
    try {
      const data = await window.apiFetch("/api/publish/jobs");
      (data.jobs || []).forEach(updateRowFromJob);
    } catch (_error) {
      // 后台轮询失败不遮挡用户正在编辑的内容。
    }
  }

  async function refreshSchedulerHealth(showResult = false) {
    const button = document.querySelector("[data-refresh-worker]");
    if (button) button.disabled = true;
    try {
      const data = await window.apiFetch("/api/publish/scheduler/health");
      const statusNode = document.querySelector("[data-worker-status]");
      const message = document.querySelector("[data-worker-message]");
      const help = document.querySelector("[data-worker-help]");
      const dot = document.querySelector("[data-scheduler-health] .health-dot");
      workerAvailable = Boolean(data.worker_available);
      workerMessage = data.worker_message || "Windows 发布 Worker 未连接";
      if (schedulerHealthNode) schedulerHealthNode.dataset.workerAvailable = workerAvailable ? "true" : "false";
      if (statusNode) statusNode.textContent = data.worker_available ? "正常" : "未连接";
      if (message) message.textContent = `${data.worker_message} · 页面及排期均使用北京时间`;
      if (help) help.hidden = Boolean(data.worker_available);
      if (dot) dot.classList.toggle("is-ok", Boolean(data.running && data.worker_available));
      document.querySelectorAll('[data-publish-row][data-section="schedule"], [data-publish-row][data-section="history"]').forEach((row) => applyRowReadiness(row));
      if (showResult) showMessage(data.worker_available ? "Windows Worker 已连接，可以打开账号登录窗口。" : "Worker 仍未连接，请按卡片中的连接修复步骤操作。", data.worker_available ? "success" : "error");
    } catch (error) {
      if (showResult) showMessage(`检测失败：${error.message}`, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.querySelectorAll("[data-center-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.centerTab));
  });
  document.querySelectorAll("[data-schedule-platform]").forEach((button) => {
    button.addEventListener("click", () => {
      activeSchedulePlatform = button.dataset.schedulePlatform || "douyin";
      refreshScheduleViews();
    });
  });
  document.querySelector("[data-calendar-previous]")?.addEventListener("click", () => {
    const previous = new Date(Date.UTC(calendarMonth.year, calendarMonth.month - 2, 1));
    calendarMonth = { year: previous.getUTCFullYear(), month: previous.getUTCMonth() + 1 };
    renderCalendar();
  });
  document.querySelector("[data-calendar-next]")?.addEventListener("click", () => {
    const next = new Date(Date.UTC(calendarMonth.year, calendarMonth.month, 1));
    calendarMonth = { year: next.getUTCFullYear(), month: next.getUTCMonth() + 1 };
    renderCalendar();
  });
  document.querySelector("[data-calendar-today]")?.addEventListener("click", () => {
    calendarMonth = currentBeijingMonth();
    renderCalendar();
  });
  calendarNode?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-calendar-job]");
    if (!chip) return;
    const row = document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(chip.dataset.calendarJob)}"]`);
    if (!row || row.hidden) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-calendar-focus");
    window.setTimeout(() => row.classList.remove("is-calendar-focus"), 1600);
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
        await refreshJobs();
      } catch (error) {
        if (resultNode) resultNode.textContent = `保存失败：${error.message}`;
      }
    }
  });

  document.addEventListener("click", async (event) => {
    const setupButton = event.target.closest("[data-send-setup]");
    if (setupButton) {
      await handleSendSetup(setupButton.closest("[data-publish-row]"));
      return;
    }

    const repairButton = event.target.closest("[data-repair-job]");
    if (repairButton) {
      const row = repairButton.closest("[data-publish-row]");
      const sourceId = row?.dataset.jobId;
      const accountSelect = row?.querySelector("[data-repair-account-select]");
      const accountId = String(accountSelect?.value || "");
      if (accountSelect && !accountId) {
        showMessage("请先选择用于替代任务的同平台账号。", "error");
        accountSelect.focus();
        return;
      }
      if (!sourceId || !window.confirm("确认修复并发送？原需复核记录会保留，系统只会创建一条新的 Windows Chrome 投稿任务。")) return;
      repairButton.disabled = true;
      try {
        const query = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
        const data = await window.apiFetch(`/api/publish/jobs/${sourceId}/repair-and-publish${query}`, { method: "POST" });
        cloneRowsForRetry(sourceId, data.job);
        showMessage(data.message || "旧记录已保留，替代任务已进入统一调度器。", "success");
      } catch (error) {
        if (!applyReadinessError(error, row)) showMessage(`修复发送失败：${error.message}`, "error");
      } finally { repairButton.disabled = false; }
      return;
    }

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
        if (!applyReadinessError(error, publishNowButton.closest("[data-publish-row]"))) {
          showMessage(`立即发送失败：${error.message}`, "error");
        }
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
        await refreshJobs();
      } catch (error) {
        document.querySelector("[data-worker-help]")?.removeAttribute("hidden");
        showMessage(`账号操作失败：${error.message}`, "error");
      }
      finally { accountAction.disabled = false; }
    }
  });

  document.querySelector("[data-refresh-worker]")?.addEventListener("click", () => refreshSchedulerHealth(true));

  document.querySelector("[data-open-schedule-drawer]")?.addEventListener("click", openDrawer);
  document.querySelector("[data-close-schedule-drawer]")?.addEventListener("click", closeDrawer);
  drawerBackdrop?.addEventListener("click", closeDrawer);
  document.querySelector("[data-clear-selection]")?.addEventListener("click", () => { selectedJobIds.clear(); updateSelectionUi(); });

  document.querySelector("[data-open-account-drawer]")?.addEventListener("click", () => openAccountDrawer());
  function closeAccountDrawer() { accountDrawer.hidden = true; accountBackdrop.hidden = true; document.body.classList.remove("has-schedule-drawer"); }
  document.querySelector("[data-close-account-drawer]")?.addEventListener("click", closeAccountDrawer);
  accountBackdrop?.addEventListener("click", closeAccountDrawer);

  document.querySelector("[data-send-selected]")?.addEventListener("click", async () => {
    const ids = Array.from(selectedJobIds);
    if (!ids.length) return;
    const blockedRow = ids
      .map((jobId) => document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(jobId)}"]`))
      .find((row) => {
        const readiness = effectiveReadiness(row);
        return row && !readiness.ready && !readiness.can_auto_resolve;
      });
    if (blockedRow) {
      await handleSendSetup(blockedRow);
      return;
    }
    if (!window.confirm(`确认立即发送 ${ids.length} 条任务？`)) return;
    for (const jobId of ids) {
      try {
        const result = await window.apiFetch(`/api/publish/jobs/${jobId}/publish-now`, { method: "POST" });
        updateRowFromJob(result.job || { id: jobId, status: "SCHEDULED" });
      } catch (error) {
        const row = document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(jobId)}"]`);
        if (!applyReadinessError(error, row)) showMessage(`任务 ${jobId} 入队失败：${error.message}`, "error");
        return;
      }
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
    } catch (error) {
      const row = document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(payload.job_ids[0] || "")}"]`);
      if (!applyReadinessError(error, row)) showMessage(`排期预览失败：${error.message}`, "error");
    }
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
    } catch (error) {
      const row = document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(payload.job_ids[0] || "")}"]`);
      if (!applyReadinessError(error, row)) showMessage(`排期保存失败：${error.message}`, "error");
    }
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
  document.querySelectorAll('[data-publish-row][data-section="schedule"], [data-publish-row][data-section="history"]').forEach((row) => applyRowReadiness(row));
  updateSelectionUi(); applyHistoryFilter(); refreshScheduleViews();
  window.setInterval(() => { refreshJobs(); refreshSchedulerHealth(); }, 5000);
}
