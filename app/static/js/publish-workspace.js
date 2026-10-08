/* Focused editors, draft protection and review dialogs for the sending center. */
window.NiuMaPublishWorkspace = function ({ updateJob, validateCopy, saveExperiment, syncCounters, showMessage, revealRow }) {
  const drafts = new WeakMap();
  const backdrop = document.querySelector('[data-content-editor-backdrop]');
  let activeRow = null;
  let editorTrigger = null;
  const node = (tag, text) => { const item = document.createElement(tag); if (text !== undefined) item.textContent = text; return item; };
  const displayTime = value => { const date = new Date(value); return value && !Number.isNaN(date.getTime()) ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date) : (value || '未排期'); };
  const formFor = row => row?.querySelector('[data-publish-editor]');
  const signature = form => JSON.stringify(Array.from(form.elements).filter(item => item.name).map(item => [item.name, item.type === 'checkbox' ? item.checked : item.value]));
  function state(row) {
    const form = formFor(row);
    if (!form) return null;
    if (!drafts.has(form)) drafts.set(form, { signature: signature(form), version: row.dataset.updatedAt || '', saving: null });
    return drafts.get(form);
  }
  function isDirty(row) { const saved = state(row); return Boolean(saved && saved.signature !== signature(formFor(row))); }
  function isLocked(row) { return isDirty(row) || Boolean(state(row)?.saving); }
  function label(row, text) {
    row?.querySelectorAll('[data-editor-save-state], [data-summary-save-state], [data-editor-result]').forEach(item => { item.textContent = text; });
    if (row) row.dataset.dirty = String(isDirty(row));
  }
  function observe(row, job) {
    const form = formFor(row), saved = state(row);
    if (!form || !saved) return;
    row.dataset.savedCoverPath = job.cover_file_path || '';
    if (isLocked(row)) {
      if (job.updated_at && job.updated_at !== saved.version) {
        label(row, '有未保存修改 · 服务器版本已变化，请先核对');
        row.querySelector('[data-reconcile-editor]').hidden = false;
      }
      return;
    }
    for (const key of ['title', 'description', 'tags', 'visibility', 'cover_file_path', 'cover_time_seconds']) {
      if (job[key] !== undefined && form.elements[key]) form.elements[key].value = job[key] ?? '';
    }
    if (job.account_id !== undefined || job.effective_account_id !== undefined) form.elements.account_id.value = job.effective_account_id || job.account_id || job.send_readiness?.resolved_account_id || '';
    if (job.publish_mode !== undefined) form.elements.publish_mode.value = job.send_readiness?.resolved_publish_mode || job.publish_mode;
    if (job.allow_download !== undefined) form.elements.allow_download.checked = Boolean(job.allow_download);
    saved.version = job.updated_at || saved.version;
    row.dataset.updatedAt = saved.version;
    saved.signature = signature(form);
    syncCounters(form);
    label(row, '已保存');
    row.querySelector('[data-reconcile-editor]').hidden = true;
  }
  function content(row, suggestion = null, expected = null) {
    const form = formFor(row), saved = state(row);
    return {
      title: suggestion?.title ?? form.elements.title.value.trim(),
      description: suggestion?.description ?? form.elements.description.value.trim(),
      tags: suggestion?.tags ?? form.elements.tags.value.trim(),
      account_id: form.elements.account_id.value, publish_mode: form.elements.publish_mode.value,
      visibility: form.elements.visibility.value, cover_file_path: form.elements.cover_file_path.value,
      cover_time_seconds: Number(form.elements.cover_time_seconds.value || 0),
      allow_download: form.elements.allow_download.checked,
      expected_updated_at: expected ?? saved.version,
    };
  }
  function acceptCover(row, job) {
    const form = formFor(row), saved = state(row);
    // This window committed the cover. Advance only those baseline fields;
    // typing during the request remains a local unsaved change.
    const values = { cover_file_path: job.cover_file_path || '', cover_time_seconds: String(job.cover_time_seconds || 0) };
    for (const [key, value] of Object.entries(values)) form.elements[key].value = value;
    saved.signature = JSON.stringify(JSON.parse(saved.signature).map(([key, value]) => [key, values[key] ?? value]));
    saved.version = job.updated_at; row.dataset.updatedAt = saved.version;
    row.dataset.savedCoverPath = values.cover_file_path;
    updateJob(job);
    label(row, isDirty(row) ? '封面已保存 · 还有未保存修改' : '已保存');
  }
  async function save(row, { suggestion = null, expected = null, force = false } = {}) {
    const form = formFor(row), saved = state(row);
    if (!form) return true;
    if (saved.saving) return saved.saving;
    if (!force && !suggestion && !isDirty(row)) return true;
    const validation = suggestion ? '' : validateCopy(form);
    if (validation) { label(row, `保存失败：${validation}`); showMessage(validation, 'error'); return false; }
    const controls = Array.from(form.querySelectorAll('input,select,textarea,button'));
    const disabled = controls.map(item => item.disabled);
    controls.forEach(item => { item.disabled = true; });
    label(row, '正在保存…');
    const payload = content(row, suggestion, expected);
    saved.saving = (async () => {
      let contentCommitted = false;
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${encodeURIComponent(row.dataset.jobId)}/send-content`, { method: 'PATCH', body: JSON.stringify(payload) });
        contentCommitted = true;
        // Content is committed already. Keep that version even if optional assignment fails.
        for (const key of ['title', 'description', 'tags']) if (data.job[key] !== undefined) form.elements[key].value = data.job[key];
        saved.version = data.job.updated_at;
        row.dataset.updatedAt = saved.version;
        await saveExperiment(row, form);
        saved.signature = signature(form);
        updateJob(data.job);
        label(row, '已保存');
        row.querySelector('[data-reconcile-editor]').hidden = true;
        return true;
      } catch (error) {
        label(row, contentCommitted ? `文案与配置已保存，实验归属未保存：${error.message}` : (error.status === 409 ? '版本冲突 · 输入已保留，请核对服务器内容后重试' : `保存失败：${error.message}`));
        if (error.status === 409) row.querySelector('[data-reconcile-editor]').hidden = false;
        showMessage(`保存失败：${error.message}`, 'error');
        return false;
      } finally {
        controls.forEach((item, index) => { item.disabled = disabled[index]; });
        saved.saving = null;
        syncCounters(form);
      }
    })();
    return saved.saving;
  }
  async function saveIds(ids) {
    for (const id of ids) {
      const row = document.querySelector(`[data-section="content"][data-job-id="${CSS.escape(id)}"]`);
      if (row && !await save(row)) { openEditor(row); return false; }
    }
    return true;
  }
  function closeEditor() {
    if (!activeRow) return;
    activeRow.classList.remove('is-editing');
    activeRow.querySelectorAll('video').forEach(video => video.pause());
    const dialog = activeRow.querySelector('[data-content-editor-dialog]');
    activeRow = null; backdrop.hidden = true;
    if (dialog.open) dialog.close();
    document.body.classList.remove('has-publish-editor');
    editorTrigger?.focus();
  }
  function openEditor(row) {
    if (!row) return;
    revealRow?.(row);
    if (activeRow !== row) closeEditor();
    activeRow = row; editorTrigger = row.querySelector('[data-open-content-editor]');
    row.classList.add('is-editing'); backdrop.hidden = true;
    row.hidden = false;
    const group = row.closest('[data-publish-task-group]');
    if (group) { group.hidden = false; group.querySelector('[data-task-group-body]').hidden = false; }
    document.body.classList.add('has-publish-editor');
    const form = formFor(row), dialog = row.querySelector('[data-content-editor-dialog]');
    if (!dialog.open) dialog.showModal();
    dialog.onclose = () => { if (activeRow === row) closeEditor(); };
    state(row); form.querySelector('input[name="title"]')?.focus();
  }
  async function leaveEditor() {
    if (activeRow && !await save(activeRow)) return false;
    closeEditor(); return true;
  }
  function confirm(text, input = false, initial = '') {
    const dialog = document.querySelector('[data-publish-confirm]');
    if (dialog.open) return Promise.resolve(input ? null : false);
    dialog.querySelector('[data-confirm-copy]').textContent = text;
    const field = dialog.querySelector('[data-confirm-input]'); field.value = initial;
    dialog.querySelector('[data-confirm-input-wrap]').hidden = !input;
    dialog.returnValue = '';
    return new Promise(resolve => {
      dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm' ? (input ? field.value.trim() : true) : (input ? null : false)), { once: true });
      dialog.showModal();
    });
  }
  async function preview(ids) {
    const dialog = document.querySelector('[data-ai-preview-dialog]'), host = dialog.querySelector('[data-ai-preview-list]');
    host.replaceChildren(); dialog.showModal();
    for (const id of ids) {
      const row = document.querySelector(`[data-section="content"][data-job-id="${CSS.escape(id)}"]`);
      if (!row) continue;
      const form = formFor(row), item = node('article'); item.className = 'publish-ai-preview-item';
      const status = node('p', '正在生成本条建议…'); item.append(node('h3', form.elements.title.value), status); host.append(item);
      try {
        const data = await window.apiFetch(`/api/publish/jobs/${encodeURIComponent(id)}/metadata/preview`, { method: 'POST' });
        const comparison = node('div'); comparison.className = 'publish-ai-comparison';
        for (const [name, copy] of [['当前输入', content(row)], ['AI 建议', data.metadata]]) {
          const column = node('div'); column.append(node('strong', name), node('p', `标题：${copy.title}`), node('p', `简介：${copy.description}`), node('p', `话题：${copy.tags}`)); comparison.append(column);
        }
        const accept = node('button', '接受并保存本条'); accept.type = 'button'; accept.className = 'primary-button'; accept.dataset.acceptAiSuggestion = id;
        accept.addEventListener('click', async () => {
          if (isDirty(row) && !await confirm('接受建议会替换本条当前输入中的标题、简介和话题，并保存其余配置。确认接受吗？')) return;
          accept.disabled = true;
          const ok = await save(row, { suggestion: data.metadata, expected: data.expected_updated_at });
          status.textContent = ok ? '本条建议已接受并保存' : '保存未全部完成；请先核对编辑器中的错误提示';
          accept.disabled = ok;
        });
        status.textContent = '建议尚未保存'; item.append(comparison, accept);
      } catch (error) { status.textContent = `本条生成失败：${error.message}；原文保持不变`; }
      if (!dialog.open) break;
    }
  }
  function pickCover(row, current) {
    const dialog = document.querySelector('[data-cover-dialog]'), video = dialog.querySelector('video');
    const number = dialog.querySelector('[data-cover-time]'), range = dialog.querySelector('[data-cover-range]');
    video.src = row.querySelector('[data-content-video]')?.src || row.querySelector('video')?.src || '';
    number.value = current; range.value = current; dialog.returnValue = '';
    const update = seconds => { number.value = Number(seconds).toFixed(1); range.value = seconds; };
    video.onloadedmetadata = () => { range.max = video.duration || 1; number.max = video.duration || 1; video.currentTime = Math.min(current, video.duration || current); };
    video.preload = 'metadata'; video.load();
    video.ontimeupdate = () => update(video.currentTime);
    range.oninput = () => { video.pause(); video.currentTime = Number(range.value); update(range.value); };
    number.onchange = () => { video.pause(); video.currentTime = Math.max(0, Math.min(Number(number.value), Number(range.max))); };
    dialog.querySelector('[data-confirm-cover]').onclick = () => dialog.close('confirm');
    return new Promise(resolve => {
      dialog.addEventListener('close', () => { video.pause(); resolve(dialog.returnValue === 'confirm' ? Number(number.value) : null); }, { once: true });
      dialog.showModal();
    });
  }
  function events(items) {
    const dialog = document.querySelector('[data-events-dialog]'), host = dialog.querySelector('[data-events-list]'); host.replaceChildren();
    const labels = { DRAFT: '草稿', WAITING: '等待安排', SCHEDULED: '已排期', PUBLISHING: '发送中', PUBLISHED: '已发布', EXPORTED: '已导出', FAILED: '失败', NEED_REVIEW: '待人工复核', CANCELLED: '已取消' };
    for (const event of items) { const row = node('article'); row.append(node('time', `${displayTime(event.occurred_at)} · 北京时间`), node('strong', `${labels[event.from_status] || event.from_status || '—'} → ${labels[event.to_status] || event.to_status || '—'}`), node('p', event.message || event.event_type)); host.append(row); }
    if (!items.length) host.append(node('p', '暂无执行事件。'));
    dialog.showModal();
  }
  let policyPreview = null, policyPayload = null, policyRefresh = null;
  async function previewPolicy(policy, refresh) {
    const payload = { enabled: !policy.enabled, include_existing: !policy.enabled, daily_limit: policy.daily_limit, min_gap_minutes: policy.min_gap_minutes, daily_start_time: policy.daily_start_time, daily_end_time: policy.daily_end_time };
    policyPayload = payload; policyRefresh = refresh;
    const data = await window.apiFetch(`/api/publish/schedules/adaptive/${encodeURIComponent(policy.account_id)}/preview`, { method: 'POST', body: JSON.stringify(payload) });
    policyPreview = data;
    const dialog = document.querySelector('[data-policy-dialog]'), host = dialog.querySelector('[data-policy-impact]');
    dialog.querySelector('[data-policy-summary]').textContent = `康熙来了 · ${payload.enabled ? '启用动态策略' : '停用动态策略'}；未来受管 ${data.managed_count} 条，其中新纳入 ${data.newly_managed_count} 条，${data.protected_count} 条保持保护。${data.message}`;
    host.replaceChildren();
    for (const item of data.schedule || []) host.append(node('p', `${item.title}：${displayTime(item.old_time)} → ${item.scheduled_at_local_display || displayTime(item.scheduled_at_utc)}`));
    if (data.protected?.length) {
      const details = node('details'); details.append(node('summary', `查看 ${data.protected_count} 条受保护记录`));
      for (const item of data.protected) details.append(node('p', `${item.title}：${item.reason}`));
      host.append(details);
    }
    if (!data.schedule?.length) host.append(node('p', '本次没有拟调整时间。当天、人工固定、已执行和待复核记录保持保护。'));
    dialog.querySelector('[data-policy-confirm]').checked = false; dialog.querySelector('[data-apply-policy]').disabled = true;
    dialog.querySelector('[data-policy-feedback]').textContent = ''; dialog.showModal();
  }
  document.querySelector('[data-policy-confirm]').addEventListener('change', event => { document.querySelector('[data-apply-policy]').disabled = !event.target.checked; });
  document.querySelector('[data-apply-policy]').addEventListener('click', async event => {
    if (!policyPreview || !document.querySelector('[data-policy-confirm]').checked) return;
    const button = event.currentTarget; button.disabled = true;
    try {
      await window.apiFetch(`/api/publish/schedules/adaptive/${encodeURIComponent(policyPreview.account_id)}/apply-preview`, { method: 'POST', body: JSON.stringify({ ...policyPayload, preview_token: policyPreview.preview_token, confirmed: true }) });
      document.querySelector('[data-policy-dialog]').close(); await policyRefresh(); showMessage('策略已按本次确认保存。');
    } catch (error) { document.querySelector('[data-policy-feedback]').textContent = `${error.message}；请关闭后重新生成影响预览。`; }
  });
  let scheduleView = 'list';
  function setScheduleView(view) {
    scheduleView = view;
    document.querySelector('[data-schedule-calendar-card]').hidden = view !== 'calendar';
    document.querySelector('.publish-plan-list').hidden = view === 'calendar';
    document.querySelector('.publish-plan-header').hidden = view === 'calendar';
    document.querySelectorAll('[data-schedule-view]').forEach(button => { button.classList.toggle('is-active', button.dataset.scheduleView === view); button.setAttribute('aria-pressed', String(button.dataset.scheduleView === view)); });
  }
  function dateGroups() {
    const list = document.querySelector('.publish-plan-list'); list.querySelectorAll('[data-date-heading]').forEach(item => item.remove());
    let previous = '';
    for (const row of list.querySelectorAll('[data-section="schedule"]')) {
      if (row.hidden) continue;
      const time = row.querySelector('[data-row-schedule]');
      const date = time.dataset.utc ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(time.dataset.utc)) : '尚未排期';
      if (date !== previous) { const title = node('h3', date); title.dataset.dateHeading = ''; title.className = 'publish-date-heading'; list.insertBefore(title, row); previous = date; }
    }
    setScheduleView(scheduleView);
  }
  document.querySelectorAll('[data-schedule-view]').forEach(button => button.addEventListener('click', () => setScheduleView(button.dataset.scheduleView)));
  document.addEventListener('input', event => { const form = event.target.closest('[data-publish-editor]'); if (form) { const row = form.closest('[data-publish-row]'); label(row, isDirty(row) ? '有未保存修改' : '已保存'); } });
  document.addEventListener('change', event => { const form = event.target.closest('[data-publish-editor]'); if (form) { const row = form.closest('[data-publish-row]'); label(row, isDirty(row) ? '有未保存修改' : '已保存'); } });
  document.addEventListener('click', async event => {
    const reconcile = event.target.closest('[data-reconcile-editor]');
    if (reconcile) {
      const row = reconcile.closest('[data-publish-row]'); reconcile.disabled = true;
      try {
        const data = await window.apiFetch(`/api/publish/jobs?job_ids=${encodeURIComponent(row.dataset.jobId)}`), job = data.jobs?.[0];
        if (!job) throw new Error('发送记录已不存在');
        if (!['DRAFT', 'WAITING', 'SCHEDULED'].includes(job.status)) throw new Error('记录已进入执行或历史阶段，当前不能覆盖内容');
        if (await confirm(`服务器当前标题：${job.title}\n简介：${job.description}\n话题：${job.tags}\n\n你的输入：${formFor(row).elements.title.value}\n\n确认以这个服务器版本作为保存基准？输入会保留，之后仍须点击保存。`)) {
          state(row).version = job.updated_at; row.dataset.updatedAt = job.updated_at;
          label(row, '已核对服务器版本 · 当前输入尚未保存'); reconcile.hidden = true;
        }
      } catch (error) { label(row, error.message); }
      finally { reconcile.disabled = false; }
    }
    const open = event.target.closest('[data-open-content-editor]'); if (open) openEditor(open.closest('[data-publish-row]'));
    if (event.target.closest('[data-close-content-editor]') || event.target === backdrop) closeEditor();
    for (const [hook, dialog] of [['data-close-ai-preview', 'data-ai-preview-dialog'], ['data-close-cover', 'data-cover-dialog'], ['data-close-events', 'data-events-dialog'], ['data-close-policy', 'data-policy-dialog']]) if (event.target.closest(`[${hook}]`)) document.querySelector(`[${dialog}]`).close();
  });
  document.addEventListener('keydown', event => {
    if (!activeRow || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') { event.preventDefault(); closeEditor(); }
    if (event.key === 'Tab') {
      const controls = Array.from(formFor(activeRow).querySelectorAll('input,select,textarea,button,a[href]')).filter(item => !item.disabled && item.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  window.addEventListener('beforeunload', event => { if (Array.from(document.querySelectorAll('[data-section="content"]')).some(isLocked)) { event.preventDefault(); event.returnValue = ''; } });
  document.querySelectorAll('[data-section="content"]').forEach(state);
  return { save, saveIds, isDirty, isLocked, observe, acceptCover, version: row => state(row)?.version, openEditor, closeEditor, leaveEditor, confirm, preview, pickCover, events, previewPolicy, dateGroups, setScheduleView };
};
