/* Explicit original-proposal decisions; no automatic production action. */
(() => {
  const reasons = { worth_publishing: "值得发", not_funny: "不好笑", fragmented: "片段不完整", missing_setup: "铺垫缺失", duplicate: "内容重复", dragging: "节奏拖沓", low_value: "内容价值不足", weak_evidence: "论据或上下文不足", other: "其他" };
  const el = (tag, text = "") => { const node = document.createElement(tag); node.textContent = text; return node; };
  const api = async (url, payload) => {
    const response = await fetch(url, payload ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) } : {});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "请求未通过校验，请刷新后重试");
    return result;
  };
  const review = document.getElementById("human-observation-review");
  if (review) {
    const task = encodeURIComponent(review.dataset.taskId);
    const select = document.getElementById("human-review-run");
    const status = document.getElementById("human-review-decision-status");
    let generation = 0;
    let loaded = false;
    let saving = false;
    const load = async () => {
      const sequence = ++generation;
      const run = select.value;
      if (!run) return;
      status.textContent = "加载原始候选证据…";
      try {
        const result = await api(`/api/tasks/${task}/ai-analysis-runs/${encodeURIComponent(run)}/review-observations`);
        if (sequence !== generation) return;
        const pool = document.getElementById("human-review-observations");
        const diagnostics = document.getElementById("human-review-diagnostic-items");
        pool.replaceChildren(); diagnostics.replaceChildren();
        document.getElementById("human-review-evidence-note").textContent = `${result.profile} · Profile 版本 ${result.profile_version_id || "历史未知"} · Prompt 版本 ${result.prompt_version_id || "历史未知"} · ${result.evidence_basis === "frozen_v1" ? "已冻结初始候选" : "读取历史快照，缺失等级/诊断保持未知"}${result.analysis_incomplete ? " · 文字分析不完整，仅作诊断" : ""}`;
        for (const row of result.observations) {
          const card = el("article"); card.className = "clip-quality-card";
          card.append(el("strong", row.title || "未命名候选"), el("p", `${row.start_time}–${row.end_time} · ${row.quality_tier ? row.quality_tier + " 级" : "等级未知"} · ${row.quality_score ?? "分数未知"} · ${row.initial_recommended === true ? "AI 默认推荐" : row.initial_recommended === false ? "AI 未默认推荐" : "原始推荐未知"}`), el("p", row.summary));
          if (row.rejection_reason) card.append(el("p", `AI 未选原因：${row.rejection_reason}`));
          const current = el("p", row.human_decision ? `已明确${row.human_decision === "keep" ? "接受" : "拒绝"} · ${reasons[row.decision_reason] || row.decision_reason}` : row.ambiguous ? "历史保存记录无法证明明确审阅，等待确认" : "未明确审阅");
          card.append(current);
          const preview = el("a", "查看原片对应位置");
          const seconds = row.start_time.split(":").reduce((sum, part) => sum * 60 + Number(part), 0);
          preview.href = `/media/tasks/${task}/source-video#t=${Number.isFinite(seconds) ? seconds : 0}`; preview.target = "_blank"; preview.rel = "noopener";
          card.append(preview);
          const reason = el("select"); reason.setAttribute("aria-label", `拒绝原因：${row.title}`);
          const placeholder = el("option", "拒绝前选择原因"); placeholder.value = ""; reason.append(placeholder);
          for (const [key, label] of Object.entries(reasons)) if (key !== "worth_publishing") { const option = el("option", label); option.value = key; reason.append(option); }
          const controls = el("div"); controls.className = "button-row";
          for (const [decision, label] of [["keep", "明确接受"], ["reject", "明确拒绝"]]) {
            const button = el("button", label); button.type = "button"; button.className = "secondary-button compact-button";
            button.addEventListener("click", async () => {
              if (saving || run !== select.value) return;
              if (decision === "reject" && !reason.value) { status.textContent = "请先为这条候选选择拒绝原因。"; reason.focus(); return; }
              saving = true; select.disabled = true;
              const buttons = review.querySelectorAll("button"); buttons.forEach(b => { b.disabled = true; });
              try {
                await api(`/api/tasks/${task}/ai-analysis-runs/${encodeURIComponent(run)}/review-observations`, { observation_key: row.key, observation_sha256: row.sha256, decision, reason_code: decision === "keep" ? "worth_publishing" : reason.value });
                current.textContent = `已明确${decision === "keep" ? "接受" : "拒绝"}`;
                status.textContent = "评价已保存。切片选择仍使用原有审核操作。";
              } catch (error) { status.textContent = error.message; }
              finally { saving = false; select.disabled = false; buttons.forEach(b => { b.disabled = false; }); }
            });
            controls.append(button);
          }
          controls.append(reason); card.append(controls);
          (row.in_review_pool ? pool : diagnostics).append(card);
        }
        if (!diagnostics.children.length) diagnostics.append(el("p", "这个批次没有保存可评价的诊断候选；不能据此推断 C 级为零或已被人工拒绝。"));
        status.textContent = result.observations.length ? "请选择真正审阅过的片段记录意见。" : "这个批次没有可验证的候选快照。";
      } catch (error) { if (sequence === generation) status.textContent = error.message; }
    };
    select.addEventListener("change", load);
    document.getElementById("human-observation-disclosure").addEventListener("toggle", async event => {
      if (!event.target.open || loaded) return;
      loaded = true;
      try {
        const result = await api(`/api/tasks/${task}/review-observations`);
        select.replaceChildren();
        for (const run of result.runs) { const option = el("option", `第 ${run.run_number} 次 · ${run.created_at}${run.is_active ? " · 当前" : ""}`); option.value = run.id; select.append(option); }
        if (!result.runs.length) status.textContent = "没有可读取的 AI 分析历史。";
        else await load();
      } catch (error) { loaded = false; status.textContent = error.message; }
    });
  }
  if (document.getElementById("human-review-report")) {
    const status = document.getElementById("human-review-report-status");
    const period = document.getElementById("human-review-days");
    let generation = 0;
    const percent = value => value == null ? "暂无有效分母" : `${(value * 100).toFixed(1)}%`;
    const load = async () => {
      const sequence = ++generation;
      status.textContent = "加载人工审片统计…";
      try {
        const report = await api(`/api/content-review/human-review?days=${period.value}`);
        if (sequence !== generation) return;
        const summary = document.getElementById("human-review-summary");
        const groups = document.getElementById("human-review-groups");
        summary.replaceChildren(); groups.replaceChildren();
        const s = report.summary;
        summary.append(el("p", `候选 ${s.candidate_count}（诊断 ${s.diagnostic_count}） · 接受 ${s.accepted} · 拒绝 ${s.rejected} · 未明确审阅 ${s.unreviewed}（来源歧义 ${s.ambiguous}）`));
        summary.append(el("p", `AI 推荐接受率 ${percent(s.recommendation_acceptance_rate)}（${s.recommendation_confidence_label}） · 审阅覆盖率 ${percent(s.review_coverage)} · 已审推荐 ${s.reviewed_recommended} / 推荐 ${s.recommended} · 推荐状态未知 ${s.recommendation_unknown}`));
        summary.append(el("p", `${s.confidence_label} · ${s.task_count} 条任务 / ${s.run_count} 次分析 · 可验证独立原片 ${s.verified_original_count} · 原片身份未知的决定 ${s.source_unknown_decisions}。形成探索性建议至少需要 20 个有效决定、3 条独立原片。`));
        summary.append(el("p", `Profile 版本未知 ${report.profile_version_unknown} · Prompt 版本未知 ${report.prompt_version_unknown} · 批次内无法匹配反馈 ${report.unmatched_feedback} · 近期无批次归因反馈 ${report.recent_unattributed_feedback} · 无效批次 ${report.invalid_runs}`));
        for (const [dimension, label] of [["profile", "内容类型"], ["quality_tier", "A / B / C 等级（跨类型描述统计）"], ["score_band", "评分区间（跨类型描述统计）"]]) {
          const details = el("details"); details.append(el("summary", label));
          for (const row of report.groups[dimension]) {
            details.append(el("p", `${row.key}：接受 ${row.accepted} / 拒绝 ${row.rejected} / 未审 ${row.unreviewed}；已审候选接受率 ${percent(row.manual_acceptance_rate)}，审阅覆盖率 ${percent(row.all_candidate_review_coverage)}；${row.confidence_label}，独立原片 ${row.verified_original_count}`));
            const rejected = Object.entries(row.rejection_reasons).map(([key, count]) => `${reasons[key] || key} ${count}`).join("、");
            if (rejected) details.append(el("p", `拒绝原因：${rejected}`));
          }
          groups.append(details);
        }
        status.textContent = `${report.start} 至 ${report.cutoff}。${report.truncated ? "数据超过本页计算上限，当前仅为部分统计，不形成建议。" : ""}${report.note}`;
      } catch (error) { if (sequence === generation) status.textContent = error.message; }
    };
    period.addEventListener("change", load); load();
  }
})();
