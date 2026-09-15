(() => {
  'use strict';
  const byId = id => document.getElementById(id), form = byId('material-batch-form');
  if (!form) return;
  const key = 'niuma-material-batch-pending-v1', selected = new Set();
  let pending = null, busy = false;
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
    byId('batch-selection-count').textContent = `已选 ${selected.size} 个素材`;
  }
  function attachSelection() {
    document.querySelectorAll('[data-material-id]').forEach(card => {
      if (card.querySelector('[data-batch-material]')) return;
      const label = node('label', ''); label.className = 'material-check';
      const input = document.createElement('input'); input.type = 'checkbox'; input.dataset.batchMaterial = card.dataset.materialId;
      input.checked = selected.has(card.dataset.materialId);
      input.addEventListener('change', () => { input.checked ? selected.add(card.dataset.materialId) : selected.delete(card.dataset.materialId); controls(); });
      label.append(input, node('span', '选入批次')); card.prepend(label);
    });
    controls();
  }
  window.addEventListener('material-library-updated', attachSelection);
  form.addEventListener('change', controls);
  field('selection_profile').addEventListener('change', event => { field('ai_prompt_preset_id').value = event.target.selectedOptions[0]?.dataset.prompt || ''; });
  async function refresh() {
    try {
      const response = await fetch('/api/material-batches?limit=20');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '批次读取失败');
      const list = byId('batch-list'); list.replaceChildren();
      for (const batch of data.batches) {
        const card = node('article', ''); card.className = 'material-item'; card.dataset.batchId = batch.id;
        const profile = Array.from(field('selection_profile').options).find(option => option.value === batch.config.selection_profile);
        card.append(node('strong', `${batch.items.length} 个视频 · ${profile?.textContent || batch.config.selection_profile}`));
        for (const item of batch.items) {
          const row = node('p', ''), link = node('a', item.file_name); link.href = `/tasks/${encodeURIComponent(item.task_id)}`;
          row.append(link, node('span', ` · ${item.is_deleted ? '任务已删除' : item.job_status === 'completed' ? `已导入 · ${item.task_status_label}` : item.message || item.job_status}${item.error_message ? `：${item.error_message}` : ''}`)); card.append(row);
        }
        list.append(card);
      }
      if (!data.batches.length) list.append(node('p', '暂无批次。'));
    } catch (error) { byId('batch-status').textContent = error.message; }
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
    for (const name of ['candidate_clip_count','final_clip_target','max_clip_duration','highlight_density_per_hour','highlight_total_limit']) settings[name] = Number(field(name).value);
    const request = {material_ids:Array.from(selected).sort(), settings, request_key:crypto.randomUUID(), confirmed:true, create_new_production:field('create_new_production').checked};
    try { localStorage.setItem(key, JSON.stringify(request)); pending = request; }
    catch (_) { byId('batch-status').textContent = '浏览器无法保存重试凭据，尚未提交。请允许本站本地存储后重试。'; return; }
    submitPending();
  });
  byId('batch-retry').addEventListener('click', submitPending);
  byId('batch-abandon').addEventListener('click', () => {
    localStorage.removeItem(key); pending = null; controls();
    byId('batch-status').textContent = '仅清除本地重试记录。已提交到服务器的批次仍保留，请先查看最近批次。'; refresh();
  });
  byId('batch-refresh').addEventListener('click', refresh);
  attachSelection(); refresh(); controls();
})();
