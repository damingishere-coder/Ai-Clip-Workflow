# 项目当前进度

## 当前开发：v2.6 PR2 批次任务与原片导入（尚未部署）

PR #112 的三项 CI 通过并合并 `a3f9ef1`；当前短期分支 `codex/v2.6-batch-import`。已接事务内批次/Task/Workflow Job 创建、配置冻结、受租约保护的原片复制与真实哈希/媒体校验、跨浏览器刷新重试、素材池批次进度。第 21 项迁移仅在隔离库执行，正式仍 v2.5.5 / `c0d7962`、19 项迁移。

批次默认只导入并等待逐步处理；自动生产暂不开放，人工审核/排期边界会在后续 PR 一并接入。新增定向 17 项、旧 Profile/Provider/素材/迁移 56 项通过；真实十条短视频复制解码及原片保留通过。完整离线基线 1283 passed（377.80 秒）、最终批次/浏览器增量 15 passed、最终 cuts-async 门禁单项通过；Ruff、编译及 12 JS 通过。只读审查无确定性阻塞，待独立 PR/CI。全局重型并发、统一 Inbox 和首页工作台仍属后续 PR。

## 已合并：v2.6 PR1 素材目录与登记（尚未部署）

v2.5.5 证据 PR #111 最终 `d3fae7d` 三项 CI 通过，合并 `884af4d`；从最新 master 建立 `codex/v2.6-material-catalog`。首步单独验证文件访问与登记边界：目录预览、明确确认、元数据版本、重复/并发复用、响应丢失重试和素材池页面。后续 PR 才在原 Workflow Job 接任务/复制及生产队列；本 PR 不生成生产任务、不复制原片、不调用 AI。

新增功能/迁移/1440 与 390 Chrome 定向 14 项通过（11.79 秒）；完整回归 1266 passed、0 failed/0 skipped（348.90 秒），Ruff、编译、11 JS 通过，只读审查无本 PR 阻塞。全量后仅补目录输入框现有 control 样式，两宽度 Chrome 再测 2 passed（8.79 秒）。首轮迁移夹具遗漏旧 task_dir_name，被既有初始化补齐；修正为旧正常任务后严格比较通过。证据在忽略目录 `data/acceptance/v26-material-catalog-regression/`；准备独立 PR/CI。正式仍 v2.5.5 / `c0d7962`、19 项迁移，第 20 项尚未部署。

## 当前：v2.5.5 已发布与部署（2026-09-15 10:08）

