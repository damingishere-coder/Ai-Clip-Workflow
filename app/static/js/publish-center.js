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
  const latestScheduleButton = document.querySelector("[data-use-latest-schedule]");
  const latestScheduleNote = document.querySelector("[data-latest-schedule-note]");
  const previewScheduleButton = document.querySelector("[data-preview-schedule]");
  const scheduleFeedbackNode = document.querySelector("[data-schedule-feedback]");
  const confirmScheduleButton = document.querySelector("[data-confirm-schedule]");
  const historyFilter = document.querySelector("[data-history-filter]");
  const historyCalendarCard = document.querySelector("[data-history-calendar-card]");
  const historyCalendarNode = document.querySelector("[data-history-calendar]");
  const historyCalendarTitle = document.querySelector("[data-history-calendar-title]");
  const historyListNode = document.querySelector("[data-history-list]");
  const historyEmpty = document.querySelector("[data-history-empty]");
  const historyListTitle = document.querySelector("[data-history-list-title]");
  const historyListSummary = document.querySelector("[data-history-list-summary]");
  const historyListEyebrow = document.querySelector("[data-history-list-eyebrow]");
  const historyClearDateButton = document.querySelector("[data-history-clear-date]");
  const historyBatchBar = document.querySelector("[data-history-batch-bar]");
  const historySelectedCount = document.querySelector("[data-history-selected-count]");
  const historyBatchHideButton = document.querySelector("[data-history-batch-hide]");
  const historyBatchRestoreButton = document.querySelector("[data-history-batch-restore]");
  const historyPagination = document.querySelector("[data-history-pagination]");
  const historyPageSummary = document.querySelector("[data-history-page-summary]");
  const historyPreviousButton = document.querySelector("[data-history-previous]");
  const historyNextButton = document.querySelector("[data-history-next]");
  const calendarNode = document.querySelector("[data-schedule-calendar]");
  const calendarTitle = document.querySelector("[data-calendar-title]");
  const calendarDayDetail = document.querySelector("[data-calendar-day-detail]");
  const calendarDayTitle = document.querySelector("[data-calendar-day-title]");
  const calendarDaySummary = document.querySelector("[data-calendar-day-summary]");
  const calendarDayList = document.querySelector("[data-calendar-day-list]");
  const scheduleListNode = document.querySelector(".publish-plan-list");
  const scheduleEmpty = document.querySelector("[data-schedule-empty]");
  const contentEmpty = document.querySelector("[data-content-empty]");
  const platformListTitle = document.querySelector("[data-platform-list-title]");
  const schedulerHealthNode = document.querySelector("[data-scheduler-health]");
  const backfillCoversButton = document.querySelector("[data-backfill-covers]");
  let latestPreviewSignature = "";
  let latestPreviewItems = [];
  let activePlatform = "douyin";
  let calendarMonth = currentBeijingMonth();
  let selectedCalendarDate = "";
  let historyMonth = currentBeijingMonth();
  let historySelectedDate = "";
  let historyDeletedView = false;
  let historyPage = 1;
  let historyTotalPages = 0;
  let historyRefreshFrame = 0;
  let historyRefreshCalendar = false;
  let historyRequestSequence = 0;
  let historyRefreshInFlight = false;
  let historyRefreshQueuedCalendar = false;
  let historyRefreshQueuedRecords = false;
  const selectedHistoryJobIds = new Set();
  let scheduleRefreshFrame = 0;
  let scheduleRowOrderSequence = 0;
  const scheduleRowOrders = new WeakMap();
  let workerAvailable = schedulerHealthNode?.dataset.workerAvailable === "true";
  let workerMessage = document.querySelector("[data-worker-message]")?.textContent?.split(" · ")[0] || "Windows 发布 Worker 未连接";

  function showMessage(message, tone = "info") {
    if (!messageNode) return;
    messageNode.hidden = false;
    messageNode.textContent = message;
    messageNode.classList.toggle("tone-red", tone === "error");
    messageNode.classList.toggle("tone-blue", tone !== "error");
  }

  function missingCoverRows() {
    return Array.from(document.querySelectorAll('[data-publish-row][data-section="content"]')).filter((row) => {
      const editor = row.querySelector("[data-publish-editor]");
      const coverPath = String(editor?.elements?.cover_file_path?.value || "").trim();
      return (
        row.dataset.platform === activePlatform
        && row.dataset.outputActive !== "false"
        && sectionAllows("content", String(row.dataset.status || "").toUpperCase())
        && !coverPath
      );
    });
  }

  function updateBackfillCoversButton() {
    if (!backfillCoversButton) return;
    const count = missingCoverRows().length;
    const loading = backfillCoversButton.dataset.loading === "true";
    backfillCoversButton.dataset.missingCount = String(count);
    backfillCoversButton.disabled = loading || count === 0;
    backfillCoversButton.textContent = loading
      ? `正在补充${platformLabel()} ${count} 条封面…`
      : `一键补充${platformLabel()}缺失封面${count ? `（${count}）` : ""}`;
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

  function scheduleRowOrder(row) {
    if (!scheduleRowOrders.has(row)) {
      scheduleRowOrders.set(row, scheduleRowOrderSequence);
      scheduleRowOrderSequence += 1;
    }
    return scheduleRowOrders.get(row);
  }

  function scheduleTimestamp(row) {
    if (String(row?.dataset.status || "").toUpperCase() !== "SCHEDULED") return Number.POSITIVE_INFINITY;
    const timestamp = Date.parse(row.querySelector("[data-row-schedule]")?.dataset.utc || "");
    return Number.isFinite(timestamp) ? timestamp : Number.POSITIVE_INFINITY;
  }

  function sortScheduleRows() {
    if (!scheduleListNode) return;
    const rows = scheduleRows();
    rows.forEach(scheduleRowOrder);
    rows.sort((first, second) => {
      const firstTimestamp = scheduleTimestamp(first);
      const secondTimestamp = scheduleTimestamp(second);
      const firstScheduled = Number.isFinite(firstTimestamp);
      const secondScheduled = Number.isFinite(secondTimestamp);
      if (firstScheduled !== secondScheduled) return firstScheduled ? -1 : 1;
      if (firstScheduled && firstTimestamp !== secondTimestamp) return firstTimestamp - secondTimestamp;
      return scheduleRowOrder(first) - scheduleRowOrder(second);
    });
    rows.forEach((row) => scheduleListNode.appendChild(row));
  }

  function scheduledJobsByDate() {
    const jobsByDate = new Map();
    scheduleRows().forEach((row) => {
      if (
        row.dataset.platform !== activePlatform
        || row.dataset.status !== "SCHEDULED"
        || row.dataset.outputActive === "false"
      ) return;
      const utcValue = row.querySelector("[data-row-schedule]")?.dataset.utc || "";
      const timestamp = Date.parse(utcValue);
      const key = beijingDateKey(utcValue);
      if (!key || !Number.isFinite(timestamp)) return;
      if (!jobsByDate.has(key)) jobsByDate.set(key, []);
      jobsByDate.get(key).push({
        id: row.dataset.jobId,
        title: row.querySelector("[data-row-title]")?.textContent?.trim() || "未命名任务",
        account: row.querySelector("[data-row-account]")?.textContent?.trim() || "未选择账号",
        status: row.querySelector("[data-row-status]")?.textContent?.trim() || "已排期",
        time: formatBeijingTimestamp(utcValue).slice(11),
        timestamp,
        order: scheduleRowOrder(row),
      });
    });
    jobsByDate.forEach((jobs) => {
      jobs.sort((first, second) => first.timestamp - second.timestamp || first.order - second.order);
    });
    return jobsByDate;
  }

  function calendarDateLabel(value) {
    const [year, month, day] = String(value || "").split("-").map(Number);
    return year && month && day ? `${year} 年 ${month} 月 ${day} 日` : "当天排期";
  }

  function closeCalendarDayDetail() {
    selectedCalendarDate = "";
    if (calendarDayDetail) calendarDayDetail.hidden = true;
    if (calendarDayList) calendarDayList.innerHTML = "";
    calendarNode?.querySelectorAll("[data-calendar-date]").forEach((cell) => {
      cell.classList.remove("is-selected");
      cell.setAttribute("aria-selected", "false");
    });
  }

  function renderCalendarDayDetail(jobsByDate) {
    if (!calendarDayDetail || !calendarDayList || !selectedCalendarDate) {
      if (calendarDayDetail) calendarDayDetail.hidden = true;
      return;
    }
    const jobs = jobsByDate.get(selectedCalendarDate) || [];
    if (!jobs.length) {
      closeCalendarDayDetail();
      return;
    }
    if (calendarDayTitle) calendarDayTitle.textContent = calendarDateLabel(selectedCalendarDate);
    if (calendarDaySummary) {
      calendarDaySummary.textContent = `${platformLabel()} · ${jobs.length} 条排期 · 按北京时间从早到晚`;
    }
    calendarDayList.innerHTML = "";
    jobs.forEach((job) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "publish-calendar-day-item";
      button.dataset.calendarDetailJob = job.id;
      button.setAttribute("aria-label", `${job.time} ${job.title}，定位到任务`);
      const time = document.createElement("time");
      time.textContent = job.time;
      const identity = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = job.title;
      const meta = document.createElement("small");
      meta.textContent = `${job.account} · ${job.status}`;
      identity.append(title, meta);
      const action = document.createElement("span");
      action.className = "publish-calendar-day-action";
      action.textContent = "定位到任务";
      button.append(time, identity, action);
      calendarDayList.appendChild(button);
    });
    calendarDayDetail.hidden = false;
  }

  function focusScheduleRow(jobId) {
    const row = document.querySelector(
      `[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(jobId || "")}"]`,
    );
    if (!row || row.hidden) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-calendar-focus");
    window.setTimeout(() => row.classList.remove("is-calendar-focus"), 1600);
  }

  function setTaskGroupExpanded(group, expanded) {
    if (!group) return;
    group.dataset.expanded = expanded ? "true" : "false";
    const body = group.querySelector("[data-task-group-body]");
    const toggle = group.querySelector("[data-task-group-toggle]");
    if (body) body.hidden = !expanded;
    if (toggle) toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    const action = group.querySelector("[data-task-group-action]");
    if (action) action.textContent = expanded ? "收起" : "展开";
  }

  function syncContentTaskGroups() {
    const groups = Array.from(document.querySelectorAll("[data-publish-task-group]"));
    const visibleGroups = [];
    groups.forEach((group) => {
      const rows = Array.from(group.querySelectorAll('[data-publish-row][data-section="content"]'));
      const visibleCount = rows.filter((row) => !row.hidden).length;
      const count = group.querySelector("[data-task-group-count]");
      if (count) count.textContent = `${visibleCount} 条待准备`;
      group.hidden = visibleCount === 0;
      if (visibleCount > 0) visibleGroups.push(group);
      if (visibleCount === 0) setTaskGroupExpanded(group, false);
      syncTaskGroupSelectionUi(group);
    });
    if (visibleGroups.length && !visibleGroups.some((group) => group.dataset.expanded === "true")) {
      setTaskGroupExpanded(visibleGroups[0], true);
    }
    visibleGroups.forEach((group) => setTaskGroupExpanded(group, group.dataset.expanded === "true"));
    if (contentEmpty) contentEmpty.hidden = visibleGroups.length > 0;
  }

  function platformLabel(platform = activePlatform) {
    return platform === "bilibili" ? "B站" : "抖音";
  }

  function visibilityLabel(value) {
    return { public: "公开", friends: "好友可见", private: "仅自己可见" }[value] || "公开";
  }

  function sendConfirmation(row, actionLabel, visibility = "") {
    const title = row?.querySelector("[data-row-title]")?.textContent?.trim() || "未命名任务";
    const account = row?.querySelector("[data-row-account]")?.textContent?.trim()
      || row?.querySelector("[data-repair-account-select] option:checked")?.textContent?.trim()
      || "未选择";
    const resolvedVisibility = visibility || row?.dataset.visibility || "public";
    return `${actionLabel}\n\n平台：${platformLabel(row?.dataset.platform)}\n账号：${account}\n标题：${title}\n可见范围：${visibilityLabel(resolvedVisibility)}\n\n请确认以上信息无误。`;
  }

  function renderPlatformSchedule() {
    const rows = scheduleRows();
    ["douyin", "bilibili"].forEach((platform) => {
      const available = rows.filter((row) => (
        row.dataset.outputActive !== "false"
        && sectionAllows("schedule", row.dataset.status || "")
        && row.dataset.platform === platform
      ));
      const waiting = available.filter((row) => row.dataset.status === "WAITING").length;
      const scheduled = available.filter((row) => row.dataset.status === "SCHEDULED").length;
      const waitingNode = document.querySelector(`[data-platform-waiting="${platform}"]`);
      const scheduledNode = document.querySelector(`[data-platform-scheduled="${platform}"]`);
      if (waitingNode) waitingNode.textContent = String(waiting);
      if (scheduledNode) scheduledNode.textContent = String(scheduled);
    });
    document.querySelectorAll("[data-publish-platform]").forEach((button) => {
      const active = button.dataset.publishPlatform === activePlatform;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
      const action = button.querySelector(".platform-card-action");
      if (action) action.textContent = active ? "当前" : "切换";
    });
    let visibleCount = 0;
    rows.forEach((row) => {
      const visible = (
        row.dataset.outputActive !== "false"
        && sectionAllows("schedule", row.dataset.status || "")
        && row.dataset.platform === activePlatform
      );
      row.hidden = !visible;
      if (visible) visibleCount += 1;
    });
    document.querySelectorAll('[data-publish-row][data-section="content"]').forEach((row) => {
      row.hidden = (
        row.dataset.outputActive === "false"
        || !sectionAllows("content", row.dataset.status || "")
        || row.dataset.platform !== activePlatform
      );
    });
    syncContentTaskGroups();
    document.querySelectorAll("[data-account-row]").forEach((row) => {
      row.hidden = row.dataset.accountPlatform !== activePlatform;
    });
    document.querySelectorAll("[data-active-platform-label], [data-selection-platform]").forEach((node) => {
      node.textContent = platformLabel();
    });
    const accountForm = document.querySelector("[data-account-create]");
    if (accountForm?.elements.platform) accountForm.elements.platform.value = activePlatform;
    const accountPlatformLabel = accountForm?.querySelector("[data-account-create-platform]");
    if (accountPlatformLabel) accountPlatformLabel.textContent = platformLabel();
    filterAccountOptions(document.querySelector("[data-batch-account]"), activePlatform);
    if (platformListTitle) platformListTitle.textContent = `${platformLabel()}任务清单`;
    if (scheduleEmpty) scheduleEmpty.hidden = visibleCount > 0;
    updateBackfillCoversButton();
    applyHistoryFilter();
  }

  function renderCalendar() {
    if (!calendarNode) return;
    const { year, month } = calendarMonth;
    if (calendarTitle) calendarTitle.textContent = `${year} 年 ${month} 月 · ${platformLabel()}`;
    const firstDay = new Date(Date.UTC(year, month - 1, 1));
    const mondayOffset = (firstDay.getUTCDay() + 6) % 7;
    const gridStart = new Date(Date.UTC(year, month - 1, 1 - mondayOffset));
    const todayParts = beijingDateParts();
    const todayKey = `${todayParts.year}-${String(todayParts.month).padStart(2, "0")}-${String(todayParts.day).padStart(2, "0")}`;
    const jobsByDate = scheduledJobsByDate();
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
      cell.classList.toggle("is-has-jobs", jobs.length > 0);
      cell.classList.toggle("is-selected", jobs.length > 0 && key === selectedCalendarDate);
      cell.setAttribute("aria-selected", jobs.length > 0 && key === selectedCalendarDate ? "true" : "false");
      if (jobs.length) {
        cell.dataset.calendarDate = key;
        cell.tabIndex = 0;
        cell.setAttribute("aria-label", `${calendarDateLabel(key)}，${jobs.length} 条排期，点击查看全部`);
      }
      const number = document.createElement("span");
      number.className = "calendar-day-number";
      number.textContent = String(cellDay);
      cell.appendChild(number);
      jobs.slice(0, 2).forEach((job) => {
        const chip = document.createElement("span");
        chip.className = "calendar-job-chip";
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
    renderCalendarDayDetail(jobsByDate);
  }

  function historyMonthKey() {
    return `${historyMonth.year}-${String(historyMonth.month).padStart(2, "0")}`;
  }

  function historyPanelIsActive() {
    return Boolean(document.querySelector('[data-center-panel="history"].active'));
  }

  function createHistoryButton(label, className, dataAttribute) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.dataset[dataAttribute] = "";
    return button;
  }

  function createHistoryVisibilitySelect(job, dataAttribute, ariaLabel) {
    const select = document.createElement("select");
    select.className = "compact-filter";
    select.dataset[dataAttribute] = "";
    select.setAttribute("aria-label", ariaLabel);
    [
      ["public", "公开"],
      ["friends", "好友可见"],
      ["private", "仅自己可见"],
    ].forEach(([value, label]) => {
      const option = new Option(label, value, false, String(job.visibility || "public") === value);
      select.add(option);
    });
    return select;
  }

  function appendRepairAccountSelect(actions, job) {
    const select = document.createElement("select");
    select.className = "compact-filter";
    select.dataset.repairAccountSelect = "";
    select.setAttribute("aria-label", "选择替代任务账号");
    select.add(new Option("请选择账号", ""));
    const source = document.querySelector("[data-batch-account]");
    Array.from(source?.options || []).forEach((option) => {
      if (!option.value || option.dataset.accountPlatform !== job.platform) return;
      const clone = new Option(option.textContent, option.value);
      select.add(clone);
    });
    actions.appendChild(select);
    return select;
  }

  function renderHistoryActions(actions, job) {
    const readiness = job.send_readiness || {};
    if (historyDeletedView) {
      actions.appendChild(createHistoryButton("恢复记录", "secondary-button", "historyRestore"));
      actions.appendChild(createHistoryButton("执行详情", "text-button", "viewEvents"));
      return;
    }

    if (job.status === "NEED_REVIEW" && readiness.repairable) {
      const readinessMessage = document.createElement("small");
      readinessMessage.className = `publish-send-readiness ${readiness.dispatch_ready ? "is-ready" : "is-blocked"}`;
      readinessMessage.dataset.readinessMessage = "";
      readinessMessage.textContent = readiness.message || "";
      actions.appendChild(readinessMessage);
      actions.appendChild(createHistoryVisibilitySelect(job, "repairVisibility", "替代任务可见范围"));
      if (readiness.action === "select_account") {
        appendRepairAccountSelect(actions, job);
        actions.appendChild(createHistoryButton("选择后修复并发送", "primary-button", "repairJob"));
      } else if (readiness.dispatch_ready) {
        actions.appendChild(createHistoryButton("修复并发送", "primary-button", "repairJob"));
      } else {
        const setup = createHistoryButton(setupActionLabel(readiness.action), "secondary-button", "sendSetup");
        setup.dataset.primarySendAction = "";
        actions.appendChild(setup);
      }
    }

    if (job.platform_url) {
      const link = document.createElement("a");
      link.className = "text-button";
      link.href = job.platform_url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "平台链接";
      actions.appendChild(link);
    }
    if (job.status === "FAILED") {
      actions.appendChild(createHistoryVisibilitySelect(job, "retryVisibility", "重试任务可见范围"));
      actions.appendChild(createHistoryButton("立即发送", "primary-button", "retryJob"));
    }
    if (job.status === "NEED_REVIEW") {
      actions.appendChild(createHistoryButton("打开创作者中心", "secondary-button", "openCreator"));
      actions.appendChild(createHistoryButton("标记已发布", "secondary-button", "markPublished"));
      actions.appendChild(createHistoryButton("标记失败", "secondary-button", "markFailed"));
    }
    if (job.is_user_removed) {
      actions.appendChild(createHistoryButton("重新加入内容准备", "secondary-button", "restoreJob"));
    }
    if (["PUBLISHED", "FAILED", "EXPORTED", "CANCELLED"].includes(job.status)) {
      actions.appendChild(createHistoryButton("删除记录", "text-button danger", "historyHide"));
    }
    actions.appendChild(createHistoryButton("执行详情", "text-button", "viewEvents"));
  }

  function renderHistoryRow(job) {
    const row = document.createElement("article");
    row.className = "publish-execution-row";
    row.dataset.publishRow = "";
    row.dataset.historyRecord = "";
    row.dataset.jobId = job.id;
    row.dataset.accountId = job.account_id || "";
    row.dataset.platform = job.platform;
    row.dataset.visibility = job.visibility || "public";
    row.dataset.status = job.status;
    row.dataset.section = "history";
    row.dataset.sendReadiness = JSON.stringify(job.send_readiness || {});

    const selectCell = document.createElement("label");
    selectCell.className = "publish-history-select";
    if (historyDeletedView || ["PUBLISHED", "FAILED", "EXPORTED", "CANCELLED"].includes(job.status)) {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = job.id;
      checkbox.dataset.historySelect = "";
      checkbox.setAttribute("aria-label", `选择 ${job.title || "执行记录"}`);
      checkbox.checked = selectedHistoryJobIds.has(job.id);
      selectCell.appendChild(checkbox);
    } else {
      selectCell.textContent = "—";
    }

    const identity = document.createElement("div");
    identity.className = "publish-history-identity";
    const title = document.createElement("strong");
    title.dataset.rowTitle = "";
    title.textContent = job.title || "未命名任务";
    const platform = document.createElement("small");
    platform.textContent = job.platform_label || platformLabel(job.platform);
    identity.append(title, platform);
    const executionMessage = job.error_message || job.execution_phase_label || "";
    if (executionMessage) {
      const message = document.createElement("small");
      message.className = "publish-send-readiness";
      message.dataset.executionMessage = "";
      message.textContent = executionMessage;
      identity.appendChild(message);
    }
    if (job.error_message) {
      const details = document.createElement("details");
      details.className = "publish-error-detail";
      const summary = document.createElement("summary");
      summary.textContent = "查看错误";
      const errorText = document.createElement("p");
      errorText.textContent = job.error_message;
      const code = document.createElement("code");
      code.textContent = job.error_code || "";
      details.append(summary, errorText, code);
      identity.appendChild(details);
    }

    const account = document.createElement("span");
    account.dataset.rowAccount = "";
    account.textContent = job.account_name || "未选择账号";
    const scheduled = document.createElement("time");
    scheduled.dataset.rowSchedule = "";
    scheduled.dataset.utc = job.scheduled_at_utc || job.scheduled_at || "";
    scheduled.textContent = job.scheduled_at_display || "未排期";
    const started = document.createElement("time");
    started.textContent = `开始 ${job.started_at_display || "—"}`;
    const finished = document.createElement("time");
    finished.textContent = `结束 ${job.finished_at_display || "—"}`;
    const actualTimes = document.createElement("span");
    actualTimes.className = "publish-history-time-stack";
    actualTimes.append(started, finished);
    const status = document.createElement("span");
    status.className = `status-pill tone-${job.status_tone || "blue"}`;
    status.dataset.rowStatus = "";
    status.textContent = job.status_label || statusLabel(job.status);
    const actions = document.createElement("div");
    actions.className = "publish-row-actions publish-history-actions";
    const mode = document.createElement("small");
    mode.className = "publish-history-mode";
    mode.textContent = job.publish_mode_label || "";
    actions.appendChild(mode);
    renderHistoryActions(actions, job);
    row.append(selectCell, identity, account, scheduled, actualTimes, status, actions);
    applyRowReadiness(row);
    return row;
  }

  function updateHistorySelectionUi() {
    document.querySelectorAll("[data-history-select]").forEach((checkbox) => {
      checkbox.checked = selectedHistoryJobIds.has(checkbox.value);
    });
    const count = selectedHistoryJobIds.size;
    if (historySelectedCount) historySelectedCount.textContent = String(count);
    if (historyBatchBar) historyBatchBar.hidden = count === 0;
    if (historyBatchHideButton) historyBatchHideButton.hidden = historyDeletedView;
    if (historyBatchRestoreButton) historyBatchRestoreButton.hidden = !historyDeletedView;
  }

  function clearHistorySelection() {
    selectedHistoryJobIds.clear();
    updateHistorySelectionUi();
  }

  function renderHistoryRecords(data) {
    const jobs = data.jobs || [];
    const visibleIds = new Set(jobs.map((job) => job.id));
    Array.from(selectedHistoryJobIds).forEach((jobId) => {
      if (!visibleIds.has(jobId)) selectedHistoryJobIds.delete(jobId);
    });
    historyListNode?.replaceChildren(...jobs.map(renderHistoryRow));
    const pagination = data.pagination || {};
    historyPage = Number(pagination.page || 1);
    historyTotalPages = Number(pagination.total_pages || 0);
    const total = Number(pagination.total || 0);
    if (historyEmpty) historyEmpty.hidden = jobs.length > 0;
    if (historyPagination) historyPagination.hidden = historyTotalPages <= 1;
    if (historyPageSummary) {
      historyPageSummary.textContent = historyTotalPages
        ? `第 ${historyPage} / ${historyTotalPages} 页`
        : "暂无记录";
    }
    if (historyPreviousButton) historyPreviousButton.disabled = historyPage <= 1;
    if (historyNextButton) historyNextButton.disabled = !historyTotalPages || historyPage >= historyTotalPages;
    if (historyListTitle) {
      historyListTitle.textContent = historyDeletedView
        ? "已删除记录"
        : (historySelectedDate ? `${historySelectedDate} 执行记录` : "全部执行记录");
    }
    if (historyListEyebrow) historyListEyebrow.textContent = historyDeletedView ? "Deleted Records" : "History";
    if (historyListSummary) {
      historyListSummary.textContent = historyDeletedView
        ? `共 ${total} 条 · 删除仅影响页面展示`
        : `共 ${total} 条 · 页面及归档日期均使用北京时间`;
    }
    if (historyClearDateButton) historyClearDateButton.hidden = historyDeletedView || !historySelectedDate;
    updateHistorySelectionUi();
  }

  function renderHistoryCalendar(data) {
    if (!historyCalendarNode) return;
    const { year, month } = historyMonth;
    if (historyCalendarTitle) historyCalendarTitle.textContent = `${year} 年 ${month} 月 · ${platformLabel()}执行日历`;
    const dayMap = new Map((data.days || []).map((item) => [item.date, item]));
    const firstDay = new Date(Date.UTC(year, month - 1, 1));
    const mondayOffset = (firstDay.getUTCDay() + 6) % 7;
    const gridStart = new Date(Date.UTC(year, month - 1, 1 - mondayOffset));
    const todayParts = beijingDateParts();
    const todayKey = `${todayParts.year}-${String(todayParts.month).padStart(2, "0")}-${String(todayParts.day).padStart(2, "0")}`;
    const statusItems = [
      ["SCHEDULED", "待", "tone-blue"],
      ["PUBLISHING", "中", "tone-purple"],
      ["PUBLISHED", "成", "tone-green"],
      ["FAILED", "败", "tone-red"],
      ["NEED_REVIEW", "核", "tone-amber"],
      ["EXPORTED", "导", "tone-neutral"],
      ["CANCELLED", "取", "tone-neutral"],
    ];
    historyCalendarNode.replaceChildren();
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(gridStart.getTime() + index * 86400000);
      const cellYear = date.getUTCFullYear();
      const cellMonth = date.getUTCMonth() + 1;
      const cellDay = date.getUTCDate();
      const key = `${cellYear}-${String(cellMonth).padStart(2, "0")}-${String(cellDay).padStart(2, "0")}`;
      const day = dayMap.get(key);
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "publish-calendar-day publish-history-calendar-day";
      cell.dataset.historyDate = key;
      cell.setAttribute("role", "gridcell");
      cell.setAttribute("aria-label", `${key}${day ? `，共 ${day.total} 条执行记录` : "，没有执行记录"}`);
      cell.classList.toggle("is-outside", cellMonth !== month);
      cell.classList.toggle("is-today", key === todayKey);
      cell.classList.toggle("is-selected", key === historySelectedDate);
      const number = document.createElement("span");
      number.className = "calendar-day-number";
      number.textContent = String(cellDay);
      cell.appendChild(number);
      if (day?.total) {
        const total = document.createElement("small");
        total.className = "history-calendar-total";
        total.textContent = `共 ${day.total} 条`;
        cell.appendChild(total);
        const statuses = document.createElement("span");
        statuses.className = "history-calendar-statuses";
        statusItems.forEach(([status, label, tone]) => {
          const count = Number(day.counts?.[status] || 0);
          if (!count) return;
          const badge = document.createElement("small");
          badge.className = tone;
          badge.textContent = `${label}${count}`;
          statuses.appendChild(badge);
        });
        cell.appendChild(statuses);
      }
      historyCalendarNode.appendChild(cell);
    }
  }

  async function refreshHistory(options = {}) {
    if (!historyListNode || !historyPanelIsActive()) return;
    const includeCalendar = options.calendar !== false && !historyDeletedView;
    const includeRecords = options.records !== false;
    if (historyRefreshInFlight) {
      historyRefreshQueuedCalendar = historyRefreshQueuedCalendar || includeCalendar;
      historyRefreshQueuedRecords = historyRefreshQueuedRecords || includeRecords;
      return;
    }
    historyRefreshInFlight = true;
    const sequence = ++historyRequestSequence;
    const requests = [];
    if (includeCalendar) {
      const calendarUrl = `/api/publish/history/calendar?platform=${encodeURIComponent(activePlatform)}&month=${encodeURIComponent(historyMonthKey())}`;
      requests.push(
        window.apiFetch(calendarUrl).then((data) => {
          if (sequence === historyRequestSequence) renderHistoryCalendar(data);
        }),
      );
    }
    if (includeRecords) {
      const params = new URLSearchParams({
        platform: activePlatform,
        status: String(historyFilter?.value || "all"),
        deleted: historyDeletedView ? "true" : "false",
        page: String(historyPage),
        page_size: "50",
      });
      if (historySelectedDate && !historyDeletedView) params.set("date", historySelectedDate);
      requests.push(
        window.apiFetch(`/api/publish/history/records?${params.toString()}`).then((data) => {
          if (sequence === historyRequestSequence) renderHistoryRecords(data);
        }),
      );
    }
    try {
      await Promise.all(requests);
    } catch (error) {
      if (sequence === historyRequestSequence) showMessage(`加载执行记录失败：${error.message}`, "error");
    } finally {
      historyRefreshInFlight = false;
      if (historyRefreshQueuedCalendar || historyRefreshQueuedRecords) {
        const queuedCalendar = historyRefreshQueuedCalendar;
        const queuedRecords = historyRefreshQueuedRecords;
        historyRefreshQueuedCalendar = false;
        historyRefreshQueuedRecords = false;
        void refreshHistory({ calendar: queuedCalendar, records: queuedRecords });
      }
    }
  }

  function queueHistoryRefresh(includeCalendar = true) {
    if (!historyPanelIsActive()) return;
    historyRefreshCalendar = historyRefreshCalendar || includeCalendar;
    if (historyRefreshFrame) window.cancelAnimationFrame(historyRefreshFrame);
    historyRefreshFrame = window.requestAnimationFrame(() => {
      const refreshCalendar = historyRefreshCalendar;
      historyRefreshFrame = 0;
      historyRefreshCalendar = false;
      void refreshHistory({ calendar: refreshCalendar, records: true });
    });
  }

  async function updateHistoryVisibility(hidden, jobIds) {
    const ids = Array.from(new Set(jobIds.filter(Boolean)));
    if (!ids.length) return;
    const actionLabel = hidden ? "删除" : "恢复";
    const confirmation = hidden
      ? `确认安全删除 ${ids.length} 条执行记录？\n\n记录会从正常列表和日历中隐藏，但视频、执行明细、数据库历史和平台作品都不会删除。`
      : `确认恢复 ${ids.length} 条已删除记录？`;
    if (!window.confirm(confirmation)) return;
    try {
      const endpoint = hidden ? "hide" : "restore";
      const data = await window.apiFetch(`/api/publish/history/records/${endpoint}`, {
        method: "POST",
        body: JSON.stringify({ platform: activePlatform, job_ids: ids }),
      });
      clearHistorySelection();
      showMessage(data.message || `${actionLabel}成功。`, "success");
      await refreshHistory({ calendar: true, records: true });
    } catch (error) {
      showMessage(`${actionLabel}执行记录失败：${error.message}`, "error");
    }
  }

  function refreshScheduleViews() {
    sortScheduleRows();
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
    return {
      normal: "正常",
      invalid: "登录失效",
      login_pending: "等待登录完成",
      busy: "账号操作中",
      login_required: "需要重新登录",
    }[status] || "需要重新登录";
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
    if (!list) return null;
    const existing = list.querySelector(`[data-account-id="${CSS.escape(account.id)}"]`);
    if (existing) return existing;
    const article = document.createElement("article");
    article.dataset.accountRow = "";
    article.dataset.accountId = account.id;
    article.dataset.accountPlatform = account.platform;
    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = account.account_name;
    const platform = document.createElement("small");
    platform.textContent = account.platform_label;
    const message = document.createElement("small");
    message.dataset.accountMessage = "";
    identity.append(name, platform, message);
    const status = document.createElement("span");
    status.className = "status-pill tone-amber";
    status.dataset.accountStatus = "";
    const actions = document.createElement("div");
    actions.className = "button-row";
    article.append(identity, status, actions);
    list.append(article);
    return article;
  }

  function updateAccountRow(account) {
    if (!account?.id) return;
    const article = appendAccountRow(account);
    if (!article) return;
    article.dataset.accountPlatform = account.platform;
    article.hidden = account.platform !== activePlatform;
    const status = article.querySelector("[data-account-status]");
    if (status) {
      status.textContent = account.login_status_label || accountStatusLabel(account.login_status);
      status.classList.toggle("tone-green", account.login_status === "normal");
      status.classList.toggle("tone-red", account.login_status === "invalid");
      status.classList.toggle("tone-amber", !["normal", "invalid"].includes(account.login_status));
    }
    const message = article.querySelector("[data-account-message]");
    if (message) message.textContent = account.login_message || "";
    const actions = article.querySelector(".button-row");
    if (!actions) return;
    actions.replaceChildren();
    const primary = document.createElement("button");
    primary.type = "button";
    primary.className = "secondary-button";
    const secondary = document.createElement("button");
    secondary.type = "button";
    secondary.className = "text-button";
    if (account.login_status === "normal") {
      primary.dataset.accountOpenCenter = "";
      primary.textContent = "打开创作者中心";
      secondary.dataset.accountLogin = "";
      secondary.textContent = "重新登录";
    } else {
      primary.dataset.accountLogin = "";
      primary.textContent = account.login_status === "login_pending" ? "再次打开登录窗口" : "登录 / 重新登录";
      secondary.dataset.accountCheck = "";
      secondary.textContent = "检查状态";
    }
    actions.append(primary, secondary);
  }

  function sectionAllows(section, status) {
    if (section === "content") return ["DRAFT", "WAITING", "SCHEDULED"].includes(status);
    if (section === "schedule") return ["WAITING", "SCHEDULED"].includes(status);
    if (section === "history") {
      return ["SCHEDULED", "PUBLISHING", "PUBLISHED", "EXPORTED", "FAILED", "NEED_REVIEW", "CANCELLED"].includes(status);
    }
    return true;
  }

  function applyHistoryFilter() {
    queueHistoryRefresh(true);
  }

  function visibleTaskGroupRows(group) {
    if (!group) return [];
    return Array.from(group.querySelectorAll('[data-publish-row][data-section="content"]')).filter(
      (row) => !row.hidden && row.dataset.platform === activePlatform,
    );
  }

  function syncTaskGroupSelectionUi(group) {
    const checkbox = group?.querySelector("[data-task-group-select]");
    if (!checkbox) return;
    const rows = visibleTaskGroupRows(group);
    const selectedCount = rows.filter((row) => selectedJobIds.has(row.dataset.jobId)).length;
    const allSelected = rows.length > 0 && selectedCount === rows.length;
    checkbox.disabled = rows.length === 0;
    checkbox.checked = allSelected;
    checkbox.indeterminate = selectedCount > 0 && !allSelected;
    const label = group.querySelector("[data-task-group-select-label]");
    if (label) label.textContent = allSelected ? "取消全选" : "全选本任务";
  }

  function updateSelectionUi() {
    Array.from(selectedJobIds).forEach((jobId) => {
      const row = document.querySelector(`[data-publish-row][data-job-id="${CSS.escape(jobId)}"]`);
      if (!row || row.dataset.platform !== activePlatform) selectedJobIds.delete(jobId);
    });
    document.querySelectorAll("[data-publish-select]").forEach((checkbox) => {
      checkbox.checked = selectedJobIds.has(checkbox.value);
    });
    const count = selectedJobIds.size;
    if (selectedCountNode) selectedCountNode.textContent = String(count);
    if (drawerCount) drawerCount.textContent = String(count);
    if (selectionBar) selectionBar.hidden = count === 0;
    document.querySelectorAll("[data-publish-task-group]").forEach(syncTaskGroupSelectionUi);
  }

  function setActivePlatform(nextPlatform) {
    const next = nextPlatform === "bilibili" ? "bilibili" : "douyin";
    const changed = next !== activePlatform;
    activePlatform = next;
    if (changed) {
      selectedJobIds.clear();
      closeCalendarDayDetail();
      historySelectedDate = "";
      historyPage = 1;
      clearHistorySelection();
      invalidatePreview();
      closeDrawer();
      document.querySelectorAll("[data-publish-task-group]").forEach((group) => setTaskGroupExpanded(group, false));
      showMessage(`已切换到${platformLabel()}；之前勾选的任务已清空，不会跨平台发送。`, "success");
    }
    updateSelectionUi();
    refreshScheduleViews();
    if (changed) queueHistoryRefresh(true);
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
    if (tab === "history") void refreshHistory({ calendar: true, records: true });
  }

  function updateRowFromJob(job) {
    if (!job?.id) return;
    document.querySelectorAll(`[data-publish-row][data-job-id="${CSS.escape(job.id)}"]`).forEach((row) => {
      const status = String(job.status || row.dataset.status || "").toUpperCase();
      row.dataset.status = status;
      if (job.platform) row.dataset.platform = job.platform;
      if (job.account_id !== undefined) row.dataset.accountId = job.account_id || "";
      if (job.visibility) row.dataset.visibility = job.visibility;
      if (job.send_readiness) row.dataset.sendReadiness = JSON.stringify(job.send_readiness);
      if (job.output_is_active !== undefined) row.dataset.outputActive = job.output_is_active ? "true" : "false";
      row.hidden = (
        !sectionAllows(row.dataset.section, status)
        || row.dataset.platform !== activePlatform
        || (row.dataset.section !== "history" && row.dataset.outputActive === "false")
      );
      const statusNode = row.querySelector("[data-row-status]");
      if (statusNode) statusNode.textContent = job.status_label || statusLabel(status);
      const executionMessage = row.querySelector("[data-execution-message]");
      if (executionMessage) executionMessage.textContent = job.error_message || job.execution_phase_label || "";
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
        if (job.cover_file_path !== undefined && editor.elements.cover_file_path) {
          editor.elements.cover_file_path.value = job.cover_file_path || "";
        }
        if (job.cover_time_seconds !== undefined && editor.elements.cover_time_seconds) {
          editor.elements.cover_time_seconds.value = String(job.cover_time_seconds || 0);
        }
        syncPlatformFields(editor);
      }
      const coverPreview = row.querySelector("[data-cover-preview]");
      if (coverPreview && job.cover_media_url) {
        coverPreview.src = job.cover_media_url;
        coverPreview.hidden = false;
      }
      const readyNode = row.querySelector("[data-content-ready]");
      if (readyNode && job.content_complete !== undefined) {
        readyNode.textContent = job.content_complete ? "内容完整" : `缺少：${(job.missing_fields || []).join("、")}`;
        readyNode.classList.toggle("tone-green", Boolean(job.content_complete));
        readyNode.classList.toggle("tone-amber", !job.content_complete);
      }
      const contentScheduleBadge = row.querySelector("[data-content-schedule]");
      if (contentScheduleBadge) {
        const isScheduled = status === "SCHEDULED";
        const utcValue = job.scheduled_at_utc || job.scheduled_at || "";
        row.classList.toggle("is-scheduled", isScheduled);
        contentScheduleBadge.hidden = !isScheduled;
        const contentScheduleTime = contentScheduleBadge.querySelector("[data-content-schedule-time]");
        if (contentScheduleTime) {
          contentScheduleTime.textContent = job.scheduled_at_display || (utcValue ? formatBeijingTimestamp(utcValue) : "");
        }
        const addToPlanButton = row.querySelector("[data-add-to-plan]");
        if (addToPlanButton) addToPlanButton.textContent = isScheduled ? "调整排期" : "加入发布计划";
      }
      const restoreButton = row.querySelector("[data-restore-job]");
      if (restoreButton && job.is_user_removed !== undefined) restoreButton.hidden = !job.is_user_removed;
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
    updateBackfillCoversButton();
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
      platform: activePlatform,
      action,
      start_at_local: String(scheduleForm?.elements.start_at_local?.value || ""),
      timezone: APP_TIMEZONE,
      interval_minutes: preset === "custom" ? Number(scheduleForm?.elements.interval_minutes?.value || 180) : Number(preset),
      daily_start_time: String(scheduleForm?.elements.daily_start_time?.value || "07:00"),
      daily_end_time: String(scheduleForm?.elements.daily_end_time?.value || "00:00"),
    };
  }

  function previewSignature(payload) {
    return JSON.stringify({ ...payload, confirmed_schedule: undefined });
  }

  function showScheduleFeedback(message = "", tone = "info") {
    if (!scheduleFeedbackNode) return;
    scheduleFeedbackNode.hidden = !message;
    scheduleFeedbackNode.textContent = message;
    scheduleFeedbackNode.classList.toggle("tone-red", tone === "error");
    scheduleFeedbackNode.classList.toggle("tone-blue", tone !== "error");
  }

  function showLatestScheduleNote(message = "", tone = "info") {
    if (!latestScheduleNote) return;
    latestScheduleNote.hidden = !message;
    latestScheduleNote.textContent = message;
    latestScheduleNote.classList.toggle("tone-red", tone === "error");
    latestScheduleNote.classList.toggle("tone-blue", tone !== "error");
  }

  function invalidatePreview() {
    latestPreviewSignature = "";
    latestPreviewItems = [];
    if (confirmScheduleButton) confirmScheduleButton.disabled = true;
    if (previewList) previewList.innerHTML = '<p class="form-hint">请先生成预览，再确认应用。</p>';
    showScheduleFeedback();
  }

  function openDrawer() {
    if (!selectedJobIds.size) {
      showMessage("请先选择至少一条任务。", "error");
      return;
    }
    drawer.hidden = false;
    drawerBackdrop.hidden = false;
    document.body.classList.add("has-schedule-drawer");
    showLatestScheduleNote();
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
    if (platform && platform !== activePlatform) setActivePlatform(platform);
    accountDrawer.hidden = false;
    accountBackdrop.hidden = false;
    document.body.classList.add("has-schedule-drawer");
    const form = accountDrawer.querySelector("[data-account-create]");
    if (form?.elements.platform) form.elements.platform.value = activePlatform;
    const label = form?.querySelector("[data-account-create-platform]");
    if (label) label.textContent = platformLabel();
    document.querySelectorAll("[data-account-row]").forEach((row) => {
      row.hidden = row.dataset.accountPlatform !== activePlatform;
    });
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
        if (data.account) updateAccountRow(data.account);
        showMessage(data.message || data.worker_result?.message || "登录窗口已打开，请在专属 Chrome 中完成登录。", "success");
        await Promise.all([refreshAccounts(), refreshJobs()]);
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

  async function refreshAccounts() {
    try {
      const data = await window.apiFetch("/api/publish/accounts");
      (data.accounts || []).forEach(updateAccountRow);
    } catch (_error) {
      // 登录窗口仍可继续使用；下一轮会自动重试同步状态。
    }
  }

  async function refreshSchedulerHealth(showResult = false) {
    const button = document.querySelector("[data-refresh-worker]");
    if (button) button.disabled = true;
    try {
      const data = await window.apiFetch("/api/publish/scheduler/health");
      const statusNode = document.querySelector("[data-worker-status]");
      const runtimeNode = document.querySelector("[data-scheduler-runtime]");
      const message = document.querySelector("[data-worker-message]");
      const help = document.querySelector("[data-worker-help]");
      const dot = document.querySelector("[data-scheduler-health] .health-dot");
      workerAvailable = Boolean(data.worker_available);
      workerMessage = data.worker_message || "Windows 发布 Worker 未连接";
      const schedulerFailures = Number(data.consecutive_failures || 0);
      const schedulerHealthy = Boolean(data.running && schedulerFailures === 0);
      if (schedulerHealthNode) {
        schedulerHealthNode.dataset.workerAvailable = workerAvailable ? "true" : "false";
        schedulerHealthNode.dataset.schedulerFailures = String(schedulerFailures);
      }
      if (statusNode) statusNode.textContent = data.worker_available ? "正常" : "未连接";
      if (runtimeNode) runtimeNode.textContent = schedulerFailures ? "异常重试中" : (data.running ? "正常" : "已停止");
      if (message) {
        const schedulerMessage = schedulerFailures ? `${data.last_error_message || "调度扫描异常，正在自动重试"} · ` : "";
        message.textContent = `${schedulerMessage}${data.worker_message} · 页面及排期均使用北京时间`;
      }
      if (help) help.hidden = Boolean(data.worker_available);
      if (dot) dot.classList.toggle("is-ok", Boolean(schedulerHealthy && data.worker_available));
      document.querySelectorAll('[data-publish-row][data-section="schedule"], [data-publish-row][data-section="history"]').forEach((row) => applyRowReadiness(row));
      if (showResult) {
        const ready = schedulerHealthy && data.worker_available;
        const resultMessage = schedulerFailures
          ? (data.last_error_message || "调度扫描异常，正在自动重试")
          : (data.worker_available ? "调度器与 Windows Worker 均已连接。" : "发送服务仍在随 Docker 项目自动启动；请稍候，或在 Docker Desktop 中停止后重新运行本项目。");
        showMessage(resultMessage, ready ? "success" : "error");
      }
    } catch (error) {
      if (showResult) showMessage(`检测失败：${error.message}`, "error");
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.querySelectorAll("[data-center-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.centerTab));
  });
  document.querySelectorAll("[data-publish-platform]").forEach((button) => {
    button.addEventListener("click", () => setActivePlatform(button.dataset.publishPlatform));
  });
  document.querySelector("[data-calendar-previous]")?.addEventListener("click", () => {
    const previous = new Date(Date.UTC(calendarMonth.year, calendarMonth.month - 2, 1));
    calendarMonth = { year: previous.getUTCFullYear(), month: previous.getUTCMonth() + 1 };
    closeCalendarDayDetail();
    renderCalendar();
  });
  document.querySelector("[data-calendar-next]")?.addEventListener("click", () => {
    const next = new Date(Date.UTC(calendarMonth.year, calendarMonth.month, 1));
    calendarMonth = { year: next.getUTCFullYear(), month: next.getUTCMonth() + 1 };
    closeCalendarDayDetail();
    renderCalendar();
  });
  document.querySelector("[data-calendar-today]")?.addEventListener("click", () => {
    calendarMonth = currentBeijingMonth();
    closeCalendarDayDetail();
    renderCalendar();
  });
  calendarNode?.addEventListener("click", (event) => {
    const cell = event.target.closest("[data-calendar-date]");
    if (!cell) return;
    selectedCalendarDate = cell.dataset.calendarDate || "";
    renderCalendar();
    calendarDayDetail?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  calendarNode?.addEventListener("keydown", (event) => {
    const cell = event.target.closest("[data-calendar-date]");
    if (!cell || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    cell.click();
  });
  document.querySelector("[data-calendar-day-close]")?.addEventListener("click", closeCalendarDayDetail);
  calendarDayList?.addEventListener("click", (event) => {
    const item = event.target.closest("[data-calendar-detail-job]");
    if (item) focusScheduleRow(item.dataset.calendarDetailJob);
  });
  historyFilter?.addEventListener("change", () => {
    historyPage = 1;
    clearHistorySelection();
    void refreshHistory({ calendar: false, records: true });
  });
  document.querySelector("[data-history-calendar-previous]")?.addEventListener("click", () => {
    const previous = new Date(Date.UTC(historyMonth.year, historyMonth.month - 2, 1));
    historyMonth = { year: previous.getUTCFullYear(), month: previous.getUTCMonth() + 1 };
    historySelectedDate = "";
    historyPage = 1;
    clearHistorySelection();
    void refreshHistory({ calendar: true, records: true });
  });
  document.querySelector("[data-history-calendar-next]")?.addEventListener("click", () => {
    const next = new Date(Date.UTC(historyMonth.year, historyMonth.month, 1));
    historyMonth = { year: next.getUTCFullYear(), month: next.getUTCMonth() + 1 };
    historySelectedDate = "";
    historyPage = 1;
    clearHistorySelection();
    void refreshHistory({ calendar: true, records: true });
  });
  document.querySelector("[data-history-calendar-today]")?.addEventListener("click", () => {
    historyMonth = currentBeijingMonth();
    historySelectedDate = "";
    historyPage = 1;
    clearHistorySelection();
    void refreshHistory({ calendar: true, records: true });
  });
  historyCalendarNode?.addEventListener("click", (event) => {
    const cell = event.target.closest("[data-history-date]");
    if (!cell) return;
    const [year, month] = String(cell.dataset.historyDate || "").split("-").map(Number);
    if (year && month) historyMonth = { year, month };
    historySelectedDate = String(cell.dataset.historyDate || "");
    historyPage = 1;
    clearHistorySelection();
    void refreshHistory({ calendar: true, records: true });
  });
  historyClearDateButton?.addEventListener("click", () => {
    historySelectedDate = "";
    historyPage = 1;
    clearHistorySelection();
    void refreshHistory({ calendar: true, records: true });
  });
  document.querySelectorAll("[data-history-view]").forEach((button) => {
    button.addEventListener("click", () => {
      historyDeletedView = button.dataset.historyView === "deleted";
      document.querySelectorAll("[data-history-view]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      if (historyCalendarCard) historyCalendarCard.hidden = historyDeletedView;
      historySelectedDate = "";
      historyPage = 1;
      clearHistorySelection();
      void refreshHistory({ calendar: !historyDeletedView, records: true });
    });
  });
  historyPreviousButton?.addEventListener("click", () => {
    if (historyPage <= 1) return;
    historyPage -= 1;
    clearHistorySelection();
    void refreshHistory({ calendar: false, records: true });
  });
  historyNextButton?.addEventListener("click", () => {
    if (!historyTotalPages || historyPage >= historyTotalPages) return;
    historyPage += 1;
    clearHistorySelection();
    void refreshHistory({ calendar: false, records: true });
  });
  historyBatchHideButton?.addEventListener("click", () => {
    void updateHistoryVisibility(true, Array.from(selectedHistoryJobIds));
  });
  historyBatchRestoreButton?.addEventListener("click", () => {
    void updateHistoryVisibility(false, Array.from(selectedHistoryJobIds));
  });
  document.querySelector("[data-history-clear-selection]")?.addEventListener("click", clearHistorySelection);

  document.addEventListener("change", (event) => {
    const historyCheckbox = event.target.closest("[data-history-select]");
    if (historyCheckbox) {
      if (historyCheckbox.checked) selectedHistoryJobIds.add(historyCheckbox.value);
      else selectedHistoryJobIds.delete(historyCheckbox.value);
      updateHistorySelectionUi();
      return;
    }
    const taskGroupCheckbox = event.target.closest("[data-task-group-select]");
    if (taskGroupCheckbox) {
      const group = taskGroupCheckbox.closest("[data-publish-task-group]");
      visibleTaskGroupRows(group).forEach((row) => {
        if (taskGroupCheckbox.checked) selectedJobIds.add(row.dataset.jobId);
        else selectedJobIds.delete(row.dataset.jobId);
      });
      updateSelectionUi();
      return;
    }
    const checkbox = event.target.closest("[data-publish-select]");
    if (checkbox) {
      const row = checkbox.closest("[data-publish-row]");
      if (checkbox.checked && row?.dataset.platform !== activePlatform) {
        checkbox.checked = false;
        showMessage("当前平台与任务不一致，已阻止跨平台选择。", "error");
      } else if (checkbox.checked) selectedJobIds.add(checkbox.value); else selectedJobIds.delete(checkbox.value);
      updateSelectionUi();
    }
    const form = event.target.closest("[data-publish-editor]");
    if (form && (event.target.matches("[data-platform-select]") || event.target.matches("[data-copyright-select]"))) syncPlatformFields(form);
    if (event.target.closest("[data-schedule-form]")) invalidatePreview();
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-publish-editor]");
    if (form) {
      event.preventDefault();
      const row = form.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      const resultNode = form.querySelector("[data-editor-result]");
      const platform = String(row?.dataset.platform || form.elements.platform.value || "douyin");
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
        updateAccountRow(account);
        accountForm.reset();
        accountForm.elements.platform.value = activePlatform;
        const platformNode = accountForm.querySelector("[data-account-create-platform]");
        if (platformNode) platformNode.textContent = platformLabel();
        if (resultNode) resultNode.textContent = "账号已保存，请点击登录 / 重新登录。";
        showMessage("账号记录已保存；系统没有保存账号密码。", "success");
        await refreshJobs();
      } catch (error) {
        if (resultNode) resultNode.textContent = `保存失败：${error.message}`;
      }
    }
  });

  document.addEventListener("click", async (event) => {
    const taskGroupToggle = event.target.closest("[data-task-group-toggle]");
    if (taskGroupToggle) {
      const group = taskGroupToggle.closest("[data-publish-task-group]");
      setTaskGroupExpanded(group, group?.dataset.expanded !== "true");
      return;
    }

    const historyHide = event.target.closest("[data-history-hide]");
    if (historyHide) {
      const jobId = historyHide.closest("[data-history-record]")?.dataset.jobId;
      if (jobId) await updateHistoryVisibility(true, [jobId]);
      return;
    }

    const historyRestore = event.target.closest("[data-history-restore]");
    if (historyRestore) {
      const jobId = historyRestore.closest("[data-history-record]")?.dataset.jobId;
      if (jobId) await updateHistoryVisibility(false, [jobId]);
      return;
    }

    const setupButton = event.target.closest("[data-send-setup]");
    if (setupButton) {
      await handleSendSetup(setupButton.closest("[data-publish-row]"));
      return;
    }

    const dismissButton = event.target.closest("[data-dismiss-job]");
    if (dismissButton) {
      const row = dismissButton.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      const taskName = row?.closest("[data-publish-task-group]")?.querySelector("h3")?.textContent?.trim() || "未命名任务";
      const clipName = row?.querySelector("[data-row-title]")?.textContent?.trim() || "当前片段";
      const confirmation = [
        `确认把“${clipName}”移出${platformLabel(row?.dataset.platform)}内容准备？`,
        "",
        `所属任务：${taskName}`,
        "如果已经排期，排期会同时取消。原视频、裁剪成片、字幕和另一个平台的内容都不会删除。",
        "以后可以在“执行记录”中重新加入。",
      ].join("\n");
      if (!jobId || !window.confirm(confirmation)) return;
      dismissButton.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/dismiss`, { method: "POST" });
        updateRowFromJob(data.job);
        showMessage(data.message || "已从当前平台内容准备中移出。", "success");
      } catch (error) {
        showMessage(`移出失败：${error.message}`, "error");
      } finally { dismissButton.disabled = false; }
      return;
    }

    const repairButton = event.target.closest("[data-repair-job]");
    if (repairButton) {
      const row = repairButton.closest("[data-publish-row]");
      const sourceId = row?.dataset.jobId;
      const accountSelect = row?.querySelector("[data-repair-account-select]");
      const accountId = String(accountSelect?.value || "");
      const visibility = String(row?.querySelector("[data-repair-visibility]")?.value || row?.dataset.visibility || "public");
      if (accountSelect && !accountId) {
        showMessage("请先选择用于替代任务的同平台账号。", "error");
        accountSelect.focus();
        return;
      }
      if (!sourceId || !window.confirm(sendConfirmation(row, "确认转换并发送？原需复核记录会保留，系统只会创建一条同平台的 Windows Chrome 投稿任务。", visibility))) return;
      repairButton.disabled = true;
      try {
        const queryParams = new URLSearchParams({ visibility });
        if (accountId) queryParams.set("account_id", accountId);
        const query = `?${queryParams.toString()}`;
        const data = await window.apiFetch(`/api/publish/jobs/${sourceId}/repair-and-publish${query}`, { method: "POST" });
        if (!row?.matches("[data-history-record]")) cloneRowsForRetry(sourceId, data.job);
        showMessage(data.message || "旧记录已保留，替代任务已进入统一调度器。", "success");
        await refreshHistory({ calendar: true, records: true });
      } catch (error) {
        if (!applyReadinessError(error, row)) showMessage(`修复发送失败：${error.message}`, "error");
      } finally { repairButton.disabled = false; }
      return;
    }

    const publishNowButton = event.target.closest("[data-publish-now]");
    if (publishNowButton) {
      const publishRow = publishNowButton.closest("[data-publish-row]");
      const jobId = publishRow?.dataset.jobId;
      if (!jobId || !window.confirm(sendConfirmation(publishRow, "确认立即发送？任务会先进入 SCHEDULED，再由统一调度器执行真实投稿。"))) return;
      publishNowButton.disabled = true;
      try {
        const result = await window.apiFetch(`/api/publish/jobs/${jobId}/publish-now`, { method: "POST" });
        if (result.source_job_id && result.job?.id && result.job.id !== jobId) {
          cloneRowsForRetry(jobId, result.job);
          await refreshJobs();
        } else {
          updateRowFromJob(result.job || { id: jobId, status: "SCHEDULED" });
        }
        showMessage(result.message || "任务已按当前北京时间加入统一调度器。", "success");
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
        const data = await window.apiFetch("/api/publish/jobs/schedule-batch", { method: "PATCH", body: JSON.stringify({ job_ids: [jobId], platform: activePlatform, action: "clear", timezone: APP_TIMEZONE }) });
        (data.jobs || []).forEach(updateRowFromJob);
        showMessage(data.message, "success");
      } catch (error) { showMessage(`清除排期失败：${error.message}`, "error"); }
      return;
    }

    const cancelButton = event.target.closest("[data-cancel-job]");
    if (cancelButton) {
      const row = cancelButton.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      const confirmation = [
        "确认取消这条发送安排并返回“内容准备”？",
        "",
        "当前排期会清除；视频、标题、简介、话题和封面都会保留。",
      ].join("\n");
      if (!jobId || !window.confirm(confirmation)) return;
      cancelButton.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/cancel`, { method: "POST" });
        updateRowFromJob(data.job);
        selectedJobIds.delete(jobId);
        updateSelectionUi();
        syncContentTaskGroups();
        const contentRow = document.querySelector(
          `[data-publish-row][data-section="content"][data-job-id="${CSS.escape(jobId)}"]`,
        );
        setTaskGroupExpanded(contentRow?.closest("[data-publish-task-group]"), true);
        switchTab("content");
        contentRow?.scrollIntoView({ behavior: "smooth", block: "center" });
        showMessage(data.message || "已取消发送并返回内容准备。", "success");
      } catch (error) {
        showMessage(`取消发送失败：${error.message}`, "error");
      } finally {
        cancelButton.disabled = false;
      }
      return;
    }

    const restoreButton = event.target.closest("[data-restore-job]");
    if (restoreButton) {
      const row = restoreButton.closest("[data-publish-row]");
      const jobId = row?.dataset.jobId;
      if (!jobId || !window.confirm(`确认把这条${platformLabel(row?.dataset.platform)}内容重新加入“内容准备”？\n\n恢复后不会自动排期或发送。`)) return;
      restoreButton.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${jobId}/restore`, { method: "POST" });
        updateRowFromJob(data.job);
        showMessage(data.message || "已重新加入内容准备。", "success");
        await refreshHistory({ calendar: true, records: true });
      } catch (error) {
        showMessage(`恢复失败：${error.message}`, "error");
      } finally { restoreButton.disabled = false; }
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
      const planRow = addPlan.closest("[data-publish-row]");
      const jobId = planRow?.dataset.jobId;
      if (planRow?.dataset.platform !== activePlatform) {
        showMessage("当前平台与任务不一致，已阻止加入计划。", "error");
        return;
      }
      selectedJobIds.add(jobId);
      updateSelectionUi();
      switchTab("schedule");
      openDrawer();
      return;
    }

    const retryButton = event.target.closest("[data-retry-job]");
    if (retryButton) {
      const sourceId = retryButton.closest("[data-publish-row]")?.dataset.jobId;
      const retryRow = retryButton.closest("[data-publish-row]");
      const visibility = retryRow?.querySelector("[data-retry-visibility]")?.value || retryRow?.dataset.visibility || "public";
      if (!window.confirm(`${sendConfirmation(retryRow, "确认立即发送？", visibility)}\n\n系统会保留原失败记录，并创建一条立即执行的新任务。`)) return;
      retryButton.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${sourceId}/retry`, { method: "POST", body: JSON.stringify({ visibility }) });
        if (!retryRow?.matches("[data-history-record]")) cloneRowsForRetry(sourceId, data.job);
        showMessage("原失败记录已保留，新任务已进入统一调度器并开始立即发送。", "success");
        await refreshHistory({ calendar: true, records: true });
      } catch (error) {
        if (!applyReadinessError(error, retryRow)) showMessage(`立即发送失败：${error.message}`, "error");
      } finally {
        retryButton.disabled = false;
      }
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
        await refreshHistory({ calendar: true, records: true });
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
        await refreshHistory({ calendar: true, records: true });
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

    const accountAction = event.target.closest("[data-account-login], [data-account-check], [data-account-open-center]");
    if (accountAction) {
      const accountRow = accountAction.closest("[data-account-row]");
      const accountId = accountRow?.dataset.accountId;
      const action = accountAction.matches("[data-account-login]")
        ? "login"
        : (accountAction.matches("[data-account-open-center]") ? "open-center" : "check");
      accountAction.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/accounts/${accountId}/${action}`, { method: "POST" });
        const account = data.account || {};
        updateAccountRow(account);
        showMessage(data.message || data.worker_result?.message || "账号操作已执行。", "success");
        await Promise.all([refreshAccounts(), refreshJobs()]);
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

  document.querySelector("[data-apply-batch-target]")?.addEventListener("click", async () => {
    const payload = {
      job_ids: Array.from(selectedJobIds),
      platform: activePlatform,
      account_id: document.querySelector("[data-batch-account]").value,
      publish_mode: "local_browser",
    };
    try {
      const data = await window.apiFetch("/api/publish/jobs/target-batch", { method: "PATCH", body: JSON.stringify(payload) });
      (data.jobs || []).forEach(updateRowFromJob);
      showMessage(`已更新 ${data.updated_count} 条${platformLabel()}任务的账号。`, "success");
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

  backfillCoversButton?.addEventListener("click", async () => {
    const missingCount = missingCoverRows().length;
    if (!missingCount) {
      showMessage("当前没有需要补充封面的未发布任务。", "success");
      updateBackfillCoversButton();
      return;
    }
    backfillCoversButton.dataset.loading = "true";
    updateBackfillCoversButton();
    try {
      const data = await window.apiFetch(
        `/api/publish/covers/backfill?platform=${encodeURIComponent(activePlatform)}`,
        { method: "POST" },
      );
      (data.jobs || []).forEach(updateRowFromJob);
      const errorText = (data.errors || [])
        .slice(0, 3)
        .map((item) => `${item.output_file_name || item.output_clip_id}：${item.message}`)
        .join("；");
      showMessage(
        `${data.message || "封面补充完成。"}${errorText ? ` ${errorText}` : ""}`,
        data.status === "partial" ? "error" : "success",
      );
    } catch (error) {
      showMessage(`一键补充封面失败：${error.message}`, "error");
    } finally {
      backfillCoversButton.dataset.loading = "false";
      updateBackfillCoversButton();
    }
  });

  scheduleForm?.addEventListener("input", () => {
    invalidatePreview();
    showLatestScheduleNote();
  });

  scheduleForm?.elements.interval_preset?.addEventListener("change", () => {
    document.querySelector("[data-custom-interval]").hidden = scheduleForm.elements.interval_preset.value !== "custom";
  });

  latestScheduleButton?.addEventListener("click", async () => {
    const payload = schedulePayload("apply");
    const request = {
      job_ids: payload.job_ids,
      platform: payload.platform,
      timezone: payload.timezone,
      interval_minutes: payload.interval_minutes,
      daily_start_time: payload.daily_start_time,
      daily_end_time: payload.daily_end_time,
    };
    latestScheduleButton.disabled = true;
    latestScheduleButton.textContent = "正在查询当前最晚排期…";
    showLatestScheduleNote();
    showScheduleFeedback("正在读取当前平台的最新排期，请稍候。");
    try {
      const data = await window.apiFetch("/api/publish/schedules/next-start", {
        method: "POST",
        body: JSON.stringify(request),
      });
      invalidatePreview();
      if (data.status === "empty") {
        showLatestScheduleNote(data.message || "当前平台暂无其他未来排期，请手动选择时间。");
        return;
      }
      scheduleForm.elements.start_at_local.value = data.next_start_at_local;
      showLatestScheduleNote(
        `当前最晚：${data.latest_scheduled_at_local_display}；本次第 1 条：${data.next_start_at_local_display}`,
      );
    } catch (error) {
      showScheduleFeedback(`读取最晚排期失败：${error.message}`, "error");
    } finally {
      latestScheduleButton.disabled = false;
      latestScheduleButton.textContent = "接在当前平台最晚排期后";
    }
  });

  previewScheduleButton?.addEventListener("click", async () => {
    const payload = schedulePayload("apply");
    if (!payload.start_at_local || beijingInputToTimestamp(payload.start_at_local) <= Date.now()) {
      showScheduleFeedback("请选择晚于当前时间的北京时间。", "error");
      return;
    }
    previewScheduleButton.disabled = true;
    previewScheduleButton.textContent = "正在生成预览…";
    showScheduleFeedback("正在按北京时间计算每一条发布时间，请稍候。");
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
      showScheduleFeedback(`已生成 ${latestPreviewItems.length} 条具体发布时间，请核对后确认应用。`);
    } catch (error) {
      const row = document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(payload.job_ids[0] || "")}"]`);
      applyReadinessError(error, row);
      showScheduleFeedback(`排期预览失败：${error.message}`, "error");
    } finally {
      previewScheduleButton.disabled = false;
      previewScheduleButton.textContent = "预览排期";
    }
  });

  scheduleForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = schedulePayload("apply");
    if (latestPreviewSignature !== previewSignature(payload) || !latestPreviewItems.length) {
      showScheduleFeedback("排期参数已变化，请重新预览。", "error");
      return;
    }
    payload.confirmed_schedule = latestPreviewItems;
    confirmScheduleButton.disabled = true;
    confirmScheduleButton.textContent = "正在应用排期…";
    showScheduleFeedback("正在保存已确认的具体发布时间，请稍候。");
    try {
      const data = await window.apiFetch("/api/publish/jobs/schedule-batch", { method: "PATCH", body: JSON.stringify(payload) });
      (data.jobs || []).forEach(updateRowFromJob);
      const saved = (data.schedule || []).map((item, index) => `第 ${index + 1} 条：${item.scheduled_at_local_display}`).join("；");
      showMessage(`${data.message} ${saved}`, "success");
      selectedJobIds.clear(); updateSelectionUi(); closeDrawer(); invalidatePreview();
    } catch (error) {
      const row = document.querySelector(`[data-publish-row][data-section="schedule"][data-job-id="${CSS.escape(payload.job_ids[0] || "")}"]`);
      applyReadinessError(error, row);
      showScheduleFeedback(`排期保存失败：${error.message}`, "error");
      confirmScheduleButton.disabled = false;
    } finally {
      confirmScheduleButton.textContent = "确认应用具体时间";
    }
  });

  document.querySelector("[data-supplement-publish-jobs]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget; button.disabled = true;
    try {
      const data = await window.apiFetch(`/api/publish/queue/refresh?use_ai=false&platform=${encodeURIComponent(activePlatform)}`, { method: "POST" });
      showMessage(data.message || "缺失任务已补充，请稍后查看内容准备区。", "success");
    } catch (error) { showMessage(`补充任务失败：${error.message}`, "error"); }
    finally { button.disabled = false; }
  });

  document.querySelectorAll("[data-publish-editor]").forEach(syncPlatformFields);
  const focus = document.querySelector("[data-publish-focus]");
  if (focus?.dataset.platform) setActivePlatform(focus.dataset.platform);
  if (focus?.dataset.tab) switchTab(focus.dataset.tab);
  filterAccountOptions(document.querySelector("[data-batch-account]"), activePlatform);
  if (scheduleForm?.elements.start_at_local) scheduleForm.elements.start_at_local.value = beijingDatetimeValue(Date.now() + 10 * 60 * 1000);
  document.querySelectorAll('[data-publish-row][data-section="schedule"], [data-publish-row][data-section="history"]').forEach((row) => applyRowReadiness(row));
  updateSelectionUi(); applyHistoryFilter(); refreshScheduleViews(); updateBackfillCoversButton();
  if (focus?.dataset.taskId) {
    const group = document.querySelector(
      `[data-publish-task-group][data-task-id="${CSS.escape(focus.dataset.taskId)}"]`,
    );
    if (group && !group.hidden) {
      setTaskGroupExpanded(group, true);
      group.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      showMessage("已定位到该处理任务，但当前平台没有可准备的新版本内容。可切换平台或返回任务页重新同步。");
    }
  }
  window.setInterval(() => { refreshJobs(); refreshAccounts(); refreshSchedulerHealth(); }, 5000);
}
