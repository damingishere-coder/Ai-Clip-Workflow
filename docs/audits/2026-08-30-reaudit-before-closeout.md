# NiuMa Studio 第二次工程复检报告

> 复检日期：2026-08-30  
> 复检性质：整改后的第二次只读体检，不修改生产代码  
> 当前快照：`codex/feat-content-review-feedback-loop` / `e2ca3907f1d9f2c8c19d744f3061693195dbbd76`  
> 对比基线：`PROJECT_AUDIT.md` 最终 P0/P1 收口结论（`78/100 · 稳定 V1`）与上次 Codemap 基线 `56f4d501087860145412fa6ecc411252ae8e877e`  
> 方法：Codemap 全模块复扫 + Code Overhaul FULL AUDIT + SonarQube 26.8 实扫 + 本地隔离回归验证

## 1. 最终结论

本轮不能直接判定为“已经可以停止全部优化”。原有 P0 没有回归，绝大部分 P1 机制也仍然有效；但最新 2.2 内容复盘、AI 和迁移改动引入了几项高置信问题，其中两项触碰了上轮 P1 的核心契约。

- 上次 13 个 P0/P1 条目中，**11 个保持完整关闭，2 个被新代码局部重新打开**：P0 为 `3/3`，P1 为 `8/10` 完整关闭、`2/10` 部分回归。
- 上次列出的 11 类 P2 中，**1 类已完整解决**（本机工具版本与声明一致），其余主要是按计划保留的结构债；其中可观测性、备份恢复和运维门禁有改善，但仍未完整关闭。
- 上次唯一残留的 HIGH——旧任务目录迁移脚本——已经通过入口无条件拒绝而安全停用，不再属于当前可执行风险。
- 当前发现 **1 条 Codemap HIGH、63 条 MED、17 条 LOW**。主报告只保留其中当前真正值得处理的 6 组问题，不建议为其余格式、命名或纯结构清洁度继续扩大战线。
- 当前综合健康度为 **72/100**，较上次 `78/100` 下降 6 分。项目仍是可用的本地单用户产品，但当前分支还不能作为“稳定 2.2 候选”结束本轮。
- 建议停止全面重构和代码洁癖式优化，只完成本报告第 6 节的窄范围修正及回归测试；全部通过后即可停止本轮优化。

## 2. 本次复检快照与范围

### 2.1 代码变化

| 项目 | 上次 | 当前 | 变化 |
| --- | ---: | ---: | ---: |
| Codemap 模块 | 13 | 14 | 新增 `content_review` |
| Codemap 跟踪行数 | 55,768 | 61,805 | +6,037（+10.8%） |
| Codemap 跟踪文件 | 132 | 139 | +7 |
| Codemap 平均分 | 68.8 | 65.4 | -3.4 |
| Git 变化 | — | 21 个提交、119 个文件 | 14,107 行新增、1,151 行删除 |

当前 HEAD 比真实默认分支 `origin/master` 超前 9 个提交。现有 PR #65 的 base 是 `fix/douyin-official-export-sync`，不是 `master`；该 base 分支本身没有开放 PR，且 PR #65 返回 **0 个 GitHub checks**。因此本报告把本地验证与 CI 状态明确分开：本地测试通过，不等于这批代码已经通过仓库 CI 或可以合并发布。

### 2.2 审计边界

- 没有调用真实 AI Provider、火山引擎、抖音/B站投稿、真实发布或生产数据库写入。
- 活动 SQLite 只通过只读连接检查，结果为 `quick_check=ok`、`foreign_key_check=0`。
- 没有修改任何生产代码、配置、数据库或外部平台状态。
- 本轮只更新 Codemap 审计产物并新增本报告；没有提交、Push、创建 PR 或合并。

## 3. 上次 P0 / P1 问题复核

### 3.1 P0：3/3 保持解决

| 上次问题 | 当前状态 | 本轮证据 |
| --- | --- | --- |
| P0-1 测试可能误用活动库 | 已解决，未回归 | `tests/conftest.py` 继续强制数据库、媒体和发布包进入 pytest 临时 sandbox；全量 854 项测试通过。 |
| P0-2 活动库 17 条外键违规 | 已解决，未回归 | 当前 `data/workflow.sqlite3` 为 10,276,864 字节；只读 `quick_check=ok`，外键违规 `0`。 |
| P0-3 永久删除先删文件后提交数据库 | 已解决，未回归 | 当前删除路径继续使用隔离目录、manifest 与恢复流程；未发现重新回到不可恢复先删文件的实现。 |

