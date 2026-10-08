(() => {
  "use strict";
  const byId = id => document.getElementById(id);
  const focusable = root => Array.from(root.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')).filter(node => !node.closest('[hidden]') && node.getClientRects().length);
  const menu = byId('studio-navigation');
  const menuToggle = byId('studio-menu-toggle');
  const menuBackdrop = document.querySelector('[data-close-navigation]');
  let menuReturnFocus = null;
  function closeNavigation() {
    document.body.classList.remove('studio-navigation-open');
    menuToggle?.setAttribute('aria-expanded', 'false');
    if (menuBackdrop) menuBackdrop.hidden = true;
    menuReturnFocus?.focus();
    menuReturnFocus = null;
  }
  function openNavigation() {
    menuReturnFocus = document.activeElement;
    document.body.classList.add('studio-navigation-open');
    menuToggle.setAttribute('aria-expanded', 'true');
    menuBackdrop.hidden = false;
    focusable(menu)[0]?.focus();
  }
  menuToggle?.addEventListener('click', () => menuToggle.getAttribute('aria-expanded') === 'true' ? closeNavigation() : openNavigation());
  menuBackdrop?.addEventListener('click', closeNavigation);
  window.matchMedia('(min-width: 901px)').addEventListener('change', event => { if (event.matches) closeNavigation(); });

  document.querySelectorAll('label.file-upload-button[for]').forEach(label => {
    label.setAttribute('role', 'button'); label.tabIndex = 0;
    label.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault(); byId(label.htmlFor)?.click();
    });
  });

  window.StudioUI = {
    confirm({ title = '确认操作', message = '', confirmLabel = '确认', danger = false } = {}) {
      return new Promise(resolve => {
        const dialog = document.createElement('dialog');
        dialog.className = 'studio-confirm-dialog';
        const heading = document.createElement('h2'); heading.textContent = title;
        const text = document.createElement('p'); text.textContent = message;
        const actions = document.createElement('div'); actions.className = 'button-row';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'secondary-button'; cancel.textContent = '取消';
        const confirm = document.createElement('button'); confirm.type = 'button'; confirm.className = 'primary-button'; confirm.textContent = confirmLabel;
        if (danger) confirm.classList.add('danger-button');
        heading.id = 'studio-confirm-title'; dialog.setAttribute('aria-labelledby', heading.id);
        actions.append(cancel, confirm); dialog.append(heading, text, actions); document.body.append(dialog);
        const previous = document.activeElement;
        let accepted = false;
        cancel.addEventListener('click', () => dialog.close());
        confirm.addEventListener('click', () => { accepted = true; dialog.close(); });
        dialog.addEventListener('close', () => { dialog.remove(); if (previous?.isConnected) previous.focus(); resolve(accepted); }, { once: true });
        dialog.showModal(); cancel.focus();
      });
    },
  };

  // Existing workspaces keep their close handlers; this layer adds keyboard focus behavior.
  let activeOverlay = null;
  let overlayReturnFocus = null;
  const overlaySelectors = '.modal-backdrop:not([hidden]) [role="dialog"],.schedule-drawer:not([hidden]),.account-drawer:not([hidden]),.transcript-drawer:not([hidden]),[data-studio-dialog]:not([hidden])';
  function syncOverlay() {
    const candidates = Array.from(document.querySelectorAll(overlaySelectors)).filter(node => node.getClientRects().length);
    const next = candidates.at(-1) || null;
    if (next === activeOverlay) return;
    if (!next) {
      if (overlayReturnFocus?.isConnected && overlayReturnFocus.getClientRects().length) overlayReturnFocus.focus();
      activeOverlay = null; overlayReturnFocus = null; return;
    }
    if (!activeOverlay) overlayReturnFocus = document.activeElement;
    activeOverlay = next;
    next.setAttribute('role', 'dialog'); next.setAttribute('aria-modal', 'true');
    if (!next.hasAttribute('aria-label') && !next.hasAttribute('aria-labelledby')) next.setAttribute('aria-label', next.querySelector('h2,h3')?.textContent || '编辑面板');
    if (!next.contains(document.activeElement)) focusable(next)[0]?.focus();
  }
  let overlayFrame = 0;
  new MutationObserver(records => {
    if (!records.some(record => record.type === 'attributes' && (record.attributeName === 'hidden' || record.attributeName === 'class'))) return;
    if (!overlayFrame) overlayFrame = requestAnimationFrame(() => { overlayFrame = 0; syncOverlay(); });
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['hidden', 'class'] });
  document.addEventListener('keydown', event => {
    const navigationOpen = document.body.classList.contains('studio-navigation-open');
    const root = navigationOpen ? menu : activeOverlay;
    if (!root || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') {
      if (navigationOpen) { event.preventDefault(); closeNavigation(); }
      else {
        const close = root.querySelector('[aria-label^="关闭"],[data-close-schedule-drawer],[data-close-account-drawer],#close-ai-config,#cancel-cut-edit');
        if (close) { event.preventDefault(); close.click(); }
      }
    } else if (event.key === 'Tab') {
      const nodes = focusable(root);
      const first = nodes[0], last = nodes.at(-1);
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }
  });

  const search = byId('studio-task-search');
  const results = byId('studio-search-results');
  let searchTimer = 0, searchController = null, searchGeneration = 0;
  function closeSearch() { window.clearTimeout(searchTimer); ++searchGeneration; searchController?.abort(); if (results) results.hidden = true; search?.setAttribute('aria-expanded', 'false'); }
  async function searchTasks() {
    const query = search.value.trim();
    const generation = ++searchGeneration;
    searchController?.abort();
    if (!query) { closeSearch(); return; }
    searchController = new AbortController();
    results.hidden = false; search.setAttribute('aria-expanded', 'true');
    const status = results.querySelector('[role="status"]');
    const items = results.querySelector('[data-search-items]');
    status.textContent = '正在搜索…'; items.replaceChildren();
    try {
      const payload = await window.apiFetch(`/api/ui/tasks?q=${encodeURIComponent(query)}`, { signal: searchController.signal });
      if (generation !== searchGeneration) return;
      const tasks = payload.tasks || [];
      status.textContent = tasks.length ? `找到 ${payload.pagination?.total ?? tasks.length} 个制作任务` : '没有匹配的制作任务，请换个关键词。';
      for (const task of tasks.slice(0, 6)) {
        const link = document.createElement('a'); link.className = 'studio-search-result'; link.href = `/tasks/${encodeURIComponent(task.id)}`;
        const title = document.createElement('strong'); title.textContent = task.title;
        const note = document.createElement('small'); note.textContent = [task.platform_label, task.ui?.stage_label || task.status_label].filter(Boolean).join(' · ');
        link.append(title, note); items.append(link);
      }
      const all = document.createElement('a'); all.className = 'studio-search-all'; all.href = `/tasks?q=${encodeURIComponent(query)}`; all.textContent = '在制作任务中查看全部结果'; items.append(all);
    } catch (error) { if (generation === searchGeneration && error.name !== 'AbortError') status.textContent = '搜索暂时不可用，请按回车进入任务列表重试。'; }
  }
  search?.addEventListener('input', () => { window.clearTimeout(searchTimer); ++searchGeneration; searchController?.abort(); searchTimer = window.setTimeout(searchTasks, 220); });
  search?.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' && !results.hidden) { event.preventDefault(); focusable(results)[0]?.focus(); }
    if (event.key === 'Escape') { event.preventDefault(); closeSearch(); }
  });
  results?.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); closeSearch(); search.focus(); }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); const nodes = focusable(results); const index = nodes.indexOf(document.activeElement);
      if (event.key === 'ArrowUp' && index === 0) search.focus(); else nodes[Math.max(0, Math.min(nodes.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus();
    }
  });
  document.addEventListener('click', event => { if (!event.target.closest('.studio-search')) closeSearch(); });

  function initializeTabs(group) {
    const buttons = Array.from(group.querySelectorAll('[data-studio-tab]'));
    if (!buttons.length) return;
    const key = group.dataset.tabQuery || 'tab';
    const values = new Set(buttons.map(button => button.dataset.studioTab));
    const activate = (value, updateUrl = false) => {
      if (!values.has(value)) value = buttons[0].dataset.studioTab;
      buttons.forEach(button => {
        const active = button.dataset.studioTab === value;
        button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
        const panel = byId(button.getAttribute('aria-controls')); if (panel) panel.hidden = !active;
      });
      if (updateUrl) { const url = new URL(location.href); url.searchParams.set(key, value); history.replaceState(null, '', url); }
      document.dispatchEvent(new CustomEvent('studio-tab-change', { detail: { group: group.id, value } }));
    };
    buttons.forEach(button => button.addEventListener('click', () => activate(button.dataset.studioTab, true)));
    group.addEventListener('keydown', event => {
      const index = buttons.indexOf(document.activeElement);
      if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      activate(buttons[next].dataset.studioTab, true); buttons[next].focus();
    });
    activate(new URLSearchParams(location.search).get(key) || group.dataset.defaultTab);
  }
  document.querySelectorAll('[data-studio-tabs]').forEach(initializeTabs);

  const planForm = byId('studio-plan-form');
  let plans = [], selectedPlan = null, savedPlan = '', plansLoaded = false, planBusy = false;
  const planStatus = byId('studio-plan-status');
  const planDirty = () => !!selectedPlan && JSON.stringify({ name: planForm.elements.name.value, prompt_text: planForm.elements.prompt_text.value }) !== savedPlan;
  function updatePlanState() {
    if (!planForm) return;
    byId('studio-plan-save').disabled = !selectedPlan || !planDirty() || planBusy;
    planForm.querySelectorAll('input,textarea').forEach(node => { node.disabled = !selectedPlan || planBusy; });
    document.querySelectorAll('[data-plan-id]').forEach(node => { node.disabled = planBusy; });
  }
  async function selectPlan(plan) {
    if (planBusy) return;
    if (planDirty() && !(await window.StudioUI.confirm({ title: '放弃尚未保存的方案修改？', message: '当前修改还没有保存。切换后将重新载入所选方案。', confirmLabel: '放弃并切换' }))) return;
    selectedPlan = plan; planForm.elements.name.value = plan.name; planForm.elements.prompt_text.value = plan.prompt_text || '';
    savedPlan = JSON.stringify({ name: plan.name, prompt_text: plan.prompt_text || '' });
    byId('studio-plan-meta').textContent = `方案 ${plan.slot} · ${plan.id} · 更新于 ${plan.updated_at || '尚未更新'}`;
    document.querySelectorAll('[data-plan-id]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.planId === plan.id)));
    planStatus.textContent = '已载入共享方案。修改后需点击“保存共享方案”，新任务选用更新内容。'; updatePlanState();
  }
  async function loadPlans() {
    if (!planForm || plansLoaded) return;
    plansLoaded = true; planStatus.textContent = '正在读取制作方案…';
    try {
      plans = await window.apiFetch('/api/ai-prompt-presets');
      const list = byId('studio-plan-list'); list.replaceChildren();
      plans.forEach(plan => { const button = document.createElement('button'); button.type = 'button'; button.className = 'studio-plan-choice'; button.dataset.planId = plan.id; button.setAttribute('aria-pressed', 'false'); const label = document.createElement('strong'); label.textContent = plan.name; const note = document.createElement('small'); note.textContent = `方案 ${plan.slot}${plan.is_default ? ' · 默认' : ''}`; button.append(label, note); button.addEventListener('click', () => selectPlan(plan)); list.append(button); });
      if (plans.length) await selectPlan(plans[0]); else planStatus.textContent = '暂时没有可编辑的制作方案。';
    } catch (error) { plansLoaded = false; planStatus.textContent = `读取失败：${error.message}。重新打开此标签可重试。`; }
  }
  document.addEventListener('studio-tab-change', event => { if (event.detail.group === 'studio-settings-tabs' && event.detail.value === 'plans') void loadPlans(); });
  if (planForm && !byId('studio-settings-plans').hidden) void loadPlans();
  planForm?.addEventListener('input', () => { planStatus.textContent = '有未保存的方案修改'; updatePlanState(); });
  planForm?.addEventListener('submit', async event => {
    event.preventDefault(); if (!planDirty() || planBusy || !planForm.reportValidity()) return;
    const payload = { name: planForm.elements.name.value.trim(), prompt_text: planForm.elements.prompt_text.value.trim() };
    planBusy = true; updatePlanState();
    if (!(await window.StudioUI.confirm({ title: '保存共享制作方案', message: '这会更新共享方案，供今后新建制作选用。已经冻结的任务继续使用原方案。当前生产策略的启用与回退仍须在改进试验中另行确认。', confirmLabel: '保存共享方案' }))) { planBusy = false; updatePlanState(); return; }
    planStatus.textContent = '正在保存…';
    try {
      const data = await window.apiFetch(`/api/ai-prompt-presets/${encodeURIComponent(selectedPlan.id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
      const index = plans.findIndex(plan => plan.id === selectedPlan.id); plans[index] = data.preset;
      selectedPlan = data.preset; planForm.elements.name.value = data.preset.name; planForm.elements.prompt_text.value = data.preset.prompt_text || ''; savedPlan = JSON.stringify({ name: data.preset.name, prompt_text: data.preset.prompt_text || '' });
      document.querySelector(`[data-plan-id="${CSS.escape(selectedPlan.id)}"] strong`).textContent = selectedPlan.name;
      byId('studio-plan-meta').textContent = `方案 ${selectedPlan.slot} · 更新于 ${selectedPlan.updated_at}`;
      planStatus.textContent = '共享方案已保存，已经冻结的任务保持原方案。';
    } catch (error) { planStatus.textContent = `保存失败：${error.message}。修改已保留，可重试。`; }
    finally { planBusy = false; updatePlanState(); }
  });
  let aiDirty = false;
  document.addEventListener('studio-ai-config-saved', () => { aiDirty = false; });
  byId('ai-config-form')?.addEventListener('input', () => { aiDirty = true; });
  new MutationObserver(() => { if (/配置.*已保存|配置保存成功|配置已更新|已保存.*配置/.test(byId('ai-config-result')?.textContent || '')) aiDirty = false; }).observe(byId('ai-config-result') || document.createElement('span'), { childList: true, subtree: true, characterData: true });
  window.addEventListener('beforeunload', event => { if (!planDirty() && !aiDirty) return; event.preventDefault(); event.returnValue = ''; });
})();
