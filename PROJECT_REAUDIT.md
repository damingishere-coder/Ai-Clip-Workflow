# NiuMa Studio 第二次工程复检整改收口报告

> 复检日期：2026-08-30
> 上次稳定基线：`PROJECT_AUDIT.md` 的 `78/100 · 稳定 V1`
> 整改前复检：`e2ca3907f1d9f2c8c19d744f3061693195dbbd76`，`72/100`
> 整改后代码：`12ca670b521507d1df47750721933342c5b7a8d3`
> 方法：Codemap 全模块独立复扫 + Code Overhaul 整改后增量复评 + SonarQube 26.8 同项目键复扫 + 本地隔离回归 + GitHub 三环境 CI
> 边界：本报告只复检和更新审计产物；没有在本分支继续修改业务代码，没有迁移活动库，没有调用真实 AI、Chrome、抖音或 B站，也没有合并任何整改 PR。

## 1. 最终结论

当前项目健康度为 **80/100 · 稳定 V1**。相对整改前的第二次复检 `72/100` 提升 8 分，相对上次完整 P0/P1 收口的 `78/100` 提升 2 分。

- 上次 `PROJECT_AUDIT.md` 的 **13 个 P0/P1 核心问题当前为 13/13 保持关闭**。
- 整改前 `PROJECT_REAUDIT.md` 列出的 **A-F 六项必须修复问题为 6/6 关闭**。
- 本轮实现后曾由独立前端复审发现 1 个按钮状态回归；它已在最终 revision 修复并增加宽屏、窄屏浏览器回归。**当前没有未关闭的新 P0/P1 回归。**
- Codemap 从 `1 HIGH / 63 MED / 17 LOW` 变为 `0 HIGH / 58 MED / 16 LOW`；平均分从 `65.4` 提升到 `66.8`，D 模块从 3 个减为 1 个。
- SonarQube 没有新增 Bug 或 Vulnerability，但增加 4 个 Code Smell、50 分钟技术债；**Sonar Quality Gate ERROR**，原因是没有 `coverage.xml` 和 4 个 new-code 规则问题。
- 结论是：**可以停止本轮业务代码优化。** 剩余问题都应按真实触发条件另开小任务，不应为评分、格式、重复字面量或 God Service 大小继续扩大重构。

这里的“可以停止”只表示源码整改范围已经收口，不表示已经上线：PR #67、#68、#69 仍未合并，**活动库未迁移**，本轮新增迁移尚未应用。

## 2. 复检快照与证据

| 项目 | 整改前 | 整改后 | 结果 |
| --- | ---: | ---: | --- |
| Git revision | `e2ca390` | `12ca670` | 包含 A-F 修复及前端回归修正 |
| Codemap 模块 | 14 | 14 | 全部重新审计，`needs_audit=0` |
| Codemap 跟踪代码 | 61,805 行 / 139 文件 | 62,146 行 / 139 文件 | +341 行，主要为修复与测试 |
| Codemap 平均分 | 65.4 | 66.8 | +1.4 |
| Codemap 等级 | 1 B / 10 C / 3 D | 1 B / 12 C / 1 D | D 模块减少 2 个 |
| Codemap Findings | 1 HIGH / 63 MED / 17 LOW | 0 HIGH / 58 MED / 16 LOW | HIGH 清零，总数 -7 |
| 全量 pytest | 854 passed | 867 passed | +13，全部通过 |
| GitHub CI | 当前堆叠 PR 无 checks | PR #67/#68/#69 均三环境全绿 | CI 链路恢复 |

最终 Codemap 产物：

- 交互图：`.codemap/codemap.html`
- 文本摘要：`.codemap/codemap.md`
- 结构化状态：`.codemap/modules.json`

## 3. 上次问题解决了多少

### 3.1 `PROJECT_AUDIT.md` 的 P0/P1：13/13 保持关闭

| 类别 | 数量 | 当前结果 |
| --- | ---: | --- |
| P0 | 3/3 | 测试库隔离、活动库外键一致性、可恢复删除均未回退 |
| P1 | 10/10 | Secret/鉴权、路径、Job/Publish fencing、状态与切片、转写、AI 完整性、XSS、迁移账本等主契约均保持 |

