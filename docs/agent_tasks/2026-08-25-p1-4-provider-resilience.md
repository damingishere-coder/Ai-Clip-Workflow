# P1.4 AI / FFmpeg 失败边界与幂等治理

## 背景

P1.3d 已完成字幕批准批次、目录清理和跨进程恢复。本轮处理审计路线图中工作量最大的 P1.4：AI / ASR / FFmpeg 超时、429 / 5xx、空响应、Markdown / 错误 JSON、重试幂等与重复计费。

## 目标

- 为 AI HTTP、Codex CLI、火山引擎转写与核心 FFmpeg / FFprobe 路径提供明确的超时和可诊断错误。
- 区分“请求明确未被执行，可安全自动重试”和“请求可能已经执行，不得自动重试”。
- 删除会因格式校验失败而自动再次计费的模型调用，优先本地容错解析并保留人工重试入口。
- 长直播窗口只对有明确安全证据的暂时性错误自动重试，并继续复用已完成 checkpoint。
- FFmpeg 超时或异常时清理本次未完成文件，不把部分输出当作成功产物。

## 允许修改范围

- `app/services/ai/**`
- `app/services/ai_analysis_workflow_service.py`
- `app/services/subtitle_ai_service.py`
- `app/services/transcript_service.py`
- `app/services/task_service.py`
- `app/services/video_cut_service.py`
- `app/services/subtitle_data_service.py`
- `app/core/config.py` 与 `.env.example`（仅必要的非敏感超时 / 重试配置）
- 与本轮直接相关的测试、Codemap、审计和进度文档

## 禁止修改范围

- 不调用真实 AI、远程转写、真实 FFmpeg 生产素材或发布平台。
- 不改活动数据库、不执行生产 migration、不删除用户素材。
- 不引入自动 Provider fallback，不把 5xx、超时或坏 JSON 一概机械重试。
- 不改变业务页面结构，不扩大到 P1.5 安全门禁范围。

## 已确定实现要求

1. AI Provider 错误必须带阶段、HTTP 状态、是否适合自动重试、是否存在重复计费不确定性。
2. 429 可依据“请求被限流拒绝”进行有限退避重试；连接建立前失败可有限重试；5xx、读取超时、空响应和错误 JSON 默认视为结果不确定，不自动再次调用计费模型。
3. 通用、综艺、字幕 AI 的 JSON 纠错不得通过第二次模型调用完成；Markdown 围栏和可局部提取 JSON 由本地解析器处理。
4. 长直播 checkpoint 必须记录失败类别；仅安全错误进入自动重试，其余停在明确可恢复状态。
5. 核心 ffprobe 详情查询不得无限阻塞或因文件 `stat` 异常直接 500；切片超时 / 启动失败必须清理部分文件并返回明确失败。
6. 火山引擎 HTTP 429 / 5xx / 超时必须分类，并且 checkpoint 只在确定结果成功后标记完成。

## 验收标准

- 错误 JSON、Markdown、空响应、HTTP 429、HTTP 5xx、网络超时均有隔离单测。
- 证明格式错误不会产生第二次 AI 调用。
- 证明长直播只重试安全错误，并复用已完成窗口。
- 证明 ffprobe 超时 / OSError 不阻断任务详情，FFmpeg 切片失败不遗留可误认成成功的部分文件。
- Ruff、compileall 和 P1.4 定向回归通过；测试数据库必须与活动数据库隔离。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\python.exe -m pytest -q tests/test_codex_cli_provider.py tests/test_ai_json_parsing.py tests/test_long_live_selection.py tests/test_variety_comedy_selection.py tests/test_auto_pipeline.py tests/test_pipeline_checkpoint.py tests/test_long_live_foundation.py tests/test_subtitle_auto_workflow.py tests/test_p0_security.py tests/test_versioning_rollback.py
```

## 返回格式

- 修改文件与行为变化
- 错误分类 / 自动重试矩阵
- 测试命令、通过数、失败数、warnings
- 是否触发真实 AI / ASR / FFmpeg / 平台调用
- 剩余风险与下一轮 P1.5 衔接点
