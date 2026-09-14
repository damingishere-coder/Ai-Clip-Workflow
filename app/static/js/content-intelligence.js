(() => {
  'use strict';
  const host = document.getElementById('intelligence-report');
  if (!host) return;
  const account = document.getElementById('content-review-account');
  const days = document.getElementById('intelligence-days');
  const create = document.getElementById('intelligence-create');
  const status = document.getElementById('intelligence-status');
  const result = document.getElementById('intelligence-result');
  const history = document.getElementById('intelligence-history');
  const labels = {duration_band: '片长', profile: '内容类型', topic: '话题', hook_type: 'Hook', score_band: '质量分', prompt_version_id: 'Prompt 版本', publish_hour: '北京时间发布小时'};
  let sequence = 0;
  let pending = null;
  const element = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  const dateLabel = value => new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short'}).format(new Date(value));
  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '报告请求失败');
    return data;
  }
  function render(data) {
    result.replaceChildren();
    const report = data.report, human = report.human_review.summary, performance = report.performance;
    result.append(element('p', `已冻结：${dateLabel(report.start)} 至 ${dateLabel(report.cutoff)}（北京时间）`));
    result.append(element('p', `人工明确审阅 ${human.reviewed} 条，接受 ${human.accepted}，拒绝 ${human.rejected}；${human.confidence_label}。`));
    result.append(element('p', `AI 推荐中已审 ${human.reviewed_recommended}/${human.recommended} 条；接受率 ${human.recommendation_acceptance_rate === null ? '暂无有效分母' : (human.recommendation_acceptance_rate * 100).toFixed(1) + '%'}。已知原片 ${human.verified_original_count} 个，原片未知的决定 ${human.source_unknown_decisions} 条。`));
    for (const [dimension, groups] of Object.entries(report.human_review.groups)) {
      const details = document.createElement('details');
      details.append(element('summary', `人工审片 · ${{profile: '内容类型', quality_tier: '等级', score_band: '评分'}[dimension]}`));
      for (const group of groups) details.append(element('p', `${group.key}：接受 ${group.accepted}，拒绝 ${group.rejected}，未审 ${group.unreviewed}；${group.confidence_label}。`));
      result.append(details);
    }
    result.append(element('p', performance.note));
    result.append(element('p', `官方去重作品记录 ${performance.work_count} 条；准确归因 ${performance.attributed_count || 0} 条。`));
    if (performance.truncated) result.append(element('p', '记录超过本轮读取上限，报告不完整，不能形成可信比较。'));
    for (const [dimension, groups] of Object.entries(performance.groups)) {
      const details = document.createElement('details');
      details.append(element('summary', `${labels[dimension]} · ${groups.length} 组`));
      for (const group of groups) {
        const metric = group.metrics.five_second_completion_rate;
        details.append(element('p', `${group.key === 'unknown' ? '未知' : group.key} · ${group.profile} · ${group.age_band}：${group.work_count} 条，${group.verified_original_count} 个已知原片，${group.official_weeks.length} 个导出周；${group.confidence_label}。5 秒完播中位数 ${metric.median === null ? '缺失' : (metric.median * 100).toFixed(1) + '%'}，缺失 ${(metric.missing_rate * 100).toFixed(1)}%。`));
        details.append(element('small', `策略证据缺失 ${group.strategy_unknown} 条，成片边界已变 ${group.bounds_changed} 条、未知 ${group.bounds_unknown} 条。5 秒完播指标：${metric.confidence === 'comparison_ready' ? '达到比较门槛' : '数据不足'}。`));
      }
      result.append(details);
    }
    const recommendations = performance.recommendations || [];
    if (!recommendations.length) result.append(element('p', '暂无达到比较门槛的作品建议；现有数字仅作描述。'));
    for (const item of recommendations) {
      const e = item.evidence;
      result.append(element('p', `${labels[e.dimension]}：${e.higher_key}（${e.higher_count} 条）与 ${e.lower_key}（${e.lower_count} 条）。${item.conclusion} ${item.recommendation} ${item.limitation}`));
    }
    const evidence = element('a', '查看冻结证据 JSON');
    evidence.href = `/api/content-review/intelligence/reports/${encodeURIComponent(data.id)}`;
    evidence.target = '_blank'; evidence.rel = 'noopener';
    result.append(evidence);
  }
  async function refresh() {
    const token = ++sequence;
    try {
      const data = await request(`/api/content-review/intelligence/reports?account_id=${encodeURIComponent(account.value)}`);
      if (token !== sequence) return;
      history.replaceChildren();
      for (const item of data.reports) {
        const button = element('button', `${dateLabel(item.created_at)} · 查看报告`);
        button.type = 'button'; button.className = 'secondary-button';
        button.addEventListener('click', async () => {
          const current = ++sequence;
          try { const saved = await request(`/api/content-review/intelligence/reports/${encodeURIComponent(item.id)}`); if (current === sequence) render(saved); }
          catch (error) { if (current === sequence) status.textContent = error.message; }
        });
        history.append(button);
      }
      if (!data.reports.length) history.append(element('p', '尚未保存报告。'));
    } catch (error) { if (token === sequence) status.textContent = error.message; }
  }
  create.addEventListener('click', async () => {
    const config = {account_id: account.value, days: Number(days.value)};
    const configKey = JSON.stringify(config);
    if (!pending || pending.configKey !== configKey) pending = {configKey, request_key: crypto.randomUUID()};
    create.disabled = true; account.disabled = true; days.disabled = true;
    const token = ++sequence;
    status.textContent = '正在冻结统计与证据…';
    try {
      const saved = await request('/api/content-review/intelligence/reports', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...config, request_key: pending.request_key})});
      pending = null;
      if (token === sequence) render(saved);
      status.textContent = '报告已保存。生产策略和排期保持不变。';
      await refresh();
    } catch (error) { status.textContent = `${error.message}；再次点击将使用同一请求编号核对结果。`; }
    finally { create.disabled = false; account.disabled = !account.value; days.disabled = false; }
  });
  account.addEventListener('change', () => { result.replaceChildren(); pending = null; refresh(); });
  refresh();
})();