活动库只读复核结果为 `quick_check=ok`、`foreign_key_check=0`，文件大小仍为 `10,276,864` 字节。数据库 mtime 在验证期间继续被现有服务更新，因此不能宣称活动库“完全未变化”；测试使用独立临时数据库，且未对活动库打开写连接。

### 3.2 整改前 A-F：6/6 已关闭

| 项目 | 当前状态 | 直接证据 |
| --- | --- | --- |
| A. 长直播成功结果被质量门禁误判 | 已解决 | canonical meta 明确写入 `quality_degraded=false`，完整结果可进入后续门禁 |
| B. 内容反馈迁移非原子、旧库 Prompt FK 不等价 | 已解决 | 逐语句事务执行；旧表受控重建并验证 FK、孤儿、索引、触发器、失败回滚和重试 |
| C. 显式反馈错误绑定当前 active run | 已解决 | 只采用候选 `source_analysis_run_id`；来源不可信时保持未归因，不猜测 active run |
| D. 官方作品 Prompt 指标时长口径不一致 | 已解决 | 导入、候选和输出片段使用统一的有效时长回退链 |
| E. 官方导出阻塞 FastAPI 事件循环 | 已解决 | 账号解析、Worker 调用、结果校验和 SQLite 提交整体移入线程池 |
| F. 堆叠 PR 无 CI | 已解决 | PR #67、#68、#69 的 Linux、Windows host smoke、Docker image smoke 全部通过 |

阶段验证分别为：阶段 1 全量 `861 passed`，阶段 2 全量 `865 passed`，阶段 3 和最终 revision 全量 `867 passed`；迁移专项 `28 passed`，异步边界专项 `39 passed`，最终宽屏/窄屏浏览器用例 `2 passed`。

## 4. 新回归风险检查

本轮没有遗留的新 P0/P1 回归，但发现并关闭过一条实现回归：

- 首版陈旧预览保护在切换账号后会让“重新预览”按钮持续禁用，旧确认请求完成时还可能重新启用无效的确认按钮。
- 最终提交 `12ca670` 根据当前文件、账号、批次和请求代际重新计算按钮状态，旧响应不能覆盖新上下文。
- 该问题由独立 Codemap 前端审计发现，并用 1440px 与 390px 两种浏览器视口直接回归。

当前仍存在两类发布前风险，但不是源码回归：

1. PR #67、#68、#69 尚未合并；整改代码还没有进入默认分支。
2. 活动库尚未应用 `20260830_01_ai_prompt_version_fk`；只读检查明确显示该迁移未落账。后续只能在备份、停止相关服务并按堆叠顺序合并后受控应用。

## 5. Codemap 最终结果

| 模块 | 整改前 | 当前 | 变化 |
| --- | ---: | ---: | ---: |
| AI Selection | 55/D | 70/C | +15 |
| Content Review & Feedback | 61/C | 70/C | +9 |
| API & Runtime | 58/D | 63/C | +5 |
| Media & Storage | 73/C | 73/C | 0 |
| Transcription | 63/C | 63/C | 0 |
| Task Review & Cut | 63/C | 63/C | 0 |
| Subtitle | 63/C | 63/C | 0 |
| Publish Center | 64/C | 64/C | 0 |
| Publish Scheduler | 74/C | 74/C | 0 |
| Publishers & Worker | 79/B | 79/B | 0 |
| SQLite Persistence | 68/C | 68/C | 0 |
| Pipeline & Job Queue | 65/C | 63/C | -2 |
| Frontend UI | 57/D | 54/D | -3 |
| Ops & Delivery | 73/C | 68/C | -5 |

后三项降分来自本轮独立复扫把原有的文件/lease 边界、启动失败残留 Worker、Demo 正式路径继承、UI 轮询和部分保存问题重新计入，并不代表最终提交重新引入了 P0/P1。Codemap 分数是审计判断，不是单纯按 diff 自动加分；应优先看 HIGH 已清零和修复契约是否有直接测试。

## 6. SonarQube 前后指标

