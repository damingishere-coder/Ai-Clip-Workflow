(() => {
  'use strict';
  const byId = id => document.getElementById(id), form = byId('material-batch-form');
  if (!form) return;
  const key = 'niuma-material-batch-pending-v1', selected = new Set();
  let pending = null, busy = false, refreshGeneration = 0;
  const expandedBatches = new Set();
  const retryingImports = new Set();
  try { pending = JSON.parse(localStorage.getItem(key) || 'null'); } catch (_) { /* Show fresh selection. */ }
  if (pending && (!Array.isArray(pending.material_ids) || !pending.request_key || !pending.settings)) pending = null;
  const node = (tag, text) => { const element = document.createElement(tag); element.textContent = text; return element; };
  const field = name => form.elements.namedItem(name);
  if (pending) {
    for (const [name, value] of Object.entries(pending.settings)) {
      const input = field(name);
      if (input) input.type === 'checkbox' ? input.checked = value === true : input.value = String(value);
    }
    field('create_new_production').checked = pending.create_new_production === true;
    field('confirmed').checked = true;
  }
  function controls() {
    form.querySelectorAll('input,select').forEach(el => { el.disabled = busy || !!pending; });
    document.querySelectorAll('[data-batch-material]').forEach(el => { el.disabled = busy || !!pending; });
    byId('batch-create').disabled = busy || !!pending || !selected.size || !field('confirmed').checked;
    byId('batch-pending').hidden = !pending;
    byId('batch-pending-description').textContent = pending ? `已保留 ${pending.material_ids.length} 个素材的完整请求。刷新后仍可重试同一批次，配置保持原样。` : '';
    byId('batch-retry').disabled = busy;
    byId('batch-abandon').disabled = busy;
    const count = pending ? pending.material_ids.length : selected.size;
    byId('batch-selection-count').textContent = `已选 ${count} 个素材`;
    byId('material-selected-summary').textContent = `已选 ${count} 个`;
    byId('batch-create').textContent = count ? `确认创建批次 · ${count} 个视频` : '选择素材后创建批次';
    const checks = Array.from(document.querySelectorAll('[data-batch-material]'));
    checks.forEach(input => input.closest('[data-material-id]').classList.toggle('is-selected', input.checked));
    const all = byId('batch-select-page'), checked = checks.filter(input => input.checked).length;
    all.disabled = busy || !!pending || !checks.length;
    all.checked = !!checks.length && checked === checks.length;
    all.indeterminate = checked > 0 && checked < checks.length;
    byId('batch-clear-selection').disabled = busy || !!pending || !selected.size;
    byId('batch-subtitle-help').textContent = field('subtitle_strategy').value === 'review' ?
      '审片后进入字幕审核，确认字幕后再生成带字幕成片。不会自动排期或发布。' :
      '不新增或烧录字幕，已有字幕保留。AI 选片所需的语音转写仍会执行；成片仍需人工审核，字幕方式按此设置执行。';
    byId('batch-mode-help').textContent = field('auto_production').checked ?
      '自动制作至人工审片，不会自动排期或发布。' : '当前仅复制导入，后续可在任务中继续处理。';
  }
  function attachSelection() {
    document.querySelectorAll('[data-material-id]').forEach(card => {
      if (card.querySelector('[data-batch-material]')) return;
      const label = node('label', ''); label.className = 'material-check material-card-select';
      const input = document.createElement('input'); input.type = 'checkbox'; input.dataset.batchMaterial = card.dataset.materialId;
      input.checked = pending ? pending.material_ids.includes(card.dataset.materialId) : selected.has(card.dataset.materialId);
      input.addEventListener('change', () => { input.checked ? selected.add(card.dataset.materialId) : selected.delete(card.dataset.materialId); controls(); });
      input.setAttribute('aria-label', `选入批次：${card.querySelector('h3').textContent}`);
      label.append(input, node('span', '选入批次')); card.prepend(label);
    });
    controls();
  }
  byId('batch-select-page').addEventListener('change', event => {
    if (busy || pending) return;
    document.querySelectorAll('[data-batch-material]').forEach(input => {
      input.checked = event.target.checked;
      input.checked ? selected.add(input.dataset.batchMaterial) : selected.delete(input.dataset.batchMaterial);
    }); controls();
  });
  byId('batch-clear-selection').addEventListener('click', () => {
    if (busy || pending) return;
    selected.clear(); document.querySelectorAll('[data-batch-material]').forEach(input => { input.checked = false; }); controls();
  });
  window.addEventListener('material-library-updated', attachSelection);
  form.addEventListener('change', controls);
  form.addEventListener('invalid', event => { const section = event.target.closest('details'); if (section) section.open = true; }, true);
  field('selection_profile').addEventListener('change', event => { field('ai_prompt_preset_id').value = event.target.selectedOptions[0]?.dataset.prompt || ''; });
  async function refresh() {
    const generation = ++refreshGeneration;
    try {
      const response = await fetch('/api/material-batches?limit=20');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '批次读取失败');
      const requested = new URLSearchParams(location.search).get('batch');
      if (requested && !data.batches.some(batch => batch.id === requested)) {
        const pinnedResponse = await fetch(`/api/material-batches/${encodeURIComponent(requested)}`);
        const pinned = await pinnedResponse.json();
        if (!pinnedResponse.ok) throw new Error(pinned.detail || '指定批次读取失败');
        data.batches.unshift(pinned);
      }
      if (generation !== refreshGeneration) return;
      const list = byId('batch-list'); list.replaceChildren();
      for (const batch of data.batches) {
        const card = node('article', ''); card.className = 'material-batch-card'; card.dataset.batchId = batch.id;
        const profile = Array.from(field('selection_profile').options).find(option => option.value === batch.config.selection_profile);
        const imported = batch.items.filter(item => item.job_status === 'completed' && !item.is_deleted).length;
        const failed = batch.items.filter(item => !item.is_deleted && (['failed','cancelled'].includes(item.job_status) || /fail|error/i.test(item.task_status))).length;
        const deleted = batch.items.filter(item => item.is_deleted).length;
        const active = batch.items.length - deleted;
        const waitingReview = batch.items.filter(item => !item.is_deleted && ['pending_review','PENDING_SUBTITLE_REVIEW'].includes(item.task_status)).length;
        const processing = batch.items.filter(item => !item.is_deleted && /^(PREPARING_SOURCE|TRANSCRIBING|AI_ANALYZING|CLIP_SELECTING|VIDEO_CUTTING|audio_extracting|transcribing|ai_analyzing|cutting)$/.test(item.task_status)).length;
        const header = node('div', ''); header.className = 'material-batch-heading';
        const title = node('h3', profile?.textContent || batch.config.selection_profile);
        const badge = node('span', failed ? `${failed} 项需处理` : !active ? '任务已删除' : processing ? `${processing} 项制作中` : waitingReview ? `${waitingReview} 项待审核` : imported === active ? '导入完成' : '导入中');
        badge.className = `material-tag ${failed ? 'is-warning' : imported === batch.items.length ? 'is-verified' : ''}`;
        header.append(title, badge);
        const date = new Date(batch.created_at);
        const metadata = node('p', `${batch.items.length} 个视频 · ${Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}`);
        metadata.className = 'material-note';
        const progress = document.createElement('progress'); progress.max = active || 1; progress.value = imported;
        progress.setAttribute('aria-label', '视频导入进度');
        const summary = node('p', `已导入 ${imported} / ${active}${deleted ? ` · 已删除 ${deleted}` : ''} · ${batch.config.subtitle_strategy === 'review' ? '新增字幕' : '跳过字幕'}`);
        summary.className = 'material-note';
        const stages = node('div', ''); stages.className = 'material-batch-stages';
        const counts = new Map();
        batch.items.filter(item => !item.is_deleted && item.job_status === 'completed').forEach(item => {
          const label = item.task_status === 'pending_review' ? '待审片' : item.task_status_label;
          counts.set(label, (counts.get(label) || 0) + 1);
        });
        counts.forEach((count, label) => stages.append(node('span', `${label} ${count}`)));
        const details = node('details', ''); details.className = 'material-batch-details';
        if (requested === batch.id || expandedBatches.has(batch.id) || failed) details.open = true;
        details.addEventListener('toggle', () => { if (details.isConnected) details.open ? expandedBatches.add(batch.id) : expandedBatches.delete(batch.id); });
        details.append(node('summary', `查看 ${batch.items.length} 个任务`));
        const items = node('div', ''); items.className = 'material-batch-items';
        for (const item of batch.items) {
          const row = node('div', ''); row.className = 'material-batch-task';
          const link = node(item.is_deleted ? 'span' : 'a', item.file_name);
          if (!item.is_deleted) link.href = `/tasks/${encodeURIComponent(item.task_id)}`;
          const state = node('span', item.is_deleted ? '任务已删除' : item.job_status === 'completed' ? item.task_status_label : item.message || ({queued:'等待导入',running:'正在导入',failed:'导入失败',cancelled:'导入已取消'}[item.job_status] || item.job_status));
          state.className = 'material-task-status';
          row.append(link, state);
          if (item.error_message) { const error = node('p', item.error_message); error.className = 'material-task-error'; row.append(error); }
          items.append(row);
          if (!item.is_deleted && ['failed','cancelled'].includes(item.job_status)) {
            const retry = node('button', '重试导入'); retry.type = 'button'; retry.className = 'secondary-button';
            retry.dataset.retryImport = item.job_id; retry.disabled = retryingImports.has(item.job_id);
            retry.addEventListener('click', async () => {
              if (retryingImports.has(item.job_id)) return;
              retryingImports.add(item.job_id); retry.disabled = true;
              try {
                const response = await fetch(`/api/tasks/jobs/${encodeURIComponent(item.job_id)}/retry`, {method:'POST'});
                const result = await response.json();
                if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '导入重试未通过检查');
                byId('batch-status').textContent = `${item.file_name} 已重新排入导入队列，继续使用原任务和冻结配置。`;
                await refresh();
              } catch (error) { byId('batch-status').textContent = `${error.message}；请刷新导入进度后核对，未自动重试。`; }
              finally { retryingImports.delete(item.job_id); retry.disabled = false; }
            });
            row.append(retry);
          }
        }
        details.append(items); card.append(header, metadata, progress, summary, stages, details); list.append(card);
      }
      if (!data.batches.length) { const empty = node('p', '还没有制作批次。选好素材，创建你的第一批任务。'); empty.className = 'material-empty'; list.append(empty); }
    } catch (error) { if (generation === refreshGeneration) byId('batch-status').textContent = error.message; }
  }
  async function submitPending() {
    if (!pending || busy) return;
    busy = true; controls();
    try {
      const response = await fetch('/api/material-batches', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '请检查批次配置');
      localStorage.removeItem(key); pending = null; selected.clear(); field('confirmed').checked = false;
      document.querySelectorAll('[data-batch-material]').forEach(el => { el.checked = false; });
      byId('batch-status').textContent = `已创建 ${result.items.length} 个任务，等待逐个导入。重复请求已安全核对。`;
      await refresh();
    } catch (error) { byId('batch-status').textContent = `${error.message}；请求已保留，可刷新后重试。`; }
    finally { busy = false; controls(); }
  }
  form.addEventListener('submit', event => {
    event.preventDefault(); if (busy || pending || !selected.size || !field('confirmed').checked || !form.reportValidity()) return;
    const settings = {selection_profile:field('selection_profile').value, ai_prompt_preset_id:field('ai_prompt_preset_id').value,
      ai_provider:field('ai_provider').value, subtitle_strategy:field('subtitle_strategy').value,
      visual_enabled:field('visual_enabled').checked, auto_production:field('auto_production').checked};
    for (const name of ['candidate_clip_count','final_clip_target','max_clip_duration','highlight_density_per_hour']) settings[name] = Number(field(name).value);
    const request = {material_ids:Array.from(selected).sort(), settings, request_key:crypto.randomUUID(), confirmed:true, create_new_production:field('create_new_production').checked};
    try { localStorage.setItem(key, JSON.stringify(request)); pending = request; }
    catch (_) { byId('batch-status').textContent = '浏览器无法保存重试凭据，尚未提交。请允许本站本地存储后重试。'; return; }
    submitPending();
  });
  byId('batch-retry').addEventListener('click', submitPending);
  byId('batch-abandon').addEventListener('click', () => {
    localStorage.removeItem(key); pending = null;
    document.querySelectorAll('[data-batch-material]').forEach(input => { input.checked = selected.has(input.dataset.batchMaterial); }); controls();
    byId('batch-status').textContent = '仅清除本地重试记录。已提交到服务器的批次仍保留，请先查看最近批次。'; refresh();
  });
  byId('batch-refresh').addEventListener('click', refresh);
  attachSelection(); refresh(); controls();
})();