### 3.2 P1：8/10 完整关闭，2/10 部分回归

| 上次问题 | 当前状态 | 结论 |
| --- | --- | --- |
| P1-1 Secret DTO | 保持解决 | 读取接口不再回显原始 Secret，未发现新回显入口。 |
| P1-2 本地鉴权与部署边界 | 保持解决 | loopback、Bearer 与 Origin 边界仍在；未发现重新默认信任外网写请求。 |
| P1-3 任务目录与媒体根边界 | 主问题保持解决 | HTTP 媒体和任务目录边界仍封口；内部 `resolve_video_file_path` 对数据库既有绝对路径仍有 P2 残余，不构成本轮重新打开原 P1。 |
| P1-4 Workflow Job lease fencing | 主问题保持解决 | owner/token/状态条件更新仍在；文件 checkpoint 的检查与 replace 之间仍有 TOCTOU，但没有证据表明数据库 fencing 已被移除。 |
| P1-5 Scheduler / Worker execution 幂等 | 保持解决 | execution journal、execution_id 与不确定态人工复核仍在；旧 execution 不能直接覆盖新终态。 |
| P1-6 状态转换与切片原子性 | 保持解决 | active run、切片提交和旧成片保留机制仍在；剩余问题是产物真实性校验和 God Service。 |
| P1-7 转写复用与取消 | 主问题保持解决 | source/checkpoint 与取消语义仍在；大文件弱指纹、绝对时限和 corrupt progress 属于残余 P2。 |
| P1-8 AI 部分结果、质量门禁与成本 | **部分回归** | 门禁仍 fail closed，但长直播成功结果没有写 `quality_degraded`，会被门禁判为 `quality_degraded_missing`，合法结果无法进入切片。 |
| P1-9 DOM XSS 与统一鉴权请求 | 保持解决 | 新内容复盘页面主要使用 `textContent`；未发现恢复原始高置信 DOM XSS。 |
| P1-10 迁移 ledger 与 fail closed | **部分回归** | ledger 框架仍在，但新 `content_feedback_loop` 迁移在事务内调用 `executescript()`，结构会在验证和账本前提交，破坏上轮建立的原子契约。 |

按“原问题是否在当前最新代码中仍完整关闭”统计，稳定解决率为 **11/13（84.6%）**。这里没有把新问题伪装成“旧问题从未修好”：上轮整改本身有效，本轮是后续功能再次穿透了两个既有契约。

## 4. 上次 P2 状态复核

| 上次 P2 类别 | 当前状态 | 是否值得本轮继续 |
| --- | --- | --- |
| Publish Service God Component | 仍在，约 4,800 行 | 不做全面拆分；只在修发布事务时顺带抽取小边界。 |
| 前端 God Scripts | 仍在，前端模块 15,378 行 | 不换 React/Vue；只修已证实的竞态和轮询。 |
| 多 Service 直接访问 SQLite | 仍在，内容复盘继续扩大 | 只给需要原子提交的聚合增加事务边界，不做全量 ORM。 |
| 字幕批量部分完成 | 仍在 | 本轮不扩展；没有新 HIGH。 |
| Scheduler 全量扫描 / 高频轮询 | 仍在 | 当前本机规模可接受，待数据量增长再处理。 |
| 长媒体内存峰值 | 仍在 | 当前无 OOM 证据，继续观察。 |
| 文件产物非统一原子替换 | 仍在 | 优先级低于本轮三项数据正确性问题。 |
| 可观测性不足 | 有改善但未关闭 | readiness 已增强；Worker stderr 丢弃和 fallback 静默仍值得以后触碰相关模块时补。 |
| 无 Coverage / 渐进类型检查 | 仍在 | Coverage 仍无输入；先补本轮三个回归测试，不追求漂亮总覆盖率。 |
| 当前文档与历史文档混排 | 仍在 | `docs/TASK_FLOW.md` 顶部与旧 v2.1 行为并存；不影响运行，本轮不重写。 |
| 本机工具版本漂移 | **已解决** | 当前 pytest `9.1.1`、Ruff `0.16.1` 与 `requirements-dev.txt` 一致，`pip check` 通过。 |

