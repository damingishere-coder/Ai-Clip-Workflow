# AI 片段分析说明

## 1. 配置方式

项目根目录新增 `.env.example`。第一次使用时，把它复制为 `.env`，再填写真实配置。

也可以在页面中配置：打开 `http://127.0.0.1:8001/system`，点击右上角“AI 配置”，在弹窗里填写远程 AI 或本地 AI 信息后保存。页面会把配置写入项目根目录 `.env`，真实 API Key 不会提交到 Git。

页面保存规则：

- API Key 输入框默认不回显明文，只显示脱敏占位。
- 如果保存时 API Key 留空，会保留 `.env` 里原来的密钥。
- 保存后当前运行中的服务会立即使用新配置；如果后续手动改 `.env`，建议重启服务。

远程中转站配置：

```text
AI_DEFAULT_PROVIDER=remote
AI_REMOTE_BASE_URL=https://ai.oneinfinityai.com
AI_REMOTE_API_KEY=你的远程中转站密钥
AI_REMOTE_MODEL=gpt-5.5
AI_REMOTE_REVIEW_MODEL=gpt-5.5
AI_REMOTE_PROTOCOL=responses
AI_REMOTE_REASONING_EFFORT=xhigh
AI_REMOTE_RESPONSES_PATH=/v1/responses
AI_REMOTE_DISABLE_RESPONSE_STORAGE=true
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