[PR #110](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/110) 版本提交 `c0d7962fe4ee23e81bc93669bb5ff1777502eb63` 已通过 PR 与最终主干三项 CI；[v2.5.5 Release](https://github.com/damingishere-coder/Ai-Clip-Workflow/releases/tag/v2.5.5) 已发布为 Latest，指向同一提交。v2.6 当前开发进度见本文顶部；正式服务仍保持本版本。

- Windows 11 / PowerShell 7.6.5 / Docker Desktop 4.40.0 的最终提交实机门禁 17 项通过；Demo 3 任务、6 候选、6 manual_export 草稿。有效证据位于忽略目录 `data/acceptance/v255-release-tests/windows-ps7-attempt2/`。
- 10:08:02–10:08:46 沿用原 RunDock Web/Worker 完成维护。实际版本均为 2.5.5，监听进程父链对应原管理记录与 `Ai-Clip-Workflow-offline-runtime`；deep readiness=ready，实际数据库路径与启动参数一致。
- 正式 SQLite 14→19，integrity=ok、FK=0。38 张旧表原字段原记录保留，只有旧迁移表新增 5 行；新报告、Challenger 与策略事件均为 0。根目录/运行副本配置哈希、E 盘 1322 文件（17,393,147,651 字节）元数据保持不变。
- 1440/390 Chrome 共 12 次页面检查通过，6 个实际静态资源哈希匹配运行源码；旧任务、历史候选评价、五 Profile、视觉默认关闭正常。人工统计显示数据不足，默认 keep 未冒充人工接受。所有浏览器请求只读，未造正式报告/实验，未调用真实 AI、同步或发布。完整实验确认流程已在隔离夹具中验证。
- 首次门禁脚本虽返回通过，但额外校验发现其数据库保护指向不存在的验收本地库，已拒绝部署并恢复旧服务。原因是正式 DB 路径由 RunDock CLI 注入；仅在隔离 `.env` 副本补齐实际绝对路径后重做完整门禁。首次证据保留于 `windows-ps7/`，不能作为本版发布证据。
- 回退优先保留新版二进制及增量结构，关闭新入口或向前修复；旧程序会拒绝未知迁移，不删除账本、不自动覆盖旧备份。WAL-safe 备份及隔离恢复记录见下节。真实节目质量与策略表现继续日常补验，未宣称已有提升。

## 历史：v2.5.5 版本交付预检

版本/备份/启动入口预检完成：7 个测试文件 38 passed、0 failed/0 skipped（5.02 秒），Ruff、Python 编译、10 个 JS 和发布脚本 PowerShell 解析全部通过；证据在忽略目录 `data/acceptance/v255-release-preflight/`。此结果不替代最终 Windows 实机门禁。

功能 PR [#109](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/109) 最终 `b0f66fa` 三项 CI 通过（Linux 1229 passed / 23 平台跳过 / 102.59 秒），squash 合并 `da6f9a9`。当前独立分支 `codex/v2.5.5-release-acceptance` 同步 VERSION、Web、Worker、备份清单、中英文 README、门禁与指南为 2.5.5；正式运行仍是 2.5.0 / `384db79`，实际升级、Windows 实机、Tag/Release 均待本版最终提交 CI 后执行。

2026-09-15 09:44 的 WAL-safe 升级备份已校验并恢复到隔离目录（环境不恢复、媒体不复制）：52 tasks、403 candidates、577 outputs、746 publish jobs。真实备份副本顺序升级 14→19、重复 init 均通过；38 张原表旧字段旧记录保留，integrity=ok/FK=0、新报告/Challenger/策略事件 0。私有备份在 `data/backups/v255-release/`，恢复/兼容证据在忽略目录 `data/acceptance/v255-backup-restore/`、`v255-upgrade-probe/`。版本检查进行中，后续先对最终干净 master 做 Windows 门禁，再更新原 RunDock 服务并验证旧任务与新页面；v2.6 尚未开始。

## 当前开发：v2.5.5 PR3c 正式实验与人工启用（尚未部署）

试验任务 PR [#108](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/108) 最终 `846ba66` 三项 CI 全通过（Linux 1219 passed / 21 平台跳过 / 80.50 秒），合并 `8be1195`。从最新 master 建立 `codex/v2.5.5-challenger-experiments`，复用 Content Review 实验与 Prompt 版本，补冻结官方基线、实际 Run/Job 策略核验、人工结论留证及独立启用/回退。

基础完整回归 **1250 passed、0 failed/0 skipped、9 warnings（336.35 秒）**，Ruff、编译、10 JS 通过。审查收尾前置 Run 完整性/成片边界检查、保留旧实验时长口径及调整手机展示后，**59 项定向全部通过（67.83 秒）**，含两种入组 API 和 1440/390 Chrome；此前 21 项迁移/账本回归通过。没有把基础全量冒充收尾后的全量重跑，最终 CI 将检查提交。第 19 项迁移增加实验可空证据与策略操作记录，旧实验不回填；正式 Web/Worker 仍为 v2.5.0 / `384db79`，正式库仍 14 项，未执行新迁移、真实 AI、同步或投稿。版本完成后统一交付，再进入 v2.6。

## 当前开发：v2.5.5 PR3b 显式试验任务（尚未部署）

草稿 PR [#107](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/107) 最终 `3439725` 三项 CI 通过（Linux 1205 passed / 19 平台跳过），合并 `3a7ecd0`。新分支 `codex/v2.5.5-challenger-trial-tasks` 接草稿 → 明确确认 → 原创建任务流程 → 冻结 Job/Run；草稿仍归档，普通选择器不可选，试验不能改绑 Profile/Prompt 或启动全自动流水线。

最终冻结源码完整回归 **1240 passed、0 failed/0 skipped、9 warnings（312.30 秒）**，含桌面/手机 Chrome；Ruff、Python 编译、9 JS 检查通过。首轮发现的旧 followup 参数与迁移测试问题已修复，审查后补基线过期、冻结版本保留及 Run 提交/恢复篡改校验，最终全量涵盖全部修正。证据保存在忽略目录 `data/acceptance/v255-challenger-trials-final/`。第 18 项迁移仅扩展 task_generation_rules 的可空绑定及索引/约束，旧任务未知值不回填。后续独立 PR 再完成官方实验策略核验与单独人工启用；正式 Web/Worker 继续 v2.5.0 / `384db79`，正式库迁移仍 14。

## 当前开发：v2.5.5 PR3a Challenger 草稿（尚未部署）

报告 PR [#106](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/106) 最终 `9314aa4` 已通过 Linux、Windows、Docker 三项 CI（Linux 1199 passed、17 平台跳过），合并 `15a734d`。当前 `codex/v2.5.5-challenger-drafts` 保存人工假设、Prompt 差异、来源报告与完整 Profile/评分版本；草稿单独归档，不修改正式方案。服务/迁移 6 项、1440/390 Chrome 两项及全量 1224 项均通过（233.89 秒，0 failed/0 skipped、9 warnings），Ruff、编译、9 JS 通过，只读审查无阻塞问题。证据在忽略目录 `data/acceptance/v255-challenger-drafts-regression/`；准备独立 PR/CI。

本轮把原 Challenger/实验 PR 拆成草稿与执行两个小 PR。下一步才接试验任务、实际 Run 策略匹配、现有实验及单独人工启用。正式 Web/Worker 仍是 v2.5.0 / `384db79`、迁移 14；新迁移 15–17 尚未在正式库执行。

## 历史开发：v2.5.5 PR2

人工审片 PR [#105](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/105) 的最终提交 `dec9e49` 已通过 Linux、Windows、Docker 三项 CI，合并为 `fd47c2c`；Linux 1188 passed、15 平台跳过。当前独立分支 `codex/v2.5.5-intelligence-reports` 增加官方作品特征、冻结报告和小样本保护，继续进行回归及审查。正式服务仍是 v2.5.0 / `384db79`，尚未迁移第 15/16 项，也没有触发正式 AI、同步或发布。

报告阶段基础全量 1216 passed、0 failed/0 skipped（221.71 秒），95 项定向回归通过（33.58 秒），Ruff/编译/8 JS 通过；随后初始/成片边界校验和报告页面补测 11 passed。桌面与手机 Chrome、迁移失败回滚/幂等/恢复均包含在验证中。最终 PR/CI 状态待完成，证据在忽略目录 `data/acceptance/v255-intelligence-reports-regression/`。

## 历史开发：v2.5.5 PR1

分支 `codex/v2.5.5-human-review-observations` 已补 AI 初始候选快照、明确审片评价、C 级诊断及无账号也可用的人工统计。只增加第 15 项兼容迁移，正式数据库仍为 v2.5.0 的 14 项迁移。全量离线回归 1198 passed（287.34 秒），审查修正后的 107 项定向回归通过（38.83 秒，含桌面/手机 Chrome、迁移与旧流程），Ruff、编译和 7 JS 检查通过；尚未更新运行副本，PR/CI 待完成。只读审查发现的旧 C 观察遗漏与置信样本口径问题已修复并补测。证据在忽略目录 `data/acceptance/v255-human-review-regression/`；统计口径、原片身份未知限制与回退见 [Content Intelligence](docs/CONTENT_INTELLIGENCE.md)。

## v2.5.0 已发布与部署（2026-09-14 22:45）

PR [#102](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/102) 合并视觉集成，PR [#103](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/103) 发布提交为 `384db79e0718ec9e4417016df0ea1fc1d9430330`。[v2.5.0 Release](https://github.com/damingishere-coder/Ai-Clip-Workflow/releases/tag/v2.5.0) 已发布为 Latest，指向同一提交；PR 与合并后主干三项 CI 全通过。

- 最终本地回归 1187 passed、0 failed/0 skipped；Ruff、编译、6 JS 及实际桌面/手机浏览器通过。Linux CI 1174 passed、13 skipped，平台跳过与实机验证分别记录。
- Windows 11、PowerShell 7.6.5、Docker Desktop/Engine/Compose 实机门禁通过。22:45:08–22:45:47 沿用现有 RunDock Web/Worker 记录完成短暂维护，两个实际服务版本均为 2.5.0；监听器父进程链对应原管理记录及 `Ai-Clip-Workflow-offline-runtime`。
- 数据库迁移由 12 升至 14，integrity=ok、外键异常=0；37 张原表的旧字段旧行保留。原有 52 任务全部视觉关闭、没有新增生产视觉证据、旧任务策略未知值保持 NULL。原片与配置保护通过，没有触发生产 AI、同步或发布。
- 1440/390 宽度创建页五 Profile/默认 Prompt/视觉默认关闭正常；旧任务、审片、Content Review、视觉证据页可读、无 JS 错误或横向溢出。实际 app.js、视觉 JS/CSS 与发布副本哈希一致。
- 本机忽略证据：`data/acceptance/v25-pipeline-regression-reviewed/`、`v25-pipeline-probe/`、`v25-release-tests/windows-ps7/`、`v25-backup-restore/`。WAL-safe 升级前备份通过隔离恢复；没有提交数据库、配置秘密、视频或截图。
- 回退优先保留 2.5 二进制及增量结构，关闭视觉入口/运维开关并暂停相关新 Job。实际隔离检查表明旧 2.4 能初始化并读取旧任务，但 readiness 会因未知迁移 13/14 报错，不能直接认定旧二进制可完整回滚；不得删除迁移账本来隐藏错误。整库恢复须停服务、保存当前快照并核对新增数据及外部发布事实。

视觉、访谈和知识模板仍为试用，真实节目质量在日常人工审片中补验。工程与运行验收通过后，下一版进入 v2.5.5；不提前引入 v2.6 队列业务。

## 历史：v2.5.0 版本发布准备

功能 PR #102 已合并 04b3fef，1187 项最终本地测试、Linux/Windows/Docker CI 全部通过。当前仅统一 2.5.0 版本与交付文档，正式服务仍是 2.4.0 / 15f3838、迁移 12；等待版本 CI、Windows 实机门禁和现有 RunDock 服务更新。


## 历史：v2.5 可选视觉集成验收（2026-09-14）

PR #101 已通过最终 Linux/Windows/Docker CI 并合并 `52f1909`，正式 Web/Worker 暂未更新，仍为 v2.4.0 / `15f3838`。当前 `codex/v2.5-visual-pipeline` 已接默认关闭的视觉策略、候选验证、可选综合评审、Run 原子关联、证据页与 TTL 清理。1184 项全量回归（含桌面/手机页面）、Ruff、编译、6 JS 检查通过；补充缓存轮转及康熙不完整文本测试通过。隔离真实视觉＋综合评审两次调用 68.125 秒通过并可复用，审查收尾；尚未运行正式第 13/14 项迁移。实际节目质量没有宣称通过，后续以日常人工审片验证。

## 历史阶段：v2.5 PR2 视觉证据基础（2026-09-14）

抽帧 PR #100 的最终 Linux、Windows、Docker CI 全部通过，合并 `d7a29b7`。随后在 `codex/v2.5-visual-evidence` 实现可选 VisualProvider、候选请求冻结和证据表，继续使用既有 AI checkpoint/lease。当前只在隔离环境验证，没有生产 Analyzer/API 调用入口、没有修改正式数据库或服务。正式 Web/Worker 继续 v2.4.0 / `15f3838`。后续还有 Judge、UI、轮次预算与清理，不能把本阶段表述为 v2.5 已交付。

本 PR 最终验证：1143 项完整回归全部通过，Ruff/编译通过；工具受限的实际图像链路 37.485 秒完成，严格证据落库并成功复用、无重复请求。先前测试夹具失败及修复证据保留在开发日志与忽略的验收目录中。

## 历史阶段：v2.5 PR1 基础验证（2026-09-14）

v2.4 交付账本 PR #99 已合并为 `d3f8054`。独立 `codex/v2.5-frame-sampling-baseline` 开发候选内抽帧，26 项定向测试通过；现有 Codex / gpt-6-astra 一次双图能力测试通过，另有 6 帧康熙候选本地采样通过。没有 Analyzer/API/数据库接入，正式 Web/Worker 仍是已验收的 v2.4.0 / `15f3838`。详细行为、限制和剩余 PR 见 [Visual Signal](docs/VISUAL_SIGNAL.md)，不能将基础模块视为视觉分析已经上线。

## 当前交付：v2.4.0 已发布与部署（2026-09-14 20:04）

PR [#98](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/98) 已合并为 `15f38387b11c477f94d288dc9d4da4736d0226fe`，最终 PR 与合并后主干的 Linux、Windows、Docker CI 均通过。[v2.4.0 Release](https://github.com/damingishere-coder/Ai-Clip-Workflow/releases/tag/v2.4.0) 指向同一提交。1078 项本地回归、pip check、三套 Compose 配置及 21 个 PowerShell 脚本语法检查通过。

Windows 11 / PowerShell 7.6.5 / Docker Desktop 4.40.0 实机验收 17 项全部通过，Demo 为 3 任务、6 候选、6 手动导出草稿；发布门禁通过。使用独立干净 master checkout，未暂存或改写四份用户原审计修改。Demo 配置由示例生成，不含生产凭据；正式配置另存哈希，实际正式数据库及 1322 个托管文件的元数据指纹前后相同。存储检查包含现有 doctor 的临时写入探针，并非媒体内容全量哈希。

20:04 更新原 RunDock 管理的运行副本：Web 8001 与 Windows Worker 8765 的 OpenAPI 均报告 2.4.0，进程链、实际代码目录和 served app.js 哈希已核对。深度 readiness=ready、12 条迁移、integrity=ok、外键异常 0；37 表原行原字段保留，0 新增业务记录。Chrome 1440/390 五模板与试用提示、旧任务、审片、复盘页面验证通过，所有非只读请求被拦截，没有通过验收创建任务或投稿。

本地证据：`data/acceptance/v24-release-tests/windows-ps7/`；备份包位于 `data/backups/v24-release/`，已校验并在隔离目录恢复成功。首次 PowerShell 5.1 未加载 Get-FileHash 的失败报告保留，失败后自动恢复原服务，再使用已验证的 PowerShell 7 重跑通过。回滚使用原代码 `4eaff62`，保留新增数据库结构；不将旧备份覆盖已经发生的发布事实。

工程验收已完成，允许按批准顺序进入 v2.5。三集康熙实际分析与确定性回放证据有效；人物访谈、知识观点继续标试用，真实内容质量与人工盲审保持待验，未补填人工通过。下方按时间保留历史阶段描述，以本节为当前状态。

## 当前开发：v2.4 工程验收政策更新（2026-09-14）

用户明确同意：工程测试、兼容性及运行验收通过即可继续下一版本，真实质量在日常使用中补验，不再要求专门素材或逐条盲审。人物访谈、知识观点在创建页和任务页标“试用”；显示标记不改变冻结 Profile、Prompt、评分或历史哈希。人工审片、字幕、排期/发布边界保留。

继续已有 PR #98 收口 2.4.0 工程交付：同步应用、Worker、备份版本、页面、文档与发布检查；新增试用提示验证。以下旧记录中“人工未完成所以不进入 v2.5”仅反映更新前门槛，已被本节替代；真实质量 pending 仍保留。新提交的测试、CI、实机门禁、Tag/Release 与部署结果在完成后记入本节，不预先宣称已发布。

本轮完整本地回归 **1078 passed / 0 failed**，耗时 218.49 秒；版本与 Chrome 试用提示的 5 项定向检查也通过。Ruff、diff 检查通过。正式服务在这些检查期间仍为 `4eaff62` / 2.3.0；2.4.0 的合并、实机发布与部署尚待本轮后续交付。

## 当前开发：v2.4 PR5 已交付，质量验收待完成（2026-09-14）

PR [#97](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/97) 的 Linux、Windows、Docker CI 全部通过，合并为 `4eaff62`，17:45 更新既有 RunDock Web 8001。正式库迁移保持 12 条，integrity=ok、外键异常 0，37 表原行原字段保持一致、0 新增业务记录；Worker 8765 就绪，旧任务/审片/AI history 可读。备份及实际验收位于 `data/backups/feedback-evidence-deploy-20260914-174451/`。本补丁没有数据库结构变化，也没有改变既有排期。

从最新 master 建立 `codex/v2.4-quality-acceptance-record` 收集 PR6 质量证据。真实康熙分析使用启动时加载的 `a5ece66`，独立进程和数据库；PR5 的代码兼容另由最终确定性回放和 1078 项完整测试验证，不能把这批真实调用标为 `4eaff62` 执行。人工盲审、访谈与知识各两条真实素材仍是版本门禁，未满足前不发布 v2.4、不开发 v2.5。

18:05:38，三集真实分析全部结束：每集 18/18 单元、coverage=100%、12 条候选，共 54 个成功单元；failed_units/invalid_item_count 均为 0，analysis_incomplete/quality_degraded 均为 false，无 heartbeat 异常或不确定单元。三集旧响应回放与三集反馈桥接回放全部一致；后者使用同一隔离反馈比较 PR4 与 PR5，不发生模型调用。原片与转写哈希保持不变，隔离音频/转写副本一致；原片 Range GET 可读，不替代人工观看。[脱敏验收证据](docs/CONTENT_PROFILE_ACCEPTANCE_EVIDENCE.json) 已保存，本机完整审片包为 `data/acceptance/content-profile-v2.4/人工对比材料-完整批次.md`。PR6 保持质量证据草稿，正式版本仍为 2.3.0。

## 当前开发：v2.4 反馈证据与验收收口（2026-09-14）

PR [#96](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/96) 的 Linux、Windows、Docker 检查全部通过，合并为 `a5ece66`，17:19 已部署至原 RunDock Web 8001。迁移 12 条、integrity=ok、外键异常 0，37 表原行原字段保持一致，只追加知识模板/Prompt/版本/账本；Worker 8765 就绪。既有服务进程链及实际 JS 响应哈希已核对；桌面与 390px 创建页五模板切换、旧任务/审片/复盘页均无脚本错误和页面级横向溢出。完整证据和 8 张截图位于 `data/backups/knowledge-profile-deploy-20260914-171848/`。

随后从 master 新建 `codex/v2.4-acceptance-evidence`，补齐验收中发现的实际反馈输入缺口：新综艺 Job 在原事务冻结进入 Judge 的反馈集合（包括空数组），Run 保存同一证据。旧 Job 缺字段继续原路径、不改变 checkpoint 输入指纹；已有后续 Job 复用时验证原快照并比较业务参数，不再按当前反馈重新冻结。没有新增迁移、修改 Prompt 或评分。

最终完整回归 **1078 passed / 0 failed / 0 skipped**（含浏览器），Ruff/compileall 通过；三集历史响应回放在最终补丁后再次全部一致。真实第一集于 17:34 完成 18/18 单元、coverage=100%、12 候选，第二集正在执行；这不是人工质量结论。PR5 的 CI、合并与运行验收以随后交付记录为准。

真实康熙对比已于 17:19 在隔离库启动，使用原 Codex CLI / gpt-6-astra / preset_001，逐集执行，不自动重试失败或不确定调用；正式任务、候选和发布数据不用于写入验收结果。完整实时证据保存在 `data/acceptance/content-profile-v2.4/real-model/`。三集历史响应回放在补丁后仍结果/请求指纹一致。真实分析、人工盲审及访谈/知识各两条素材的质量门禁尚未全部完成；不发布 v2.4、不进入 v2.5，VERSION 仍为 2.3.0。

## 当前开发：v2.4 五模板创建入口（2026-09-14）

PR [#95](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/95) 三项 CI 通过，合并为 `06d4d12`，16:46 已部署至原 RunDock Web 8001。Worker 8765 就绪，迁移 11 条、integrity=ok、外键异常 0；所有旧行原字段不变，仅追加访谈 Profile/Prompt/版本/账本。证据：`data/backups/interview-profile-deploy-20260914-164633/acceptance.json`。

当前独立分支 `codex/v2.4-knowledge-profile-ui` 新增知识观点模板，创建页提供五种内容类型、Prompt、Provider 及对应数量/时长提示。任务和新 Job 冻结实际 Provider 配置身份；默认分析使用任务选择，显式按钮可覆盖本轮，配置漂移在调用前阻断，旧 Job 保持原恢复方式。含认证信息的地址和本地认证路径只保存哈希，不写入新快照。

PR4 完整回归 **1070 passed / 0 failed / 0 skipped**（含两种宽度的 Chrome 创建页与既有浏览器测试）；CI、合并和实际部署在交付后留证，正式版本仍为 **2.3.0**。正式库副本连续初始化至 12 条迁移，37 张现有表的所有原行原字段保持不变，历史 Provider 未回填，证据 `data/backups/knowledge-profile-preflight-20260914-170537/acceptance.json`。三集不同康熙原片完成哈希、历史响应校验及升级前后确定性回放：每集 18 单元、结果与请求指纹一致、0 次模型调用。此回放固定空反馈和已读任务参数，不代表重现了全部历史调用环境；真实人工盲审以及访谈、知识各两条素材检查仍待完成，不进入 v2.5。

## 当前开发：v2.4 人物访谈共享流程（2026-09-14）

PR [#94](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/94) 三项 CI 通过，合并为 `cc8ae63`。16:23 已部署到既有 RunDock Web 8001，Worker 8765 保持就绪；深度检查 ready，迁移 10 条，integrity=ok、外键异常 0。正式库升级前后 **34 张旧业务表所有原字段逐行哈希一致**，旧任务/审片页/分析历史正常返回。历史任务和 Run 的 Profile 关联均未回填。备份与验收：本机 `data/backups/profile-registry-deploy-20260914-162259/`。未触发真实 AI 或投稿。

随后从最新 master 建立 `codex/v2.4-interview-shared-analyzer`：新增访谈独立模板/Prompt、共享召回/扩展/全局评审及通用评分证据。新模板先接 API，五模板创建页统一在 PR4 接入。完整回归 **1047 passed / 0 failed / 0 skipped**，随后两项补充与末尾修改由 **35 项定向测试**覆盖；Ruff/compileall 通过。正式库副本增量迁移至 11 条，所有旧行保持原样，仅追加新版本/预设/账本；证据 `data/backups/interview-profile-preflight-20260914-163632/acceptance.json`。PR3 尚待 CI、合并和运行验收；真实访谈质量仍待两条适合素材及人工检查，产品版本仍为 2.3.0。

## 当前开发：v2.4 Profile Registry 与快照（2026-09-14）

PR [#93](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/93) 已通过 Linux、Windows、Docker 三项 CI，合并为 `4264e31`；纯领域基础无需部署。随后从最新 master 创建 `codex/v2.4-profile-registry-snapshots`。

PR2 已接入三个旧模式 Registry、新任务/Job/Run 版本证据及康熙参数配置化。完整回归 **1033 passed / 0 failed / 0 skipped**（含浏览器），随后新增的执行快照用例单独通过；Ruff/compileall 通过。新迁移在隔离测试与正式库副本上验证，34 张既有表原字段不变、历史版本不回填；正式库目前仍为 9 条迁移、运行代码 `594bc60`，尚未部署。CI、合并及运行验收在交付后更新。访谈/知识尚未接入，正式产品版本继续 2.3.0。

## 当前开发：v2.4 Content Profile 基础（2026-09-14）

已批准按 v2.4 → v2.5 → v2.5.5 → v2.6 分版本实施。首分支 `codex/v2.4-profile-domain-baseline` 基于 `ffdb77f`，新增纯不可变模型、三个旧模式描述与合成兼容测试；尚未接入生产路由，正式版本 2.3.0。

本 PR 无数据库或正式 Prompt 变化、无真实 AI/发布调用，无需部署重启。本地完整回归 **1019 passed / 0 failed / 0 skipped**（包含浏览器用例），Ruff 与 Python 编译通过；CI/合并状态以本分支 PR 为准。真实质量验收尚未执行。16 个顺序 PR 及门禁见 [实施账本](docs/CONTENT_PROFILE_ROLLOUT.md)。四份已有本地审计修改保持原样，不纳入提交。

## 当前修复：第 32 条任务扩展时间范围失败（2026-09-12）

运行任务 `49229f4828f5` 的扩展批次 6 返回 38 秒片段，实际转写边界归一化失败，旧实现却提前记录批次成功。现已将边界校验前移到 checkpoint 写入/复用之前，并明确提示词时长硬边界与时间范围错误分类。正式版本保持 2.3.0。

失败任务、原片和数据库已保存至本机 `data/backups/task32-rebuild-20260912/`。PR [#90](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/90) 已 squash 合并为 `594bc60`，991 项完整回归及 Linux / Windows / Docker CI 全部通过。7:51 已部署至原 RunDock Web 8001（运行目录 `Ai-Clip-Workflow-offline-runtime`），Worker 8765 无需重启。深度就绪 ready、数据库 integrity=ok、外键异常 0。旧任务通过产品删除入口隐藏并清理媒体，新任务 `1ccad93c1e77` 按原参数重新上传启动；新旧原片 SHA-256 一致，其余 49 条任务记录逐字段不变。**重跑验收完成**：7:51:25 创建新任务，7:56:03 完成 23 段转写，8:10:06 AI 通过 18/18 单元、coverage=100%、invalid_item_count=0、failed_units=0、analysis_incomplete=false；生成 12 条候选，8:10:28 产出 5 条切片，8:10:29 正常暂停于 `PENDING_SUBTITLE_REVIEW`。五条视频时长为 81/69/60/91/84 秒，FFprobe 音视频流、时长对照与完整 FFmpeg 解码均通过。网页自动刷新、字幕审核入口及 5 条切片统计正常；未创建发布任务、未投稿。用户可打开 [新任务字幕审核](http://127.0.0.1:8001/subtitles/1ccad93c1e77) 检查内容。备份目录中的 `acceptance.json`、`progress.jsonl` 和 `expansion-results.json` 保存完整证据。

更新日期：2026-09-11。适用版本：**2.3.0**。本页汇总当前交付状态；操作待办见 [NEXT_STEPS.md](NEXT_STEPS.md)，历史过程见 [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md)。旧记录里的“待合并、待部署”只代表记录当时的状态。

## 当前修复：手动只读复盘

PR [#88](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/88) 已 squash 合并为 `657aabe`，并于 9 月 11 日部署到原 RunDock Web 8001 / Worker 8765，运行目录仍为 `Ai-Clip-Workflow-offline-runtime`。产品版本继续为 2.3.0，此修复没有新建 Release 或移动旧 Tag。

- 本地完整回归 **987 passed**；Ruff、JS 语法和 diff 检查通过；[CI](https://github.com/damingishere-coder/Ai-Clip-Workflow/actions/runs/34587021299) 的 Linux、Windows、Docker 三项通过。
- 正式深度检查 ready，数据库 integrity=ok、外键异常 0。任务、Workflow Job、发布任务、提示词方案与版本、规则头、任务快照、历史应用记录共 8 张表逐项哈希不变；配置及 507 个音视频文件清单不变。
- 手动调用一次真实 Codex CLI / gpt-6-astra：报告 `weekly-ace9b439619d43968ee6` 于 18:05:58 开始、18:07:37 完成，状态 ready、三条建议、无规则补丁。正式页面可查看只读报告并反馈复制成功；Chrome 隔离测试验证复制内容及失败回退，正式 390px 页面无横向溢出和脚本错误。
- **同步实机限制**：本次只尝试一次官方同步，下载阶段返回 `DOWNLOAD_FAILED` / `Download.path: Target page, context or browser has been closed`；未导入新数据、未自动触发复盘、未重排，未自动重试。真实手动复盘使用此前 16:58 保存的官方数据。成功导入不联动由隔离 API 与后台轮询测试证明，不能将本次官方下载描述为成功。
- 回滚资料：本机 `data/backups/manual-review-20260911-180132/`，含数据库、配置、前后哈希与 `acceptance.json`。代码回滚可在停服后回到原 `28e0ae4`；无结构迁移，不覆盖运行中的数据库。没有补跑历史视频分析或触发投稿。

历史已应用规则保持现状；后续具体建议交给 Codex 单独核对、修改并验证。

## 已完成的交付

| 项目 | 已核实结果 |
| --- | --- |
| 稳定主干 | `master`；2.3 整合 PR [#86](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/86) 已 squash 合并 |
| 正式版本 | [v2.3.0 Release](https://github.com/damingishere-coder/Ai-Clip-Workflow/releases/tag/v2.3.0) 已发布；发布提交 `28e0ae4b742bca6c0a7a983eac0d85970e73853b` |
| 本机服务 | Windows 原生运行，沿用 RunDock 的 Web 8001 与 Worker 8765；原发布基线为上述提交；当前修复运行在 `657aabe`，见本页上方 |
| 实际核验 | 9 月 10 日 16:58（北京时间），Web / Worker 均返回 2.3.0；深度就绪 `ready`，数据库完整性正常、外键异常 0、9 条迁移正常，Scheduler 与 Worker 正常 |
| GitHub 首页 | 中英文 README、品牌横幅、截图、流程图、功能边界和文档导航已更新 |
| 分支整理 | 2.3 交付时本地 74 → 37，GitHub 69 → 41，开放 PR 26 → 18；这是整理完成时的快照，新任务会改变数量 |
| 数据保护 | 原有配置、数据库、媒体及四份本地审计修改已保留；Git 历史、配置和数据库已有本机备份 |

日常打开 [本地工作台](http://127.0.0.1:8001)。维护者本机的工作副本为 `Ai-Clip-Workflow`，运行副本为 `Ai-Clip-Workflow-offline-runtime`；其他电脑应以自己的服务管理器记录为准。文档后续提交可领先发布 Tag，单纯文档更新无需重启服务，也不移动已有 Tag。

## 已进入 2.3 的功能

- 素材导入、音频提取、本地 faster-whisper / 远程转写、逐句时间戳原文。
- 受控 Codex CLI 及兼容 Provider 的 AI 选片、分阶段进度、成功单元复用、输出校验与失败证据。
- 候选审核、按需切片、独立字幕工作台、内容准备、封面帧与排期预览。
- 内容复盘、手动周总结、官方数据反馈与独立排期预览；历史应用记录只读。
- SQLite Scheduler、Windows Chrome Worker、发布结果证据与不确定结果人工复核。
- 备份恢复、隔离 Demo、跨平台 CI、Windows 实机验收与发布门禁。

当前发送中心前台面向抖音；B站保留后端和历史兼容，前台与自动同步未启用。全自动流程不自动烧录字幕。功能已实现不等于每个账号、素材或真实投稿场景都已验收。

## 验证证据

- 2.3 发布前本地完整回归 **977 passed**，随后版本一致性定向验证 **3 passed**；两次结果分别记录，不相加为完整测试数量。
- [发布提交 CI](https://github.com/damingishere-coder/Ai-Clip-Workflow/actions/runs/34456014202) 的 Linux、Windows、Docker 三项检查通过；Ruff、Python / JS / PowerShell 语法和依赖检查通过。
- Windows 实机隔离 Demo 与 `release_gate.ps1` 通过，报告对应发布提交；Demo 包含 3 条任务、6 条候选和 6 条手动导出草稿。
- 正式数据库和 1207 个媒体文件通过当次验收保护检查；页面版本、主要导航与服务就绪已验证。
- 本轮版本整合与文档整理均未触发真实 AI 补跑或真实投稿。

本机完整交付记录保存在工作副本 `data/diagnostics/repository-consolidation-20260910.md`；实机报告保存在独立验收副本 `Ai-Clip-Workflow-repository-consolidation/acceptance-results/windows-20260910-164352/`。配置、数据库、报告和未提交修改备份只在本机保管，不上传仓库。

## 尚未完成或需单独核验

| 事项 | 当前边界与下一步 |
| --- | --- |
| 真实平台灰度矩阵 | [#25](https://github.com/damingishere-coder/Ai-Clip-Workflow/issues/25) 仍开放；按账号逐项验证登录、上传、提交证据及异常恢复 |
| 康熙实验 | [#84](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/84)、[#85](https://github.com/damingishere-coder/Ai-Clip-Workflow/pull/85) 未合并，不属于 2.3 正式能力 |
| 历史开放 PR | #72 / #76 的核心功能已被运行基线覆盖，但仍有独有配置或文档差异；#83 等历史文档也需按最终代码重新比对，不批量合并 |
| 依赖维护 | Dependabot PR 分别测试和合并，不能因为版本统一就视为已升级 |
| 演示与安装 | 已有真实截图；60 秒演示、可选转写依赖和 Windows 便携包仍需后续工作 |
| 单条素材进度 | 以任务页面和当前数据库为准；历史日志里的处理百分比、候选数量和排期不是实时状态 |

Issue #23 仍保留早期 v2.0 实机验收标题；本次 v2.3 实机验收已经完成。Issue 的旧标题或开放状态不能替代最新发布证据。
