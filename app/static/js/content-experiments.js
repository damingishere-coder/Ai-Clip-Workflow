(() => {
  'use strict';
  function node(tag, text) { const el = document.createElement(tag); if (text) el.textContent = text; return el; }
  function field(label, control) {
    const el = node('label');
    if (control.type === 'checkbox') { el.className = 'checkbox-row'; el.append(control, node('span', label)); }
    else { el.textContent = label; el.append(control); }
    return el;
  }
  function date(value) { return value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short'}) : '暂无'; }
  async function request(url, body) {
    const response = await fetch(url, body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : undefined);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请检查填写内容');
    return data;
  }
  function select(label) { const el = node('select'); el.setAttribute('aria-label', label); el.append(new Option('请选择', '')); return el; }
  function button(label) { const el = node('button', label); el.type = 'button'; el.className = 'secondary-button'; return el; }

  function draftExperiment(host, draft) {
    const section = node('section'); section.className = 'form-panel'; section.dataset.challengerExperiment = draft.id;
    const account = select('实验账号'), reports = select('冻结基线报告'), cohorts = select('基线分组');
    const load = button('检查官方基线'), form = node('form'), message = node('p'); message.setAttribute('role', 'status');
    const confirm = node('input'); confirm.type = 'checkbox'; confirm.required = true;
    const save = button('确认创建作品实验'); save.type = 'submit'; save.disabled = true;
    form.append(field('基线分组', cohorts), field('确认固定 5 秒完播率为主指标，冻结此组证据；创建实验不会启用正式策略。', confirm), save);
    section.append(node('h3', '正式作品实验'), node('p', '选择已经生成的官方报告。没有可信基线时，可继续人工试验任务。'),
      field('抖音账号', account), field('冻结基线报告', reports), load, form, message);
    host.append(section);
    let context = null, sequence = 0;
    function invalidate() { ++sequence; context = null; save.disabled = true; confirm.checked = false; cohorts.replaceChildren(new Option('请选择', '')); }
    request('/api/content-review/accounts').then(data => {
      for (const item of data.accounts) account.append(new Option(item.account_name, item.id));
      if (!data.accounts.length) message.textContent = '尚无账号及官方数据，不能创建正式作品实验。';
    }).catch(error => { message.textContent = error.message; });
    account.addEventListener('change', async () => {
      invalidate(); const token = sequence; reports.replaceChildren(new Option('请选择', ''));
      if (!account.value) return;
      try {
        const data = await request(`/api/content-review/intelligence/reports?account_id=${encodeURIComponent(account.value)}`);
        if (token !== sequence) return;
        for (const item of data.reports) reports.append(new Option(new Date(item.created_at).toLocaleString('zh-CN'), item.id));
        message.textContent = data.reports.length ? '选择报告后检查基线。' : '请先返回内容复盘，为此账号生成经验报告。';
      } catch (error) { message.textContent = error.message; }
    });
    reports.addEventListener('change', invalidate);
    cohorts.addEventListener('change', () => { confirm.checked = false; save.disabled = !context?.cohorts.some(c => c.sha256 === cohorts.value && c.ready); });
    load.addEventListener('click', async () => {
      invalidate(); const token = sequence;
      if (!reports.value) { message.textContent = '请先选择官方报告。'; return; }
      load.disabled = true;
      try {
        const data = await request(`/api/content-review/challengers/${encodeURIComponent(draft.id)}/experiment-context?report_id=${encodeURIComponent(reports.value)}`);
        if (token !== sequence) return;
        context = data;
        for (const c of data.cohorts) cohorts.append(new Option(`${c.age_band} · ${c.work_count} 作品 / ${c.verified_original_count} 原片 / ${c.official_weeks.length} 导出周 · ${c.controls.value.provider.name} / ${c.controls.value.model} · ${c.confidence_label}`, c.sha256));
        message.textContent = (data.cohorts.length ? '' : '数据不足，没有版本和执行条件完整的对照组。') + data.notice;
      } catch (error) { message.textContent = error.message; }
      finally { load.disabled = false; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!context || !confirm.checked || !context.cohorts.some(c => c.sha256 === cohorts.value && c.ready)) return;
      const body = {report_id: context.report_id, report_sha256: context.report_sha256,
        challenger_sha256: context.challenger_sha256, cohort_sha256: cohorts.value, confirm: true};
      const controls = [account, reports, cohorts, load, confirm, save]; controls.forEach(c => { c.disabled = true; });
      try {
        const data = await request(`/api/content-review/challengers/${encodeURIComponent(draft.id)}/experiments`, body);
        const link = node('a', '前往内容复盘查看实验'); link.href = '/content-review';
        message.replaceChildren(node('span', `实验已冻结（${data.experiment_id}），正式策略保持不变。`), link);
      } catch (error) { message.textContent = error.message; controls.forEach(c => { c.disabled = false; }); }
    });
  }

  function policyActions(host, experiment) {
    if (!experiment.challenger_id) return;
    const section = node('section'), message = node('p'); message.setAttribute('role', 'status');
    section.append(node('p', `策略版本已冻结。排除 ${experiment.progress?.excluded_count || 0} 条不满足归因、控制条件或采集年龄要求的作品。实验保留结论与正式启用是两个独立操作。`));
    const summary = experiment.progress?.summary, baseline = experiment.baseline;
    if (summary && baseline) {
      section.append(node('p', `${summary.confidence_label} · 试验原片 ${summary.verified_original_count} 条 / 对照原片 ${baseline.verified_original_count} 条 · 采集年龄 ${baseline.age_band}`));
      section.append(node('p', `试验发布时间：${date(summary.published_start)} 至 ${date(summary.published_end)}；对照发布时间：${date(baseline.published_start)} 至 ${date(baseline.published_end)}`));
      for (const [key, label] of [['completion_rate', '完播率'], ['two_second_bounce_rate', '2 秒跳出率'], ['watch_ratio', '观看比例']]) {
        const a = baseline.metrics[key], b = summary.metrics[key];
        const value = metric => metric.median == null ? '缺失' : `${(metric.median * 100).toFixed(1)}%`;
        section.append(node('p', `${label}中位数：对照 ${value(a)}（${a.count} 条，缺失 ${a.missing}）→ 试验 ${value(b)}（${b.count} 条，缺失 ${b.missing}）`));
      }
      section.append(node('p', experiment.progress.notice));
    }
    const evidence = node('a', '查看实验启用与回退记录'); evidence.href = `/api/content-review/experiments/${encodeURIComponent(experiment.id)}/policy-events`; evidence.target = '_blank'; evidence.rel = 'noopener'; section.append(evidence);
    if (experiment.status === 'completed' && experiment.decision === 'keep') {
      const actions = node('div'), previewHost = node('div'); actions.className = 'button-row';
      let sequence = 0;
      for (const [action, label] of [['activate', '预览正式启用'], ['rollback', '预览回退']]) {
        const previewButton = button(label); actions.append(previewButton);
        previewButton.addEventListener('click', async () => {
          const token = ++sequence; previewHost.replaceChildren();
          try {
            const preview = await request(`/api/content-review/experiments/${encodeURIComponent(experiment.id)}/policy-preview?action=${action}`);
            if (token !== sequence) return;
            const form = node('form'), before = node('textarea'), after = node('textarea'), confirm = node('input');
            form.className = 'form-panel';
            before.readOnly = after.readOnly = true; before.value = preview.before_text; after.value = preview.after_text;
            before.rows = after.rows = 8; confirm.type = 'checkbox'; confirm.required = true;
            const submit = button(action === 'activate' ? '确认正式启用' : '确认回退'); submit.type = 'submit';
            form.append(node('p', preview.notice), field('当前正式正文', before), field('操作后的正文', after),
              field('我已核对差异，确认只更新这个正式 Prompt 的新任务默认正文。', confirm), submit);
            const body = {action, preview_sha256: preview.sha256, confirm: true, request_key: crypto.randomUUID()};
            form.addEventListener('submit', async event => {
              event.preventDefault(); if (!confirm.checked) return;
              submit.disabled = true; for (const b of actions.children) b.disabled = true;
              try {
                await request(`/api/content-review/experiments/${encodeURIComponent(experiment.id)}/policy-events`, body);
                previewHost.replaceChildren(); message.textContent = action === 'activate' ? '正式策略已按本次确认启用，旧任务保持冻结。' : '已恢复启用前正文，已创建的任务保持冻结。';
              } catch (error) { message.textContent = error.message; submit.disabled = false; }
              finally { for (const b of actions.children) b.disabled = false; }
            });
            previewHost.append(form); message.textContent = '请逐项核对两份正文；预览不会修改策略。';
          } catch (error) { if (token === sequence) message.textContent = error.message; }
        });
      }
      section.append(actions, previewHost);
    }
    section.append(message); host.append(section);
  }
  window.NiuMaExperiments = {draftExperiment, policyActions};
})();