本轮继续使用 SonarQube Community Build `26.8.0.126808`、Scanner `8.1.0.6389` 和相同项目键 `niuma-studio-local-audit`。基线为整改前 `e2ca390`，当前为 `12ca670`。

| 指标 | 整改前 | 当前 | 变化 |
| --- | ---: | ---: | ---: |
| ncloc | 52,787 | 53,109 | +322 |
| 导入测试 | 854 | 867 | +13，100% 成功 |
| Issues | 654 | 658 | +4 |
| Bugs | 32 | 32 | 0 |
| Vulnerabilities | 1 | 1 | 0 |
| Security Hotspots | 0 | 0 | 0 |
| Code Smells | 621 | 625 | +4 |
| Duplication | 0.3% | 0.3% | 0 |
| Coverage | 0.0% | 0.0% | 仍无 `coverage.xml` 输入 |
| Cognitive Complexity | 10,189 | 10,267 | +78 |
| Cyclomatic Complexity | 10,280 | 10,352 | +72 |
| Technical Debt | 5,383 分钟 | 5,433 分钟 | +50 分钟 |
| Maintainability / Reliability / Security | A / C / D | A / C / D | 不变 |
| Quality Gate | OK，`conditions=[]` | **ERROR** | 新代码 Coverage 与 4 个规则问题触发 |

严重度由 `4 Blocker / 221 Critical / 366 Major / 63 Minor` 变为 `4 / 225 / 366 / 63`。新增 4 条全部是 Code Smell，不是 Bug 或 Vulnerability：

- `database.py` 两条重复字面量规则；
- 数据库迁移修复函数 Cognitive Complexity `29 > 15`；
- 内容复盘预览处理函数 Cognitive Complexity `21 > 15`。

Quality Gate 失败需要如实保留，但不应误读：`new_coverage=0` 表示没有覆盖率报告输入，不等于 867 项测试没有覆盖代码。若以后把 Sonar Gate 设为合并硬门禁，应单独增加 `coverage.xml` 产出并校准 new-code 规则；不建议为了消除这 4 条 smell 再拆高风险迁移函数或制造纯清洁性 diff。

## 7. Code Overhaul 整改后结论

- 架构：FastAPI + SQLite + 文件产物 + Windows Worker 仍适合本机单用户产品，不需要微服务、消息队列、React/Vue 或全量 ORM 重写。
- 代码质量：复杂度仍集中在 `publish_service.py`、`content_review_service.py`、`database.py`、`PipelineEngine` 和大体量原生前端；只有下一次真实修改相邻业务时才应渐进拆分。
- 测试：A-F 都有直接业务不变量测试，不再只是接口返回 200 或元素存在；本地全量和三环境 CI 形成闭环。
- 性能：本轮最明确的 30 分钟事件循环阻塞已经关闭；其余同步重任务只有在实际并发响应变差时再处理。
- 依赖：未修改依赖文件，`pip check` 通过。可更新项主要是 Pydantic、uvicorn、Ruff 等小版本，没有证据要求本轮升级。
- 真实边界：本轮没有执行真实 Provider、FFmpeg 长任务、Chrome 登录/风控或平台投稿，因此这些仍需按产品使用过程人工验收，不能由 mock 测试代替。

## 8. 当前仍值得继续处理的问题

以下问题有真实影响，但都不应阻止本轮结束；只有满足触发条件时再单独立项。

| 问题 | 何时值得处理 | 当前优先级 |
| --- | --- | --- |
| 历史兼容迁移不全在 ledger 事务内，旧备份又强制要求新 ledger | 下一次需要升级很老的 SQLite 或恢复 ledger 之前的备份前 | P2 |
| 备份恢复缺少贯穿健康检查到数据库替换的持续应用/Worker 互斥 | 下一次真正执行活动库恢复前，必须先补门禁或受控停服 | P2，高影响低频 |
| 文件 checkpoint 与 SQLite lease 不是统一原子边界，Worker claim 异常可退出且 stderr 被丢弃 | 需要更强的无人值守恢复、出现旧 Worker 覆盖或难诊断退出时 | P2 |
| 发送中心仍有部分成功提示、5 秒刷新覆盖脏表单、B站 API Provider 可配置但未实现 | 启用 B站 API、提高批量编辑频率或真实遇到部分保存时 | P2 |
| Demo/Development 仍可能继承正式路径和调度器，启动失败后可能残留 Worker | 需要把 Demo 当真正隔离环境或频繁自动启停时 | P2 |
| AI 恢复仍依赖部分默认归一化，坏 JSON 可能静默回退 | 真实 Provider 输出漂移、恢复成本异常或 Prompt 归因异常时 | P2 |