P2 不应被当作本轮继续重构的理由。除与第 6 节修复直接相邻的代码外，其余可以明确延期。

## 5. Codemap 复扫结果

14 个模块全部以当前内容哈希重新审计，`needs_audit=0`、`stale=0`、`unaudited=0`。

| 模块 | 上次 | 当前 | 变化 | 主要原因 |
| --- | ---: | ---: | ---: | --- |
| AI Selection | 70/C | 55/D | -15 | 长直播完整性字段回归、checkpoint 文件/lease TOCTOU、反馈归因风险 |
| Frontend UI | 62/C | 57/D | -5 | 内容复盘预览竞态、实验归属部分成功、AI 轮询与质量提示仍不完整 |
| API Runtime | 63/C | 58/D | -5 | async 路由继续执行同步长任务，新增官方导出最长可阻塞约 30 分钟 |
| Content Review | 新模块 | 61/C | 新增 | Prompt 归因、观看比例口径和官方导入边界存在缺口 |
| Transcription | 64/C | 63/C | -1 | 绝对超时、弱指纹和大服务残余 |
| Task Review & Cut | 68/C | 63/C | -5 | 成片真实性、active run 与并发执行门禁仍不完整 |
| Subtitle | 64/C | 63/C | -1 | chunk 损坏静默跳过、跨片段部分完成 |
| Publish Center | 65/C | 64/C | -1 | 约 4,800 行中心服务、旧状态和兼容发布入口 |
| Pipeline & Queue | 66/C | 65/C | -1 | 文件 checkpoint 与 lease 非原子、Worker stderr 丢失 |
| Persistence | 72/C | 68/C | -4 | 新迁移事务被提前提交、旧库 Prompt 外键不等价 |
| Media & Storage | 78/B | 73/C | -5 | crash recovery manifest 与内部路径边界仍有限 |
| Ops & Delivery | 68/C | 73/C | +5 | 危险旧迁移入口已硬停用，备份与 readiness 改善；CI 分支门禁仍有缺口 |
| Publish Scheduler | 74/C | 74/C | 0 | 状态机稳定，规模化扫描和停机超时债保留 |
| Publisher Worker | 80/B | 79/B | -1 | 防重复主链保持，旧 journal 与诊断边界仍有极窄窗口 |

当前平均分 **65.4/100**，分布为 `1 B / 10 C / 3 D`。分数下降不是因为格式或命名被重新扣分，而是新代码扩大了内容复盘、迁移和 AI 结果契约，并出现了可复现的正确性问题。

## 6. 当前仍值得处理的问题

### 6.1 必须在“停止本轮优化”前处理

#### A. 长直播成功结果会被完整性门禁判坏

- 位置：`app/services/ai/long_live_talk_analyzer.py:251-278`、`app/services/ai_analysis_workflow_service.py:120-132,1473-1489`
- 证据：analyzer 生成的 meta 有 `analysis_incomplete`，但没有 `quality_degraded`；工作流原样合并，切片校验要求该字段必须为布尔值。
- 本轮复现：其余窗口、覆盖率和失败清单全部完整时，校验仍返回 `analysis_incomplete=True`、`quality_degraded=True`、`integrity_error=quality_degraded_missing`。
- 影响：长直播真实 AI 已成功且结果完整，仍无法进入切片；用户可能误以为 Provider 或候选失败并重复运行。
- 最小处理：补齐 canonical meta，并增加“完整长直播结果可切片”的回归测试。不要放宽 fail-closed 校验。

#### B. 新内容反馈迁移破坏事务原子性，旧库 Prompt 外键不等价

- 位置：`app/db/database.py:1533-1570,1644-1684`、`app/db/database.py:367-385,1286-1310,1460-1512`
- 证据：`BEGIN IMMEDIATE` 后调用 Python `sqlite3.executescript()` 会隐式提交。本轮用内存 SQLite 复现：随后 `rollback()` 后刚创建的表仍存在。
- 影响：verify 或 ledger INSERT 失败时，Schema 已改变但账本未记录；并发启动和后续重试无法再依赖“Schema + verify + ledger 同事务”。升级旧库只增加 `prompt_version_id TEXT`，没有新库声明的 FK，悬空 Prompt 版本会让内容归因静默缺失。
- 最小处理：把新迁移改成不隐式提交的单语句执行或明确的受控迁移事务；为升级旧库设计可验证的等价约束，并补失败回滚、并发和 FK 结构测试。

