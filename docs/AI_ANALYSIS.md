# AI 片段分析说明

## 2026-05-23：AI Prompt 方案

任务详情页现在使用“AI Prompt 方案”管理 AI 分析 Prompt：

- 全局共用 1、2、3 号方案，保存在 SQLite `ai_prompt_presets` 表。
- 1 号方案默认使用直播切片分析专家 Prompt。
- 每个任务通过 `ai_prompt_preset_id` 记录当前选中的方案。
- AI 分析时读取当前任务选中的 Prompt，再替换 `{{MAX_CLIP_DURATION}}`、`{{TARGET_CLIP_COUNT}}`、`{{AI_PREFERENCE}}`、`{{TRANSCRIPT_TEXT}}`。
- AI 输出 JSON 可以不包含 `task_id`，程序会自动补当前任务 ID 后继续校验和写入片段审核数据。
- 点击远程 AI 分析前会弹出二次确认，避免误操作覆盖已有候选片段。

## 1. 配置方式

项目根目录新增 `.env.example`。第一次使用时，把它复制为 `.env`，再填写真实配置。

也可以在页面中配置：打开 `http://127.0.0.1:8001/system`，在“三类 AI 接口配置”里填写 `2. 分析文字稿，生成候选切片` 后保存。页面会把配置写入项目根目录 `.env`，真实 API Key 不会提交到 Git。

页面保存规则：

- API Key 输入框会完整回显当前值，方便本机个人使用。
- 保存时只更新页面相关配置键，会保留 `.env` 里的火山转写 Key、存储路径和其他无关配置。
- 保存后当前运行中的服务会立即使用新配置；如果后续手动改 `.env`，建议重启服务。

远程文字稿分析接口配置：

```text
AI_DEFAULT_PROVIDER=remote
AI_ANALYSIS_REMOTE_BASE_URL=https://api.deepseek.com
AI_ANALYSIS_REMOTE_API_KEY=你的文字稿分析 API Key
AI_ANALYSIS_REMOTE_MODEL=deepseek-v4-flash
AI_ANALYSIS_REMOTE_PROTOCOL=chat_completions
AI_ANALYSIS_REMOTE_REASONING_EFFORT=
AI_ANALYSIS_REMOTE_RESPONSES_PATH=/v1/responses
AI_ANALYSIS_REMOTE_DISABLE_RESPONSE_STORAGE=true
AI_ANALYSIS_REQUEST_TIMEOUT_SECONDS=120
```

本地 Ollama 配置：

```text
AI_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
AI_LOCAL_API_KEY=ollama
AI_LOCAL_MODEL=qwen3:8b
AI_LOCAL_PROTOCOL=chat_completions
AI_LOCAL_FALLBACK_PROTOCOL=
```

系统状态页的本地 AI 配置已简化为 Ollama 模型下拉选择，默认可选 `qwen3:8b`、`gemma3:12b`、`qwen3:14b`。

## 2. 分析链路

```text
转写 Markdown
→ 用户点击“远程 AI 分析”或“本地 AI 分析”
→ 任务状态进入 ai_analyzing
→ 读取 prompts/clip_analysis_prompt.txt
→ 注入最大时长、候选数量、AI 偏好、转写文本
→ Provider 调用 AI 接口
→ 解析严格 JSON
→ Pydantic 校验字段
→ 校验片段时长和转写时间范围
→ 写入 analysis/candidate_clips.json
→ 写入 clip_candidates 表
→ 任务状态进入 pending_review
```

## 3. 失败处理

- 如果转写文本不存在，任务进入 `failed`，并记录错误。
- 如果转写文本没有时间戳，任务进入 `failed`。
- 如果 AI 第一次返回非法 JSON，程序会自动追加安全重试指令，再重试一次。
- 如果重试后仍无法解析或校验，任务进入 `failed`，错误信息会显示在任务详情页。
- 如果片段超过用户设置的最长时长，或起止时间超出转写文本范围，任务进入 `failed`。

## 4. 如何测试

不需要 API Key 的本地结构测试：

```powershell
python scripts/test_ai_json_validation.py
python scripts/test_mock_transcript_analysis.py
```

远程 AI 连通性测试：

```powershell
python scripts/test_remote_ai_connection.py
```

本地 AI 连通性测试：

```powershell
python scripts/test_local_ai_connection.py
```

页面测试：

1. 启动项目。
2. 打开任务详情页。
3. 确认任务已经生成 `transcripts/transcript.md`。
4. 点击“远程 AI 分析”或“本地 AI 分析”。
5. 成功后进入片段审核页，查看候选片段列表。
