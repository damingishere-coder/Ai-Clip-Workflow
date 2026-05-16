# 片段审核页说明

## 页面入口

片段审核页入口：

```text
/tasks/{task_id}/clips/review
```

旧入口 `/tasks/{task_id}/clips` 仍保留，方便兼容前面版本的链接。

## 真实数据来源

页面按 `task_id` 读取 SQLite 数据库中的 `clip_candidates` 表，不再使用静态 UI 或模拟候选片段。

每条候选片段展示这些真实字段：

- `enabled`：是否启用。
- `title`：标题。
- `start_time`：开始时间。
- `end_time`：结束时间。
- `duration_seconds`：片段时长。
- `summary`：内容摘要。
- `highlight_reason`：推荐理由。
- `spread_value`：传播价值。
- `suggested_editing`：剪辑建议。
- `confidence_score`：AI 置信度。

## 人工编辑能力

当前支持在页面上修改：

- 标题。
- 开始时间。
- 结束时间。
- 是否启用。
- 内容摘要。

保存后会写回 `clip_candidates` 表，并把 `reviewed` 标记为 `1`。

## 保存接口

单条更新接口：

```text
POST /api/tasks/{task_id}/clips/{clip_id}/update
```

批量更新接口：

```text
POST /api/tasks/{task_id}/clips/batch-update
```

保存时会做基础校验：

- 时间格式必须是 `MM:SS` 或 `HH:MM:SS`。
- `end_time` 必须大于 `start_time`。
- `duration_seconds` 会根据起止时间自动重新计算。
- 片段时长不能超过该任务的 `max_clip_duration`。
- 校验失败时页面顶部会显示错误提示。

保存审核修改不会改变任务状态，任务会继续保持 `pending_review`，直到用户进入切割阶段。

## 筛选和排序

当前支持：

- 全部片段。
- 仅启用。
- 高传播价值。
- 按推荐分 / 置信度排序。
- 按时间顺序排序。

## 后续切割流程

页面底部“生成切片”按钮当前调用预留接口：

```text
POST /api/tasks/{task_id}/clips/generate
```

该接口当前用于第九轮前的流程占位，会提示“待视频切割模块接入”，不会把任务状态改成 `cutting`。

第九轮可以把这个入口接到真实 FFmpeg 切割流程，或调整为调用现有预研接口：

```text
POST /api/tasks/{task_id}/process/cuts
```
