(() => {
  'use strict';
  const form = document.getElementById('challenger-draft-form');
  if (!form) return;
  const byId = id => document.getElementById(`challenger-${id}`);
  const reportId = form.dataset.reportId;
  const status = byId('status'), save = byId('save'), load = byId('load');
  const profile = byId('profile'), preset = byId('preset'), prompt = byId('prompt');
  let context = null, pending = null, sequence = 0, dirty = false;
  function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
  async function request(url, options) {
    const response = await fetch(url, options), data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查草稿内容');
    return data;
  }
  function show(draft) {
    const evidence = draft.evidence, detail = byId('detail');
    detail.replaceChildren(node('h3', evidence.name), node('p', evidence.hypothesis), node('p', evidence.notice));
    detail.append(node('p', `Profile：${evidence.baseline.profile.name} · ${evidence.baseline.rules_version}。Profile 与评分未改变；仅 Prompt 正文不同。`));
    const diff = node('pre', evidence.diff); diff.className = 'challenger-evidence'; detail.append(diff);
    const link = node('a', '查看版本与来源证据'); link.href = `/api/content-review/challengers/${encodeURIComponent(draft.id)}`; link.target = '_blank'; link.rel = 'noopener'; detail.append(link);
    if (['draft', 'trial'].includes(draft.status)) {
      const trial = node('a', '使用此草稿创建试验任务'); trial.className = 'secondary-button';
      trial.href = `/tasks/new?challenger_id=${encodeURIComponent(draft.id)}`; detail.append(trial);
    }
  }
  async function history() {
    const data = await request(`/api/content-review/challengers?report_id=${encodeURIComponent(reportId)}`);
    const host = byId('history'); host.replaceChildren();
    for (const row of data.challengers) {
      const label = new Date(row.created_at).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai'});
      const button = node('button', `${label} · 查看草稿`); button.type = 'button'; button.className = 'secondary-button';
      button.addEventListener('click', async () => {
        try { show(await request(`/api/content-review/challengers/${encodeURIComponent(row.id)}`)); }
        catch (error) { status.textContent = error.message; }
      });
      host.append(button);
    }
    if (!data.challengers.length) host.append(node('p', '这份报告尚未保存草稿。'));
  }
  for (const select of [profile, preset]) select.addEventListener('change', () => { ++sequence; context = null; save.disabled = true; byId('baseline-note').textContent = '方案已改变，请重新载入并核对。'; });
  load.addEventListener('click', async () => {
    if (!profile.value) { status.textContent = '请先选择内容类型。'; return; }
    if (dirty && prompt.value && !window.confirm('重新载入将替换当前尚未保存的 Prompt，继续吗？')) return;
    const token = ++sequence;
    const query = new URLSearchParams({report_id: reportId, profile_id: profile.value});
    if (preset.value) query.set('preset_id', preset.value);
    load.disabled = true; save.disabled = true;
    try {
      const value = await request(`/api/content-review/challengers/context?${query}`);
      if (token !== sequence) return;
      context = value; pending = null;
      byId('original').value = value.baseline.prompt_text; prompt.value = value.baseline.prompt_text;
      byId('baseline-note').textContent = `${value.baseline.profile.name} · ${value.baseline.rules_version} · ${value.baseline.preset_name}。${value.notice}`;
      byId('confirm').checked = false; dirty = false; save.disabled = false;
      status.textContent = '请修改 Prompt 并填写假设。保存后仍不会启用生产策略。';
    } catch (error) { status.textContent = error.message; }
    finally { load.disabled = false; }
  });
  form.addEventListener('input', event => { if ([prompt, byId('name'), byId('hypothesis')].includes(event.target)) dirty = true; });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!context) { status.textContent = '请先载入正式方案。'; return; }
    const body = {report_id: reportId, report_sha256: context.report_sha256, profile_id: profile.value,
      champion_preset_id: context.baseline.preset_id, baseline_sha256: context.baseline.sha256,
      name: byId('name').value, hypothesis: byId('hypothesis').value, prompt_text: prompt.value};
    const key = JSON.stringify(body);
    if (!pending || pending.key !== key) pending = {key, request_key: crypto.randomUUID()};
    const controls = [...form.querySelectorAll('input,textarea,select,button')];
    controls.forEach(control => { control.disabled = true; });
    status.textContent = '正在保存草稿与版本证据…';
    try {
      const draft = await request('/api/content-review/challengers', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...body, request_key: pending.request_key})});
      dirty = false; show(draft);
      status.textContent = '草稿已保存。当前正式 Prompt、Profile 和排期保持不变。';
      history().catch(() => { status.textContent = '草稿已保存，历史列表暂未刷新。当前正式策略保持不变。'; });
    } catch (error) { status.textContent = error.message; }
    finally { controls.forEach(control => { control.disabled = false; }); save.disabled = !context; }
  });
  history().catch(error => { status.textContent = error.message; });
})();
