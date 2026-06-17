# AI 分析说明

AI 分析负责把 `transcripts/transcript.md` 转成候选高光片段，并写入文件和数据库。

## 1. 前置条件

任务必须已经完成转写，并存在：

```text
transcripts/transcript.md
```

如果没有转写文件，AI 分析会停止并提示先转写。

## 2. Provider

当前支持：

| provider | 说明 |
|---|---|
| `remote` | 远程 OpenAI-compatible / DeepSeek |
| `local` | 本地 Ollama |

`.env` 中的默认值：

```text
AI_DEFAULT_PROVIDER=remote
```

远程分析主要配置：

```text
AI_ANALYSIS_REMOTE_BASE_URL
AI_ANALYSIS_REMOTE_API_KEY
AI_ANALYSIS_REMOTE_MODEL
AI_ANALYSIS_REMOTE_PROTOCOL
```

本地分析主要配置：

```text
AI_LOCAL_BASE_URL
AI_LOCAL_API_KEY
AI_LOCAL_MODEL
AI_LOCAL_PROTOCOL
```

## 3. 真实分析流程

```text
读取 transcript.md
→ 选择 Prompt 方案
→ 按长文本分块分析
→ 合并候选片段
→ 校验时间、字段和 JSON
→ 写入 analysis/candidate_clips.json
→ 替换 clip_candidates 当前候选
→ 写入 ai_analysis_runs 历史
→ 任务进入 pending_review
```

远程和本地 provider 都会走长文本分块分析逻辑，不是整集一次性丢给模型。

## 4. 输出文件

```text
analysis/candidate_clips.json
```

该文件保存 AI 分析结果，供后续恢复、排查和人工审核参考。

## 5. 数据库写入

- `clip_candidates`：当前可审核的候选片段。
- `ai_analysis_runs`：每次分析的历史记录。

重新跑 AI 分析时，会用新结果替换当前候选片段。恢复历史 AI 分析时，也会把历史结果重新写回当前候选片段。

## 6. Prompt 方案

Prompt 方案保存在：

```text
ai_prompt_presets
prompts/
```

页面可以选择不同 Prompt。默认方案和综艺访谈方案由数据库初始化时写入。

## 7. 失败处理

- 没有转写文件：直接失败，提示先转写。
- 远程 API 不可用：暂停远程分析，提示用户可手动改用本地 AI。
- 模型返回非法 JSON：解析器会尽量修复常见格式问题，仍失败时任务进入 `failed`。
- AI 候选过短：写入任务日志作为质量提醒，不阻止切片。

## 8. 当前不做

- 不自动选择最佳模型。
- 不保证 AI 结果一定适合发布，仍需要人工审核。
- 不把完整 API 响应保存到仓库。
- 不在没有用户确认的情况下自动发布。