#### C. 显式反馈绑定当前 active run，可能污染 Prompt 归因

- 位置：`app/services/clip_feedback_service.py:87-121`
- 证据：显式反馈保存时重新按 task 查询当前 active run，而不是使用候选的 `source_analysis_run_id`；同文件 review-toggle 路径已经正确使用候选来源。
- 影响：恢复旧候选、分析完成切换 active run 或并发操作时，反馈会记到新的 Run/Prompt，直接污染 2.2 Prompt 效果统计。
- 最小处理：统一以候选来源 run 为准，缺失来源时 fail closed 或标记未归因；增加“旧候选 + 新 active run”测试。

#### D. 官方作品 Prompt 指标时长口径不一致

- 位置：`app/services/content_review_service.py:417-429,478-491,1208-1234`
- 证据：官方 Excel/Worker 导入把 `duration_seconds` 固定为 `None`；Prompt 对比只用该字段计算平均观看比例，而诊断和实验路径已经使用匹配切片的 `effective_duration` fallback。
- 影响：作品已正确匹配、切片时长也存在时，Prompt 对比仍显示空观看比例，可能让用户错误判断某版 Prompt 没有有效数据。
- 最小处理：统一三条统计路径的时长来源与缺失语义，并用同一官方导入样本做对照测试。

#### E. 官方导出同步可阻塞 FastAPI 事件循环最长约 30 分钟

- 位置：`app/routers/content_review.py:59-75`、`app/services/publishers/worker_client.py:64`
- 证据：`async def` 路由直接运行同步 `urlopen`，配置超时为 1,800 秒，之后还同步匹配并提交 SQLite。
- 影响：Windows Worker 卡住时，单个请求可阻塞同一事件循环，页面、状态查询和其他管理请求一起失去响应。
- 最小处理：移入线程池或持久 Job，并保留明确超时、取消和结果状态；不需要为此重写整个 API 层。

#### F. 当前堆叠 PR 没有 CI 证据

- 位置：`.github/workflows/ci.yml:3-7`、GitHub PR #65
- 证据：工作流只监听目标为 `master` 的 PR；#65 目标为 `fix/douyin-official-export-sync`，返回 0 个 checks，该 base 又没有开放 PR。
- 影响：当前 2,400 行新增闭环只能证明本机测试通过，无法证明 Linux、Windows host smoke 和 Docker image smoke 已在 GitHub 环境通过，也没有清晰进入默认分支的完整链路。
- 最小处理：整理堆叠 PR 基线或为受控堆叠 PR 提供等价 CI 触发，确认最终进入 `master` 的顺序；不要自动合并。

### 6.2 可以延期，不应阻止收口

- AI/切片/字幕前端轮询的超时、重试和刷新体验。
- 内容复盘预览切换时的旧响应覆盖、发送中心三请求部分成功提示。
- Pipeline 文件 checkpoint 与 SQLite lease 之间的严格原子性、Worker stderr 摘要。
- Publish Service、Content Review Service、Database、PipelineEngine 和原生前端的结构拆分。
- Scheduler SQL 到期过滤、大媒体流式处理、全项目格式化和渐进类型检查。

这些问题应在下次真实触碰对应模块时局部处理，而不是继续单独开一轮全面优化。

## 7. SonarQube 前后指标

本轮使用与上次相同的 SonarQube Community Build `26.8.0.126808`，Scanner 为 `8.1.0.6389`，扫描 revision 为 `e2ca390`。Docker Registry 当时不可达，因此改用 SonarSource 官方同版本本地包和临时 H2 审计实例；扫描范围、project key 与上次保持一致。

