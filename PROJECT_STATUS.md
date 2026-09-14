# 项目当前进度

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
