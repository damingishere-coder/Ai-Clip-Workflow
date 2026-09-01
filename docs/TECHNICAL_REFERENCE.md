# 牛马片场技术参考

这份文档保存 README 不适合展开的架构、状态、排期和发布细节。面向第一次安装的用户，请先阅读 [PROJECT_GUIDE.md](PROJECT_GUIDE.md)。

## 2026-08-28：v2.2 内容复盘技术边界

- 日汇总导入支持 `.xlsx/.csv`，限制 10MB、10,000 行和 50 列，分为预览与确认；`.xls`、宏文件、外部链接、异常范围和重复日期会被拒绝。
- 作品同步由用户手动触发，通过现有 Windows Chrome Worker 复用登录态与账号锁，最多读取最近 50 条并只返回白名单指标。固定错误码为 `LOGIN_REQUIRED`、`VERIFICATION_REQUIRED`、`RATE_LIMITED`、`PAGE_CHANGED`、`WORKER_UNAVAILABLE`，不自动重试。
- 匹配优先平台作品 ID；其次仅在同账号下使用唯一规范化标题、发布时间 ±10 分钟和时长辅助。多结果或证据不足时保留未匹配，等待人工确认。
- Prompt 对比只纳入准确关联作品：3 个完整同步周期且当前版本至少 30 条才可评估；相邻版本各至少 20 条才显示版本间对比。播放中位数、5 秒完播、2 秒跳出、平均播放时长占比和互动率共同观察，系统不自动改 Prompt。
- `/health` 继续是轻量存活检查；`/api/system/readiness` 默认检查数据库读取、迁移账本、存储和调度，`deep=1` 增加完整性与外键检查。数据库/任务存储异常为 `not_ready`，Worker/FFmpeg 异常为 `degraded`。
- GitHub 项目只用于理解 [OpenCLI 的同源指标请求](https://github.com/zhouke2020/OpenCLI/blob/9c132661d39ce2b3edb5fff28eeb69662e1dde1a/clis/douyin/stats.js)和 [cheat-on-content 的作品列表 XHR 捕获](https://github.com/XBuilderLAB/cheat-on-content/blob/4123941f59d1c86b8da94bbd0164475eb19f04e9/adapters/perf-data/douyin-session/crawler.py)思路；实现未复制其代码、未新增其为依赖，也不绕过登录、验证码或平台风控。

## 1. 运行形态

- 操作系统：Windows 本地单用户。
- Web 后端：FastAPI。
- 页面：HTML、CSS、JavaScript、Jinja2。
- 数据库：SQLite。
- 视频处理：FFmpeg / FFprobe。
- 远程转写：火山引擎。
- 本地转写：faster-whisper。
- AI 分析：受控 Codex CLI、OpenAI-compatible / DeepSeek 或本地 Ollama。
- 真实发布：Windows Chrome Worker。
- 后端支持平台：抖音、B站；当前发送中心前台、任务同步和全自动流水线只启用抖音，B站 API、Publisher 与历史数据保留。

项目不面向公网，也不是多用户 SaaS。

## 2. 主流程

```text
上传本地视频 / 选择 NAS 或本地已有视频
→ 创建独立任务目录
→ 提取音频
→ 生成逐句时间戳转写
→ AI 分析高光候选
→ 人工审核候选片段
→ 保存选择并按需生成新切片版本
→ 准备标题、简介、话题和封面帧
→ 核对平台账号、可见范围和排期
→ Scheduler 领取任务
→ Windows Chrome Worker 执行抖音 / B站投稿
→ 保存成功、失败或人工复核证据
```

## 3. 存储与数据

生产视频、音频、转写、切片和发布包统一保存在用户配置的任务存储目录中。仓库中的默认值可能使用历史 E 盘目录，外部用户应在 `.env` 中改为自己的路径。

以下数据不应进入 Git：

- `.env`
- SQLite 数据库
- 原视频、音频、转写和切片
- 发布包和运行日志
- 浏览器 Profile、Cookie、storage state 和失败截图

软删除与永久删除分开。永久删除只能清理项目托管产物，不应删除外部唯一原片；运行中的转写、切片或发送任务不能被永久删除。

## 4. AI 与转写

### 转写

- 火山引擎远程转写。
- faster-whisper 本地转写。
- 输出逐句时间戳文本，供 AI 分析、字幕和审核复用。

### AI 选片

- 受控本机 Codex CLI（默认推荐，不改变 Codex 提供商或认证配置）。
- 远程 OpenAI-compatible / DeepSeek。
- 本地 Ollama。
- 通用内容价值模式。
- 综艺笑点优先模式。
- 长内容分段分析、合并、去重和排序。

AI 结果只提供候选，不应跳过人工审核直接投稿。

## 5. 审核、切片与字幕

候选片段可以：

- 启用或禁用。
- 修改标题和摘要。
- 调整开始与结束时间。
- 保存审核结果。
- 在选择变化、文件缺失或版本不一致时生成安全的新切片版本。

字幕工作台保留 ASS / FFmpeg 成片能力。v2.1 全自动主流程暂不强制生成、烧录或叠加字幕，避免字幕失败阻断整条发布流程。

## 6. 发送中心

发送中心分为三部分：

1. **内容准备**：视频、标题、简介、话题、候选封面、账号和可见范围。
2. **排期计划**：批量预览、每日时间窗口、跨午夜、月历详情和续接最晚排期。
3. **执行记录**：等待、执行、成功、失败、导出和人工复核证据。

当前页面和自动创建范围固定为 `douyin`。页面会忽略 `platform=bilibili`，任务同步和全自动流水线也只新建抖音记录；已有 B站任务不会被删除、重写或降级，B站 API、Publisher 和 80 字标题能力继续作为后端兼容能力保留。

抖音内容统一使用规则版本写入 `provider_response.metadata_policy_version`：标题不超过 30 字，AI 目标 18～26 字；标签 4～6 个且每个 2～3 字；简介 15～35 字。保存、AI 生成、排期预检和最终 Publisher 共用同一校验，`description/caption` 与 `tags/hashtags` 始终成对同步。

升级后首次打开页面会调用幂等接口 `POST /api/publish/jobs/metadata/upgrade-pending-douyin`。接口在首次写入前创建 SQLite 备份，只处理有效输出、未排期的抖音 `DRAFT / WAITING` 草稿；失败项记录版本和失败原因但保留原文，避免自动重复调用，用户仍可手动重试。

`platform` 只表示目标平台：

- `douyin`
- `bilibili`

`publish_mode` 表示执行方式：

- `local_browser`：默认，通过 Windows Chrome Worker 执行。
- `manual_export`：只导出本地发布包。
- `opencli_publish`：旧兼容模式，默认关闭。

`local_browser` 失败时不会静默回退到 `manual_export`。

## 7. Scheduler 与 Worker

应用启动时会启动 `PublishScheduler`，默认按配置间隔扫描 `publish_jobs`。

Docker 中的 FastAPI 通常通过：

```text
http://host.docker.internal:8765
```

调用 Windows Worker。

账号浏览器目录：

```text
data/browser_profiles/{platform}/{account_id}
```

不同平台和账号使用独立 Profile。Cookie、storage state、截图和 Worker 日志必须保持在本机并被 Git 忽略。

健康检查：

```text
GET /api/publish/scheduler/health
```

手动执行一次调度扫描：

```powershell
.\.venv\Scripts\python.exe -m app.publish_scheduler run-once
```

持续运行独立调度器：

```powershell
.\.venv\Scripts\python.exe -m app.publish_scheduler run
```

## 8. 发布状态

核心原则：页面动作完成不等于平台发布成功。

- `WAITING`：内容已准备，尚未进入执行。
- `SCHEDULED`：已设置执行时间。
- `PUBLISHING`：Scheduler 已领取，Worker 正在执行。
- `PUBLISHED`：取得平台作品 ID、稿件 ID、作品链接或其他明确成功证据。
- `EXPORTED`：只完成本地发布包导出，不代表平台发布成功。
- `FAILED`：已确认失败。
- `NEED_REVIEW`：登录、验证、风控或结果不确定，需要人工核对。

遇到 `NEED_REVIEW` 时，应先进入平台创作者中心核对。只有确认未发布后，才能标记失败并创建新任务；不要直接重复上传。

## 9. 排期语义

前端固定显示北京时间。无时区输入按 `Asia/Shanghai` 解释，数据库保存带时区的 UTC ISO 8601。

“立即发送”同样先写入当前时间并设为 `SCHEDULED`，随后由统一 Scheduler 和 Publisher 执行。

排期预览请求示例：

```json
{
  "job_ids": ["job-a", "job-b"],
  "action": "apply",
  "start_at_local": "2026-08-03T09:00",
  "timezone": "Asia/Shanghai",
  "interval_minutes": 180,
  "daily_start_time": "09:00",
  "daily_end_time": "21:00",
  "confirmed_schedule": []
}
```

推荐流程：

1. 调用 `POST /api/publish/schedules/preview`。
2. 展示并确认 `scheduled_at_local`、`scheduled_at_local_display` 和 `scheduled_at_utc`。
3. 把预览返回的精确时间列表作为 `confirmed_schedule` 提交到 `PATCH /api/publish/jobs/schedule-batch`。
4. 清除排期时提交 `action=clear`。

## 10. 单条真实灰度

1. 启动项目，确认 Scheduler 和 Windows Worker 健康。
2. 在账号管理中新增抖音或 B站账号。
3. 在系统 Chrome 独立窗口中人工完成登录和平台验证。
4. 只选择一条低风险测试视频。
5. 核对标题、正文、话题、封面、账号、可见范围和北京时间。
6. 执行一次投稿并等待明确成功证据。
7. 遇到验证码、风控或结果不确定时停止自动操作，进入人工复核。

平台页面会变化，不应把一次账号验证结果视为永久兼容。

## 11. 测试

```powershell
pytest -v
```

基础检查：

```powershell
python -m compileall app
python scripts/test_ai_json_validation.py
python scripts/test_mock_transcript_analysis.py
python scripts/test_transcript_markdown_format.py
```

测试环境应使用独立 SQLite 数据库和 Mock，不连接真实账号，不打开真实发布浏览器，也不触发投稿。

## 12. 安全边界

- 不保存平台账号密码。
- 不绕过二维码、短信、验证码、滑块、登录失效或平台风控。
- 不在结果不确定时自动重试上传。
- 不默认把 FastAPI 或 Worker 暴露到公网。
- 远程 AI 或转写服务的数据处理范围由用户自行选择并确认。

详细要求见仓库根目录的 [SECURITY.md](../SECURITY.md)。