| 指标 | 上次 | 当前 | 变化 |
| --- | ---: | ---: | ---: |
| ncloc | 38,963 | 52,787 | +13,824（+35.5%） |
| 导入测试 | 500 | 854 | +354（全部成功） |
| Issues | 510 | 654 | +144（+28.2%） |
| Bugs | 29 | 32 | +3 |
| Vulnerabilities | 2 | 1 | -1 |
| Security Hotspots | 0 | 0 | 0 |
| Code Smells | 479 | 621 | +142 |
| Duplication | 0.2% | 0.3% | +0.1 个百分点 |
| Coverage | 0.0% | 0.0% | 仍无报告输入 |
| Cognitive Complexity | 7,307 | 10,189 | +2,882（+39.4%） |
| Cyclomatic Complexity | 7,608 | 10,280 | +2,672（+35.1%） |
| Technical Debt | 3,499 分钟 | 5,383 分钟 | +1,884 分钟（约 +31 小时 24 分） |
| Maintainability Rating | A | A | 不变 |
| Reliability Rating | C | C | 不变 |
| Security Rating | D | D | 漏洞减少，但评级未变 |
| Quality Gate | OK / 无条件 | OK / `conditions=[]` | 仍无实质门禁 |

严重度分布由 `4 Blocker / 134 Critical / 316 Major / 56 Minor` 变为 `4 Blocker / 221 Critical / 366 Major / 63 Minor`。绝对数量明显增加，但要结合代码规模：

- Issues 密度从约 `13.1/千行` 降至 `12.4/千行`，Code Smell 密度从约 `12.3/千行` 降至 `11.8/千行`；说明新增代码没有让所有静态问题按同等比例恶化。
- Cognitive Complexity 密度从约 `187.5/千行` 升至 `193.0/千行`，技术债密度从约 `89.8 分钟/千行` 升至 `102.0 分钟/千行`；复杂度和规则修复成本确实增长，不只是项目变大。
- 当前 32 个 Sonar Bugs 中，29 个是 HTML 容器语义、2 个是 label，另 1 个是 Python 正则规则；当前唯一 Vulnerability 仍是 `storage_service.py` 的公共临时目录安全审查。它们不能替代本轮源码复检确认的业务回归。
- 当前复杂度最高文件为 `publish_service.py`（974）、`publish-center.js`（935）、`pipeline_engine.py`（625）、`app.js`（584）、`content_review_service.py`（433）和 `publish_scheduler.py`（427）。这支持“热点集中、按业务渐进处理”，不支持全项目重写。

Coverage 仍未生成：仓库没有 `coverage.xml`，所以 Sonar 的 `0.0%` 表示“没有覆盖率输入”，不能解释为测试实际覆盖率为零。本轮 JUnit 已导入 854 项成功测试。

Sonar 的问题数量用于观察静态规则趋势，不直接等同业务 P0/P1。长直播 meta、迁移隐式提交、Prompt 归因和前端竞态均是源码审计发现，不能仅凭 Quality Gate 或 Maintainability Rating 判断已经稳定。

## 8. Code Overhaul 工程结论

### 8.1 架构范围判断

当前本地单体架构仍合理。项目是 Windows 本机、单用户、SQLite、人工审核和平台风控边界下的生产工具，不需要微服务、全量 ORM、React/Vue 或消息队列重写。真正的问题是少数跨事务、跨进程和异步协调契约，而不是“技术栈太旧”。

### 8.2 质量与依赖

- 最大维护热点仍集中在 `publish_service.py`、`content_review_service.py`、`database.py`、`app.js`、`PipelineEngine` 和字幕/转写服务。
- 当前 pytest `9.1.1`、Ruff `0.16.1` 与声明一致，`pip check` 无冲突。
- 可更新项主要是 pip、Pydantic、uvicorn、Ruff 的小版本，以及一个 pyee 主版本；本轮没有证据要求立即升级依赖。
- `ruff format --check` 显示 134 个文件会被格式化、27 个已符合格式。它不影响正确性，本轮不应制造大格式化 diff。

### 8.3 测试与真实风险

全量 854 项测试全部通过，但没有覆盖本轮 A、B、C 三个关键回归。测试数量增长是好事，仍需把“业务不变量”补成直接断言，而不是继续增加只验证返回 200 或页面元素存在的测试。

真实 Provider、真实 FFmpeg 长任务、Chrome 登录/风控和真实投稿仍不在本轮执行范围；这符合安全边界，但也意味着本报告不能把本地 mock 测试写成真实发布验收。

### 8.4 收口优先级

