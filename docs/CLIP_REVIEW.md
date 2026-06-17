# 候选片段审核说明

候选片段审核位于 AI 分析之后、自动切片之前。它的作用是让用户确认哪些片段值得切。

## 1. 入口

```text
/tasks/{task_id}/clips/review
```

任务进入 `pending_review` 后，可以打开审核页面。

## 2. 数据来源

候选片段来自：

- `analysis/candidate_clips.json`
- `clip_candidates` 数据表

页面展示以数据库 `clip_candidates` 为准。

## 3. 可以编辑的内容

当前支持：

- 修改标题。
- 修改开始时间。
- 修改结束时间。
- 修改摘要。
- 启用或停用候选片段。
- 删除候选片段。

删除是软删除，会标记 `is_deleted = 1`，不是物理删除数据库行。

## 4. 哪些片段会被切

自动切片只读取：

```text
enabled = 1
is_deleted = 0
```

停用或删除的候选片段不会进入 `05_clips/`。

## 5. 与 AI 历史的关系

每次重新跑 AI 分析会替换当前候选片段，并写入一条 `ai_analysis_runs`。

恢复历史 AI 分析时：

- 会把历史 payload 写回 `analysis/candidate_clips.json`。
- 会用历史结果替换当前 `clip_candidates`。
- 任务回到 `pending_review`。

## 6. 注意事项

- AI 候选片段不等于最终成片，仍建议人工检查开始和结束时间。
- 如果片段太短或内容割裂，可以修改时间后再切。
- 如果候选数量不合适，可以调整任务的候选数量或 Prompt 后重新分析。
