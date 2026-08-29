# AI 分析与内容归因契约修复任务

## 背景

第二次工程复检确认三个数据正确性问题：长直播结果缺少必需质量字段，显式反馈会错误绑定当前 active Run，官方导出缺少时长时 Prompt 对比无法计算平均观看比例。

## 目标

- 让长直播完整与不完整结果都产生结构完整、可验证的质量元数据。
- 让反馈只绑定候选片段的真实来源 Run；来源不可信时保留反馈但不进行 Prompt 归因。
- 让 Prompt 对比复用内容诊断已经采用的有效时长回退顺序。

## 允许修改范围

- `app/services/ai/long_live_talk_analyzer.py`
- `app/services/clip_feedback_service.py`
- `app/services/content_review_service.py`
- 上述行为对应的测试文件
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md` 与本任务文件

## 禁止修改范围

- 数据库结构和活动 SQLite。
- Provider 调用、发布执行、Chrome Worker 和运行中服务。
- 前端交互、迁移框架和本阶段无关的代码味道。

## 已确定实现要求

1. `quality_degraded` 必须由长直播分析器显式写入，不能放宽共享校验器的缺失字段门禁。
2. 显式反馈不得查询或猜测当前 active Run；只接受候选 `source_analysis_run_id` 且必须属于同一任务。
3. 来源缺失、不存在或属于其他任务时，`analysis_run_id` 写为 `NULL`，反馈本身继续保存。
4. 有效时长顺序固定为导入时长、候选时长、输出片段 `source_duration_ms`。

## 验收标准

- 完整长直播元数据通过共享切片校验，不完整结果仍被门禁阻止。
- 旧候选在新 active Run 存在时仍归因到旧来源 Run。
- 不可信来源不会回退到 active Run，也不会触发外键错误。
- 官方导出时长为空时，Prompt 对比能用候选或输出片段时长计算观看比例。
- 定向测试、全量测试、Ruff、Compileall 和 `git diff --check` 通过。

## 测试命令

```powershell
pytest -q tests/test_long_live_selection.py tests/test_partial_ai_analysis.py tests/test_content_review_foundation.py tests/test_content_review.py
ruff check app tests scripts
python -m compileall -q app tests scripts
pytest -q
git diff --check
```

## 返回格式

- 三个契约的修复说明与回归证据。
- 修改文件、测试结果、分支、提交 SHA、远端 SHA 和 PR 状态。