| 顺序 | 工作 | 收益 | 成本 | 变更风险 | 结论 |
| ---: | --- | --- | --- | --- | --- |
| 1 | 修长直播 canonical meta | 高 | 低 | 低 | 立即处理 |
| 2 | 修迁移事务与旧库 Prompt 约束 | 高 | 中 | 中 | 立即处理，先在副本演练 |
| 3 | 修反馈来源 run 与时长统计口径 | 高 | 低到中 | 低 | 立即处理 |
| 4 | 把官方导出同步移出事件循环 | 中高 | 低到中 | 低 | 本轮收口前处理 |
| 5 | 恢复堆叠 PR 的 CI 链路 | 高 | 低 | 低 | 合并前必须完成 |
| 6 | 全面拆 God Service / 全项目格式化 | 低（当前） | 高 | 高 | 不做 |

## 9. 验证结果

| 检查 | 结果 |
| --- | --- |
| Pytest 收集 | 854 项 |
| Pytest 全量 | `854 passed`，9 warnings，约 167.5 秒 |
| 严格无 Chrome 子集 | `850 passed, 1 deselected`，约 108 秒 |
| Ruff lint | 通过 |
| Ruff format check | 未通过：134 个文件会被格式化；非本轮阻断项 |
| Python compileall | 通过 |
| JavaScript 语法 | 5/5 通过 |
| PowerShell 语法 | 20/20 通过 |
| Docker Compose | 默认 / dev / demo 合并配置全部通过 |
| pip check | 通过 |
| git diff --check | 通过 |
| 活动 SQLite 只读检查 | `quick_check=ok`、FK 违规 0、5 条迁移账本 |

9 条 warnings 主要来自 Starlette/httpx 建议、Pydantic V1 `@validator` 和 `__fields__` 兼容访问，均不是本轮阻断问题。

## 10. 当前健康度评分

| 维度 | 上次 | 当前 | 说明 |
| --- | ---: | ---: | --- |
| 架构合理性 | 7 | 7 | 本地单体仍匹配范围；中心服务继续变大。 |
| 业务逻辑 | 9 | 8 | 2.2 闭环完整，但长直播与 Prompt 指标出现正确性回归。 |
| 代码质量 | 6 | 6 | lint 通过；Codemap 平均分下降且 D 模块增至 3 个。 |
| 数据设计 | 8 | 7 | 活动库健康，但新迁移事务与旧库 Prompt 约束不等价。 |
| 稳定性 | 8 | 7 | 核心 fencing 保持；30 分钟事件循环阻塞和前端竞态新增。 |
| 测试 | 9 | 8 | 854 项全绿，但关键业务不变量仍漏测，当前 PR 无 CI。 |
| 安全性 | 8 | 8 | P0、Secret、loopback、Origin 和人工复核边界保持。 |
| 性能与资源 | 7 | 6 | 新同步导出可阻塞事件循环；原有全量扫描和大文件峰值仍在。 |
| 可观测性 | 7 | 7 | readiness 改善；stderr、fallback 和前端停止轮询仍不足。 |
| 文档与可维护性 | 9 | 8 | 文档及时更新，但历史/当前混排，新增模块扩大维护面。 |
| **总分** | **78/100** | **72/100** | **可用，但尚未达到当前 2.2 分支的稳定收口线。** |

## 11. 是否可以停止本轮优化

**现在还不可以完全停止。** 原因不是还有很多可重构代码，而是 A～F 中仍有可复现的业务正确性、迁移原子性和交付证据问题。

建议的停止条件只有四类：

1. 修复长直播 meta、迁移事务/Prompt 约束、反馈来源 run/时长口径。
2. 把官方导出同步移出事件循环，并为内容预览或实验归属至少补上请求代际/部分成功保护。
3. 为上述问题增加直接回归测试，重新通过 Ruff、定向测试和全量隔离测试。
4. 整理 PR 链路，让最终进入 `master` 的改动获得 Linux、Windows host smoke 与 Docker image smoke 的真实 CI 结果。

满足这些条件后，**应停止本轮优化**。其余 God Service、格式化、全量类型检查、Scheduler 规模化和大媒体流式化都可以明确延期，不需要为了追求分数继续修改。

## 12. Git 与交付状态

- 初始快照分支：`codex/feat-content-review-feedback-loop`
- 初始与最终 HEAD：`e2ca3907f1d9f2c8c19d744f3061693195dbbd76`
- 真实默认分支：`master`
- 生产代码修改：无
- Commit / Push / 新 PR：无；本轮只审计，不创建提交
- 现有 PR：#65，OPEN / CLEAN，但 base 非默认分支且无 checks
- 合并状态：未合并，也未执行任何自动合并操作