不再建议单独处理：全项目格式化、为拆分而拆分 God Service、重复字面量、全面类型标注、切换前端框架，以及只为提高 Sonar 分数进行的机械修改。

## 9. 验证结果

| 验证 | 最终结果 |
| --- | --- |
| pytest 全量 | `867 passed`，9 个既有弃用警告 |
| 阶段 3 专项 | `39 passed` |
| 浏览器回归 | 1440px 与 390px，`2 passed` |
| Ruff | 通过 |
| Python compileall | 通过 |
| JavaScript `node --check` | 9/9 通过 |
| PowerShell Parser | 20/20，0 错误 |
| Compose | base、base+dev、base+demo 均通过 |
| `pip check` | 通过 |
| `git diff --check` | 通过，仅既有 LF→CRLF 提示 |
| PR #67 CI | Linux、Windows、Docker 全绿 |
| PR #68 CI | Linux、Windows、Docker 全绿 |
| PR #69 CI | Linux、Windows、Docker 全绿 |

## 10. 当前健康度评分

| 维度 | 整改前 | 当前 | 说明 |
| --- | ---: | ---: | --- |
| 架构合理性 | 7 | 7 | 本地单体仍匹配范围 |
| 业务逻辑 | 8 | 9 | 长直播、归因和时长口径恢复可信 |
| 代码质量 | 6 | 6 | HIGH 清零；复杂度和 Sonar smell 仍限制上限 |
| 数据设计 | 7 | 9 | 本轮迁移原子性与旧库 FK 等价性补齐；历史兼容迁移仍保留 P2 |
| 稳定性 | 7 | 8 | 异步边界和前端代际已修；Worker/文件边界仍有条件风险 |
| 测试 | 8 | 10 | 867 项、本轮业务不变量和三环境 CI 全绿 |
| 安全性 | 8 | 8 | Secret、鉴权、Origin 和人工风控边界未回退 |
| 性能与资源 | 6 | 7 | 已移除最明确的事件循环长阻塞 |
| 可观测性 | 7 | 7 | Worker stderr、坏 JSON 和启动降级仍不足 |
| 文档与可维护性 | 8 | 9 | Codemap、任务文档、日志和复检证据同步 |
| **总分** | **72/100** | **80/100** | **稳定 V1，可以停止本轮业务代码优化** |

## 11. 是否已经达到“可以停止本轮优化”

**是。** 停止条件已经满足：

1. A-F 六项正确性、迁移、归因、异步和 CI 问题全部关闭；
2. 新发现的前端按钮回归已在最终 revision 修复；
3. 最终全量测试、浏览器回归、静态检查和三环境 CI 全部通过；
4. 当前没有 Codemap HIGH，也没有新增 Sonar Bug/Vulnerability；
5. 剩余问题均有明确触发条件，继续修改的边际收益低于回归风险。

后续正确动作不是继续“清代码”，而是按堆叠顺序审阅和合并 PR，在受控停服、备份和只读预检后应用迁移，再进行不触发真实投稿的上线检查。任何自动合并、活动库迁移、真实 Provider 或平台操作都不属于本报告授权范围。

## 12. Git 与交付状态

- 真实默认分支：`master`
- CI 修复：PR #66 已经用户确认并 Squash 合并，`master=b2f949a`
- 正确性修复：PR #67，OPEN / CLEAN / CI 全绿
- 迁移修复：PR #68，OPEN / CLEAN / CI 全绿
- 异步与前端修复：PR #69，OPEN / CLEAN / CI 全绿
- 本报告分支：`codex/docs-post-remediation-reaudit`
- 合并状态：#67、#68、#69 均未合并；本轮没有执行自动合并
