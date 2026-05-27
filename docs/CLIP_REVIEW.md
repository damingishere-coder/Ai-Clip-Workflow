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
- AI 来源 / 模型：页面不再展示不易解释的置信度，改为展示本次候选片段由哪个 AI Provider 和模型生成。

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

## 预览和转写抽屉

- 左侧候选片段的“播放预览”按钮会控制右侧源视频播放器，自动跳到该片段开始时间，并在结束时间附近暂停。
- “查看这一段转写”会打开右侧转写抽屉，按片段起止时间读取 `transcripts/transcript.md` 中的“逐句时间戳原文”。
- “编辑出入点”会打开源监视器弹窗，用源视频、时间码、noUiSlider 蓝色双手柄范围和播放头辅助人工审核。
- 源监视器支持设置当前播放位置为入点或出点、跳到入点或出点、预览当前片段、拖动左右手柄，以及用 1 / 5 / 15 秒步长微调；滑块会限制最短 1 秒，并遵守任务的最大片段时长。
- 源监视器点击“应用到片段”后只回填当前页面的 `start_time` 和 `end_time`，仍需点击“保存修改”才会写回 SQLite。
- 转写抽屉会优先使用页面上当前的起止时间，因此刚应用但未保存的出入点也可以立即核对原文。
- 新增读取接口：

```text
GET /api/tasks/{task_id}/clips/{clip_id}/transcript-excerpt
```

该接口也支持通过查询参数传入未保存的时间范围：

```text
GET /api/tasks/{task_id}/clips/{clip_id}/transcript-excerpt?start_time=00:10:58&end_time=00:11:48
```

任务里的“单条最长 N 分钟”表示候选片段允许的最长时长，不代表 AI 必须按 N 分钟固定切片。AI 可以选择 6 秒、60 秒或更短的内容，人工审核时也可以改起止时间；保存时仍会校验不能超过该任务设置的最长时长。

## 筛选和排序

当前支持：

- 全部片段。
- 仅启用。
- 高传播价值。
- 按推荐分排序。
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

## 2026-05-27 删除候选片段与紧凑审核

片段审核页现在支持删除单条候选片段。点击候选卡片里的“删除”后，页面会立刻移除该卡片，并调用：

```text
DELETE /api/tasks/{task_id}/clips/{clip_id}
```

删除采用软删除方式，只把 `clip_candidates.is_deleted` 标记为 `1` 并写入 `deleted_at`，不会删除源视频、转写文件、AI 分析文件或已生成切片文件。候选片段列表、启用片段统计和自动切片流程默认排除已删除片段。

候选片段卡片也改为紧凑布局：标题、起止时间、摘要和常用操作默认展示；推荐理由、传播价值、剪辑建议和 AI 来源默认收进可展开区域，方便连续向下审核多个片段。