# Development Log

## 2026-08-22 PR #38 与发送中心新版整合验收

- 将已合并到 `origin/master` 的发送中心新版安全整合进 `feature/task-defaults-and-safe-startup`，人工解决 `DEVELOPMENT_LOG.md` 与 `NEXT_STEPS.md` 两处文档冲突；PR #38 的任务名称历史、Windows 原生运行脚本和 Docker 回退说明均完整保留。
- 原生启动改动先以 `69dcc8f 新增：支持 Windows 原生日常启动` 独立保存；合并后的 Ruff、Python 编译、JavaScript 语法、PowerShell 7 / 5.1 AST、69 个定向测试和完整 `436 passed` 全部通过。
- 通过 Alter 只重启 `Niuma-Studio` Web：8001 Web PID 从 `76964` 更新为 `140328`，8765 Worker PID 保持 `139904`，没有停止或清理抖音浏览器登录目录。重启前后 `/health=ok`，Scheduler 正常、`publishing_count=0`、`scheduled_count=4`、Worker 可用。
- 实机打开带 `platform=bilibili` 的发送中心仍固定显示抖音，B站切换入口为 0；首个原始任务点击“全选本任务”后准确选择本组 5 条、其他任务组 0 条，底部显示“已选 5 条”，排期抽屉显示 5 条并提供“预览排期”。部分选择、再次全选和最终取消均正常，未提交排期或真实投稿。
- 首次打开新版页面自动升级 5 条未排期抖音草稿，表单中的标题、简介和标签立即与最终发送字段一致；升级前生成 `data/backups/workflow-before-publish-migration-20260822-121307-772508-140328-70deb9bc.sqlite3`，没有改动 4 条既有排期和 B站历史。

## 2026-08-22 发送中心账号与抖音文案规则统一

- 修复发送中心账号徽章与发布预检口径不一致：唯一且正常的抖音账号会自动匹配并显示“内容完整”，多个账号仍需手动选择，登录失效明确显示“账号需登录”；手工保存和旧草稿升级会正式写入唯一匹配账号。
- 发送中心前台、任务同步和全自动流水线当前只创建并展示抖音任务，`platform=bilibili` 不再切换页面；既有 B站记录、API、Publisher、80 字标题能力和历史数据全部保留，不删除、不降级。
- 每个“原始处理任务”恢复“全选本任务”，支持组内全选、取消全选和部分选择状态；折叠任务组仍可选择本组全部可操作抖音视频，不跨任务组。
- 新增抖音统一文案规则：标题 AI 目标 18～26 字、硬上限 30 字；标签 4～6 个且每个严格 2～3 字；简介 15～35 字并移除“现场爆笑、引发热议、不容错过”等模板化结尾。页面显示实时字数/数量，保存、生成、排期预检和最终发布共用同一校验。
- AI 重写现在同步更新 `description/caption` 和 `tags/hashtags`；单条、批量和首次升级都会立即回填表单。AI 失败只记录失败状态并保留原文，避免失败回退覆盖用户内容。
- 新增幂等的未排期抖音草稿升级接口；首次打开发送中心自动调用，写入前强制创建本次专属 SQLite 安全备份，只处理有效切片的 `DRAFT / WAITING` 且无排期记录，并在 `provider_response` 记录规则版本与结果。升级期间刚被编辑、排期或失效的记录会通过乐观并发条件跳过；已排期、B站、历史、已移除和失效切片全部不改动。
- 新增账号一致性、抖音文案边界、失败原文保护、升级幂等、单平台同步及浏览器组内全选回归；发布专项测试 `75 passed`、完整测试 `420 passed`，Ruff、Python 编译、JavaScript 语法和差异检查均通过；未连接真实账号、未触发真实投稿。

## 2026-08-21 推送前启动与任务名历史一致性修正

- 任务名称历史现在只返回可见、非空的唯一名称，按该名称最后一次创建时间从新到旧排列，并限制最多 100 条；前后空白会被清理后再去重。
- Windows 正式启动脚本在检测到 Chrome 时默认启动发布 Worker；`-SkipWorker` 只启动工作台，Demo/Development 自动跳过 Worker，`-WithPublisher` 仅保留为兼容旧命令。
- 启动成功提示已区分主动跳过、开发模式、未检测到 Chrome 和 Worker 启动失败；停止脚本文档明确默认同时停止 Docker 与本项目 Worker。
- `README.md` 与 `docs/PORTABLE_SETUP.md` 已同步上述安全启动方法。主控相关测试 `36 passed`、完整测试 `424 passed`；Python 编译、JavaScript 语法、Docker Compose 配置和 Git 空白检查均通过，仅保留 9 个既有依赖弃用警告。`scripts/start.ps1` Windows PowerShell AST 错误数为 0，文件仍保留 UTF-8 BOM。

## 2026-08-16 新建任务页任务名称历史候选

- “新建任务”页的任务名称框改为读取应用任务列表中的可见任务名称，并按创建时间从新到旧展示候选；最新可见任务排第一，较旧任务依次向下，已隐藏/永久删除的任务不会出现。
- 点击候选可完整填入任务名称，仍允许手动输入任意新标题；输入框继续保持 `required` 校验，创建任务请求字段与表单提交流程均未改变。
- 新增只读函数 `list_task_name_history()`：仅从 `tasks` 表查询 `task_name`，沿用与正常任务列表一致的可见性条件 `COALESCE(is_deleted, 0) = 0` 并按 `created_at DESC` 排序，不探测文件也不统计切片；`/tasks/new` 路由直接调用并注入模板。
- 页面使用浏览器原生 `<datalist>` 紧邻输入框渲染，配合 `list` 关联和 `autocomplete="off"`，选项顺序保留服务返回顺序，Jinja 自动转义保持开启；未新增 JavaScript、自定义 CSS、HTTP API 或数据库结构变化。
- 新增隔离测试 `tests/test_task_name_history.py`：三个不同创建时间任务按新到旧返回、`is_deleted=1` 不返回、新建任务页包含 `list`/`autocomplete="off"` 且两条中文长标题按最新在前渲染；每个用例使用自动销毁的临时 SQLite，不连接正式数据库。专项及原有相关测试 `6 passed`，Python 编译与 Git 空白检查通过。

## 2026-08-13 GitHub 主页任务详情与发送中心图片更新

- 中文、英文 GitHub 主页的“任务详情 / Task detail”和“发送中心 / Publishing center”预览已分别改为用户提供的对应实机 PNG 图片。
- 两张图片均以原始 `2560 × 1271` 分辨率直接复制到 `docs/images/task-detail.png` 和 `docs/images/publish-center.png`，未缩放、未压缩、未转码，保留原始清晰度。
- 仅调整项目主页图片资源与引用，不修改应用页面、业务逻辑、数据库或发布流程。

## 2026-08-03 DeepSeek 接口统一切换 Flash 模型

- 本机 `.env` 中的文字稿分析、发送中心发布文案及旧版兼容模型变量统一切换为 `deepseek-v4-flash`；DeepSeek 接口地址、协议和现有 API Key 均保持不变。
- `.env.example` 的文字稿分析与发布文案默认模型同步改为 `deepseek-v4-flash`，避免新环境仍使用旧的 `deepseek-chat` 示例值。
- 项目代码中的远程 AI 默认值原本已经是 `deepseek-v4-flash`，本次未改动本地 Ollama 模型、火山引擎转写接口或其他发布配置。
- 文字稿分析与发布文案接口均已完成最小真实 JSON 连通测试；Docker 应用重建后为 `healthy`，容器内 5 个远程模型变量全部为 `deepseek-v4-flash`，首页返回 HTTP 200。
- Python 编译、AI 配置保存和 DeepSeek 请求格式专项测试通过；完整测试为 `412 passed, 1 failed`，失败项是未改动的发送中心 Playwright 历史记录等待超时，单独重跑仍失败，因此按项目门禁暂不提交、推送或创建 PR。

## 2026-08-03 Windows 实机验收安全与 PowerShell 5.1 兼容修复

- 修复 `pre_upgrade.ps1` 把命名参数当成位置参数的问题；升级前备份现在稳定进入被 Git 忽略的 `backups/`，不会在项目根目录生成 `-Label/`。
- `setup.ps1` 对已有 `.env` 改为字节级保留；仅首次创建或显式 `-Force` 时生成 Token 和路径字段。旧配置未声明存储位置时继续使用 `E:\直播间切片工作流存储`，`doctor.ps1`、正式数据指纹与 Compose 挂载保持一致。
- 修复 Windows PowerShell 5.1 对 Docker Compose 正常 stderr 进度的误判，并使用 PowerShell 哈希表传递脚本开关；启动、停止、失败清理和 Demo 计数均以真实退出码为准。
- 验收新增 8001 端口隔离、Docker Desktop 版本回退读取、容器 `healthy` 最多 90 秒等待，以及通过标准输入执行 3 / 6 / 6 只读 SQL 断言；失败报告的泛型列表也能在 PowerShell 5.1 正常序列化。
- Docker 构建保留官方 Python 与 Debian 源为默认值，同时支持进程级 `PYTHON_BASE_IMAGE`、`DEBIAN_MIRROR`、`DEBIAN_SECURITY_MIRROR` 参数；网络受限环境可以切换等价镜像而不改 `.env` 或跳过完整构建。
- Windows 11 + Docker Desktop 4.40.0 实机验收已通过：5 个页面 HTTP 200、容器 `healthy`、Demo 数据为 3 / 6 / 6，Demo 正常停止，正式 `.env`、SQLite 逻辑内容和 E 盘 217 个文件保持不变。验收报告只保存在本机 `acceptance-results/`。

## 2026-08-03 v2.0.0 主分支收拢与文档定版

- 将当前工作分支相对 `master` 的素材存储、自动流水线、审核切片、发送中心、排期和真实发布成果统一作为 v2.0 本地生产闭环，不改变 FastAPI + SQLite + Windows Worker 的现有架构。
- 项目版本、FastAPI API、Windows 发布 Worker 和界面侧栏统一更新为 `2.0.0` / `v2.0`；全自动流程结束提示同步改为当前发送中心与排期语义。
- `README.md` 增加项目现状表、完整主流程、功能清单、运行形态和人工边界；明确“发布架构已实现”不等于“所有账号和平台页面已完成无人值守验证”。
- 新手指南、架构、状态流、部署和路线图同步说明 E 盘存储、两类 AI 选片、任务实时进度、片段审核同步、排期月历、Windows Chrome Worker、发布终态与后续真实灰度重点。
- 保留所有历史版本开发记录和既有验收说明；v2.0 后续仍优先做抖音 / B站单条真实灰度、长时间运行和数据备份验收，不扩大到多用户、云端 SaaS 或绕过平台验证。
- 发布前验证已完成：Python 编译、Ruff、`app.js` / `publish-center.js` 语法和 Git 空白检查全部通过，完整自动化测试 `407 passed`；仅保留 9 条既有依赖弃用提醒，测试未连接真实平台或触发投稿。

## 2026-08-03 发送中心排期日历展开与时间升序

- “排期计划”月历中的有任务日期现在可点击或使用键盘 Enter / 空格打开，在月历下方完整展示当天当前平台的全部排期，并明确显示日期、平台和任务数量。
- 当天详情按北京时间从早到晚展示时间、标题、账号与状态；点击任意一条只会滚动并高亮下方原任务，不复制操作按钮、不修改排期，也不触发投稿。
- 月历前两条预览与下方任务清单统一按 `scheduled_at` 升序；已排期任务在前，未排期任务在后且保持原有内部顺序。应用、调整、清除或取消排期后会原地重排，无需刷新页面。
- 切换平台或月份会关闭旧日期详情；选中日期已无排期时也会自动关闭。抖音和 B站继续严格隔离，日期仍按 `Asia/Shanghai` 北京时间计算。
- 未新增 API、数据库字段或迁移，未修改调度和真实发布边界；新增浏览器回归覆盖完整日期展开、稳定排序、键盘访问、定位高亮、动态取消排期和窄屏无横向溢出。
- 本机真实页面已只读验收：8 月 3 日完整显示 `08:00 → 11:00 → 14:00 → 17:00 → 20:00 → 23:00`，主清单继续衔接到 8 月 4 日；控制台无错误，未修改排期或触发投稿。专项测试 `37 passed`、完整测试 `407 passed`，Ruff、JavaScript 语法和 Git 空白检查均通过，仅保留 9 条现有依赖弃用警告。

## 2026-08-02 发送中心续接最晚排期

- “设置排期”抽屉新增“接在当前平台最晚排期后”：每次点击都实时读取 SQLite，不依赖页面中最多 200 条的缓存列表；抖音和 B站分别计算。
- 新增 `POST /api/publish/schedules/next-start`。接口只查当前平台仍有效且带未来时间的 `WAITING / SCHEDULED / PUBLISHING` 记录，并排除本次正在调整的已选任务；已发布、失败、取消和另一平台记录不会占用本平台时间线。
- 续排严格使用当前间隔，并复用跨午夜时段规则：19:00 加 3 小时为 22:00，21:00 加 3 小时为次日 00:00，22:00 加 3 小时超出窗口后顺延到次日 07:00。
- 发送中心和全自动任务的新默认每日窗口统一为 `07:00 → 00:00`，仍可逐次修改；已有任务配置、已有排期和数据库结构均未改变。
- 修复全自动 `daily_window` 对跨午夜窗口的旧循环判断，改为复用统一的允许时段计算，不会在 `07:00 → 00:00` 下反复滚动日期。
- 新增服务层、API、默认值、平台隔离、选中任务排除、无排期、跨午夜、自动流程及浏览器回填测试；相关测试共 `49 passed`，完整测试 `397 passed`，Ruff、Python/JavaScript 语法和 Git 空白检查均通过，测试未触发真实投稿。

## 2026-08-02 片段审核同步发送中心修复

- 根因确认：片段审核页勾选“启用”后，旧“同步发送中心”只读取已经完成的激活切片；当候选片段已启用但尚未出现在当前切片批次中时，请求会返回“新增 0 条”，发送中心仍只能看到旧版本。
- “生成切片”现在会先保存页面里的启用状态、标题、摘要和出入点，再开始切割，避免页面选择尚未落库时继续使用旧审核结果。
- 片段审核页新增“保存并同步发送中心”完整流程：先保存审核选择，再核对当前启用候选与激活成片；选择或内容有变化、文件缺失、成片数量不一致时自动生成安全新版本并同步，完全一致时只做幂等同步，不重复切片。
- 同步成功后自动进入 `/publish?task_id=...&tab=content`，展开并定位当前原始处理任务；旧版待准备/已排期记录仍按现有版本规则安全转入历史，不删除视频、文案或执行证据。
- 真实任务 `6753ce9f3bfd` 已完成页面回归：3 条候选均启用、修复前只有 2 条成片和 2 条发送内容；修复后新增 3 条最新发送内容、2 条旧版安全转入历史、失败 0 条，目标“大田怕鸟怕到连杂志图都怕，小S补刀跳海”已出现在抖音内容准备列表，未排期且未触发真实投稿。
- 新增审核同步服务和路由回归测试。完整自动化测试 `383 passed`，Ruff 与 JavaScript 语法检查通过；仅保留 8 条现有依赖弃用警告。

## 2026-08-02 内容准备已排期标记优化

- 发送中心“内容准备”中的 `SCHEDULED` 卡片新增蓝色“已排期 + 北京时间”标签，并使用轻蓝渐变底色、蓝色边框、左侧强调线和轻阴影，让已经安排过的任务无需切换页签即可辨认。
- 已排期卡片底部操作由“加入发布计划”改为“调整排期”；未排期卡片继续保持原白色样式与原按钮文案。
- 标记直接复用发布任务现有状态和排期时间，不新增数据库字段，不修改历史数据；新增排期、清除排期或取消发送后，标签、卡片颜色和按钮文案会在当前页面原地同步。
- 新增模板首屏渲染与前端原地更新回归测试。完整测试 `383 passed`，Ruff、JavaScript 语法、Git 空白检查和浏览器控制台错误检查均通过；仅有 8 条现有依赖弃用提醒。
- 本机 `127.0.0.1:8001/publish` 已按约 1500×1100 桌面视口验收：“姜母鸭汤酒味重到小S喝懵了”显示“已排期 2026-08-02 13:00”，普通未排期卡片保持原样；验收未修改排期、账号或平台数据，也未触发真实投稿。

## 2026-08-02 发送中心封面计数平台隔离修复

- 根因确认：发送中心当前停留在抖音时，封面按钮仍统计了页面中隐藏的 B站任务；现场数据库中的 8 条空封面路径全部属于 B站，抖音当前缺失数实际为 0。
- 封面按钮现在只统计当前平台、当前激活切片且仍处于内容准备范围的 `DRAFT / WAITING / SCHEDULED` 任务；当前平台没有缺失封面时按钮禁用且不再显示错误数字。
- 按钮文案改为明确的平台范围，例如“补充抖音缺失封面”或“补充B站缺失封面（8）”，切换平台后会立即重新计算。
- `POST /api/publish/covers/backfill` 新增可选 `platform` 参数；页面调用时始终传入当前平台，服务端同步校验并隔离查询、生成和更新范围，不会再从抖音页面误补 B站任务，也不会修改已经进入人工复核的记录。
- 新增服务层平台隔离、非法平台拒绝、API 参数透传和浏览器切换计数回归测试。完整测试 `378 passed`，Ruff、JavaScript 语法和 Git 空白检查均通过；真实页面验收为抖音按钮禁用且无数字、B站显示 8。本次未补封面、未修改发布任务数据、未触发真实投稿。

## 2026-08-01 康熙笑点优先选片 V2

- 新增可选的“综艺笑点优先”模式，现有通用模式保持不变；候选池默认最多 12 条，最终启用目标默认 5 条，质量不足时允许少选。
- 综艺模式改为三阶段：远程 AI 使用 5 分钟窗口与 60 秒重叠、本地模型使用 3 分钟窗口与 45 秒重叠进行笑点召回；随后读取笑点前后文扩展成 60–150 秒完整片段；最后把全部候选放进同一次全局评审并按话题、时间邻近和重叠范围去重。
- 新评分由程序统一计算文字质量与轻量音频反应信号。A级要求总分不低于 78、笑点闭环不低于 75、完整度不低于 70；B级只进入审核且默认关闭；C级不进入候选列表。音频只作加分，不会让文字闭环或完整度不达标的内容越过硬门槛。
- 音频评审从已有 WAV/音频中读取转写笑声词、笑点后音量突增、动态变化、停顿与短句密度；读取失败时自动退回文字评分，不中断整次 AI 分析，不做说话人识别或画面多模态判断。
- 全自动流水线只使用 `selected_by_default = 1` 的 A 级候选，最多取 `final_clip_target` 条，不再按 AI 自报置信度强行补齐。
- 片段审核页新增 A/B 等级、总分、笑点/完整度/音频分、入选证据、未自动启用原因，以及“值得发、不好笑、太碎、铺垫不足、重复、拖沓”反馈按钮；近期反馈会作为以后同模式全局复评的口味参考。
- 数据库仅新增任务选片字段、候选质量字段和 `clip_feedback` 表；内置新增 4 号“康熙笑点优先 V2”Prompt，只有目标槽位为空时才写入，不覆盖已有自定义 Prompt 或历史任务。
- 两集只读实测：E1810 从旧版 12 条全启用收敛为 12 条候选、6 条 A 级、5 条默认启用；较弱的 E1811 收敛为 4 条候选、3 条 A 级、3 条默认启用。边界加固后对同批范围复验，两集共 16 条均为 60–150 秒；详细记录见 `docs/KANGXI_V2_COMPARISON.md`。
- 验证结果：专项测试 `16 passed`，全量自动化测试 `376 passed`；Ruff、Python 编译、JavaScript 语法和 Git 空白检查均通过。只有 8 条现有依赖弃用警告，测试未调用真实发布。

## 2026-07-29 任务与发送中心切片关联修复

- `output_clip.is_active = 1` 现在是发送中心内容准备与排期计划的唯一切片来源；任务级关联状态会一次查询统计当前切片、双平台关联、缺失、主动移出和旧版待处理数量。
- 新增 `POST /api/publish/tasks/{task_id}/sync`。通用任务默认同步抖音和 B站，重复调用保持幂等；任务级显式同步可以恢复用户主动移出的当前版本内容。
- 手动生成切片成功后会自动同步到 `WAITING` 内容准备，不设置排期、不触发投稿；同步失败只作为警告返回，不回滚已经成功生成的切片。全自动流水线继续由原有元数据与排期阶段创建发布任务，避免提前生成重复内容。
- 重新切片时，旧版 `DRAFT / WAITING / SCHEDULED` 记录安全改为 `CANCELLED`，错误码为 `superseded_by_recut`，清空排期并写入事件；`PUBLISHING / NEED_REVIEW / PUBLISHED / EXPORTED / FAILED` 保持原样。
- 新版发布内容会尽量继承同一候选片段、同一平台的标题、简介、标签、账号和发布方式，但排期始终清空，封面从新视频重新生成。
- 字幕工作台显式同步会优先使用已完成的带字幕成片；只更新未排期的 `DRAFT / WAITING` 内容。已经排期的内容不会静默更换视频，会提示先取消排期。
- 任务列表、任务详情、片段审核和字幕工作台新增发送中心关联状态、任务级同步入口与深链；`/publish` 支持 `task_id / platform / tab`，可自动切换页签、展开并定位任务组。
- 真实任务 `b84227be2b01` 已完成前后对账：12 个激活切片新增抖音 12 条和 B站 12 条 `WAITING` 内容，排期均为空；旧版 11 条 B站 `WAITING` 已安全取消并写入事件，旧的已发布、失败和待复核记录均保留。
- 新增任务关联回归测试；关联专项 10 项、关联/流水线/版本回滚/API 专项 52 项均通过。完整测试 `358 passed, 2 skipped`；Python/JavaScript 语法检查和本机浏览器页面验收通过。

## 2026-07-29 排期中断修复与安全恢复

- 根因确认：Docker 中的 FastAPI 与 Windows Worker 曾同时读写挂载的 `data/workflow.sqlite3`；7 月 28 日 09:00 投稿已被平台确认成功，但 Worker 回写终态时出现 `unable to open database file`，异常处理再次写库后使调度后台退出。
- Windows Worker 现只负责宿主 Chrome、账号检测和独立执行日志，不再导入或调用 `PublishRepository`，也不再直接更新账号或执行阶段；账号状态、平台结果、任务终态和事件统一由 Docker 内的 FastAPI 落库。
- `PUBLISHED / EXPORTED / FAILED / NEED_REVIEW` 的平台结果、任务状态和事件改为同一 SQLite 事务提交；任务状态已变化时整笔回滚，避免结果表与任务表出现半成功。
- 调度常驻循环现在隔离单条任务异常，并捕获 SQLite 临时异常；失败后保留循环、记录安全化错误信息并按 5 秒间隔重试，下一轮成功会清零连续失败计数。
- 启动恢复会主动读取所有 `PUBLISHING` 任务对应的 Worker 执行日志：`confirmed_success` 只补记成功，明确失败或需复核写入对应终态，仍在执行继续等待，无法确认时进入人工复核，禁止自动重复投稿。
- 调度健康接口和发送中心新增 `last_error_code / last_error_message / last_error_at / consecutive_failures` 展示；异常时显示“异常重试中”。历史记录请求改为单飞并合并刷新，避免 Worker 离线时的慢请求被 5 秒轮询持续作废。
- 现场恢复前已停用 Docker、Watcher 和 Windows Worker，并使用 SQLite Backup API 创建 `data/backups/workflow-before-scheduler-recovery-20260729-201655.sqlite3`；源库和备份的完整性检查均为 `ok`，未删除数据库、历史、视频、封面或文案。
- 新增 Worker 数据库隔离、FastAPI 账号落库、执行日志、中断成功幂等恢复、SQLite 自动重试、单条异常隔离、终态原子性和 18 条两小时间隔顺延测试。
- 现场数据已恢复：任务 `850892d8d68f` 根据执行日志补记为 `PUBLISHED / confirmed_success`，保留平台原始发布时间且没有重新投稿；18 条 `SCHEDULED` 任务按原顺序移动到北京时间 2026-07-30 09:00 至 2026-08-01 15:00，过期排期为 0，每条均保存原时间、新时间和恢复原因事件。
- 最终验收通过：Ruff、Python 编译、JavaScript 语法检查和完整 `pytest` 均通过，结果为 `355 passed`；恢复后数据库完整性为 `ok`，健康接口连续两轮刷新，调度器 `running=true`、连续失败 `0`、Worker 正常、18 条排期且 0 条发送中。

## 2026-07-28 SQLite 迁移备份瘦身与防复发

- 定位到 `data/backups` 在约 17 天内生成 95,291 份 SQLite 迁移备份，占用 121.615 GiB；根因是发布迁移检查被高频调用，旧判断还会把无需合并的失败/人工复核记录当成重复任务。
- 迁移备份现在只针对真正活跃的 `DRAFT / WAITING / SCHEDULED / PUBLISHING` 重复任务；备份和数据修复使用 SQLite `BEGIN IMMEDIATE` 串行化，避免多个进程同时执行迁移。
- 新备份通过独立只读连接写入唯一临时文件，`PRAGMA quick_check` 通过后才原子改名；24 小时内已有有效快照时不重复生成。
- 自动保留最近 14 个备份日、每天一份有效快照；新增 `scripts/cleanup_database_backups.py`，默认仅预演，明确添加 `--apply` 才会删除匹配的迁移备份。
- 清理工具会先检查主数据库和每天拟保留的备份；若主库损坏、某个备份日没有有效副本或预演后目录发生变化，会中止删除。
- 已删除 95,279 份重复或损坏的 SQLite 备份和 1 个残留 journal，实际释放 121.601 GiB；保留 12 份有效备份共 14.88 MiB，主库和保留备份完整性检查均为 `ok`。
- 新增备份回退、14 日保留、24 小时冷却、并发迁移和失败回滚测试；专项测试 19 项、完整测试 348 项全部通过。

## 2026-07-28 执行日历、失败立即发送与记录安全清理

- “执行记录”新增独立北京时间月历，按 `scheduled_at → started_at → finished_at → created_at` 归档；点击日期后，下方分页列表只展示当天任务，抖音与 B站继续严格隔离。
- 失败记录的“手动重试”改为“立即发送”：发送前检查内容、文件、账号登录和 Windows Worker；原失败记录保留，新任务通过统一调度器立即执行，`NEED_REVIEW` 仍禁止直接重发。
- 执行记录支持单条和批量“删除记录”，仅允许已发布、失败、已导出或已取消终态；删除只设置 `history_hidden/history_hidden_at` 并写入事件，不删除视频、执行明细、平台作品或重试关系。
- 新增“已删除记录”分页视图和批量恢复；普通成功、失败记录不再错误显示“重新加入内容准备”，该操作仅属于 `user_removed_from_preparation` 记录。
- 新增执行月历、分页查询、安全删除与恢复 API，并补充数据库、服务、API 和真实浏览器回归测试；未触发抖音或 B站真实投稿。
- 最终验收通过：执行月历固定 42 格，桌面端和 720px 窄屏均无横向溢出；Ruff、Python 编译、两份 JavaScript 语法检查和全量自动化测试全部通过，结果为 `342 passed`（仅有 8 条现有依赖弃用警告）。
- 修复失败任务重发后偶发 `Internal Server Error`：迁移备份原先把“原失败记录 + 新需复核重试记录”误判为重复活动任务，导致调度器每 5 秒反复备份 SQLite；现在备份检测与重复任务清理统一只统计 `DRAFT / WAITING / SCHEDULED / PUBLISHING`。
- 本机数据库使用报错前 1 秒的完整备份安全恢复，恢复前损坏原件保存在 `data/recovery/malformed-20260728-010322/`；恢复后的 `PRAGMA integrity_check` 为 `ok`，页面、发布任务接口和调度器均恢复正常。
- 用户首次点击已经创建的重试任务保留为 `NEED_REVIEW`，原因是内容风险标记“引战夸张”；未再次创建任务，也未触发真实平台投稿。
- 修复后 Ruff、Python 编译、JavaScript 语法检查和全量测试均通过，结果为 `343 passed`；浏览器实页确认执行日历、需复核操作和响应式布局正常，控制台无错误。

## 2026-07-23 Docker 项目与 Windows 发布 Worker 自动联动

- 新增 `NiuMa Studio Docker Watcher`：后台严格核对容器名、Compose 项目、`workflow` 服务和项目工作目录，只有当前牛马片场容器运行时才启动 Windows Worker。
- 容器停止或 Docker 不可用连续 15 秒后，只关闭命令行路径属于当前项目的 Worker；端口被其他项目或程序占用时保持原有保护，不会误关。
- Worker 保留本机公开 `/health`，新增带 Bearer Token 的 `/v1/health`；Docker 调度器使用鉴权健康接口，首次 Token 不同步时只重建 `workflow` 容器，不删除 SQLite、任务文件或 Chrome 登录资料。
- 新增观察器安装/卸载脚本。新任务验证成功后才移除当前指向已删除脚本的旧 `NiuMa Studio OpenCLI Host Bridge` 任务。
- 发送中心移除手动运行 PowerShell 的连接修复提示，改为等待 Docker 自动联动、重新检测或在 Docker Desktop 中重新运行项目。
- 本机验收通过：停止 `workflow` 后 Worker 自动关闭，重新运行容器后 Worker 与发送中心自动恢复；强制结束当前项目 Worker 后约 6 秒自动拉起新进程。PowerShell 5.1、Ruff、Python 编译、JavaScript、Docker Compose 和全量测试均通过，测试结果 `313 passed`，未执行真实投稿。

## 2026-07-22 发送中心按原始任务归类与安全移出

- “内容准备”从平铺发布卡片调整为按原始处理任务分组：组头展示任务名称、原视频文件名、任务创建时间、当前平台待准备数量和任务详情入口。
- 任务组按创建时间从新到旧排列，当前平台最新任务默认展开，其余折叠；切换抖音 / B站后重新统计数量并隐藏当前平台没有内容的分组。
- 每条内容新增“移出内容准备”：只把当前发布任务软取消为 `CANCELLED + user_removed_from_preparation`，同时清除排期并写入事件；不删除原视频、裁剪成片、字幕、封面或另一个平台的记录。
- “补充缺失任务”和全自动发布任务创建会识别用户主动移除标记，不会重新创建；执行记录提供“重新加入内容准备”，恢复前会检查同片段、同平台是否已有有效任务。
- 新增 `POST /api/publish/jobs/{job_id}/dismiss` 与 `POST /api/publish/jobs/{job_id}/restore`，直接复用现有字段与事件表，没有新增数据库表或破坏性迁移。
- 新增 5 项专项测试并更新浏览器级折叠断言；全量自动化测试 `308 passed`。Ruff、Python 编译和浏览器实页检查通过，页面控制台无错误，未对真实发布记录执行移出或投稿。

## 2026-07-19 发送中心旧批量发送入口与冗余代码清理

- 删除内容准备/排期共用底部批量栏中的旧“立即发送”；批量栏只保留当前平台账号设置、AI 文案补齐、设置排期和取消选择，避免继续进入已停用的批量直发方式。
- 保留排期任务右侧的单条“立即发送 / 转换并发送 / 立即导出”；现有 `POST /api/publish/jobs/{job_id}/publish-now → PublishScheduler → Registry → Windows Worker` 成功链路没有改动。
- 删除 `publish-center.js` 中旧批量逐条调用 `publish-now` 的监听器，并清理全局 `app.js` 内已没有模板引用的上一版发布配置、发送卡片、批量队列和投稿预览代码。
- 删除只服务旧页面的 `POST /api/publish/jobs/{job_id}/send`、`POST /api/publish/send/start`、`PublishSendStart` 及对应批量兼容服务；排期、失败重试、人工复核、账号登录和当前单条发送接口全部保留。
- 新增回归测试，固定检查旧按钮、旧前端监听器和旧路由已消失，同时当前设置排期、批量账号/AI 和单条 `publish-now` 入口仍存在。
- 已验证：发送中心相关测试 `32 passed`，全量测试 `303 passed`，Ruff 与两份 JavaScript 语法检查通过；Docker `8001/publish` 实页勾选检查通过，Worker 正常且页面控制台无错误，未触发真实投稿。

## 2026-07-19 抖音立即发送上传判断与失败窗口修复

- 根因确认：抖音视频仍处于“文件解析中，0%”时，旧逻辑把常驻的“作品描述 / 发布设置”误判为上传完成，随后触发 `douyin_preview_not_ready` 并关闭 Chrome。
- 新流程每秒读取上传进度、忙碌文案、失败文案和真实视频预览；只有真实预览连续两次稳定且没有未完成信号时才进入标题、正文、话题和封面步骤。
- 标题、正文/话题、第一张有效 AI 推荐封面和“公开 / 好友可见 / 仅自己可见”全部增加写后校验；发布按钮改为同步精确点击，仅平台成功提示或作品管理中的准确标题可以写入 `PUBLISHED`。
- 失败、验证或页面改版时保存诊断证据，Chrome 默认保留 10 分钟并显示暂停原因；实时阶段同步到发送中心，此类任务进入 `NEED_REVIEW`，不自动重复投稿。
- 失败重试接口支持覆盖新任务的可见范围，原失败记录保持不变；新增 `.\scripts\start_niuma_studio.ps1`，统一启动 Worker、Docker 和发送中心，健康 Worker 会直接复用，未知端口占用不会被结束。
- 增加真实 Chrome DOM 夹具，覆盖 0%、100% 无预览、真实预览、说明性“上传中”提示、上传失败和三种可见范围。
- 真实页面继续发现并修复两处边界：页面底部“点击发布后，如作品还在上传中”不能算上传状态；Windows Worker 的 `caption / hashtags` 必须映射为完整正文和 `#话题`，不能退化为只填标题。
- 修复历史任务保护：重复活动任务清理只处理 `DRAFT / WAITING / SCHEDULED / PUBLISHING`，不再把 `FAILED / NEED_REVIEW` 历史改成 `CANCELLED`；Worker 已结束但应用热重载时会立即按执行日志回收终态，不必等待过期时间。
- 一键启动脚本现在同时确认 Scheduler `running=true`；页面正常但调度循环退出时，只重启本项目 `workflow` 服务。Worker 启动脚本增加显式 `-Restart`，仍只会重启已确认属于本项目的进程。
- 2026-07-19 15:34 完成真实私密灰度：任务 `pub_92aeb980cd804fe3883e3539d33ad5fe` 依次通过上传稳定、标题、完整正文与 4 个话题、AI 推荐封面、可见范围和精确发布点击，抖音返回“发布成功”，本地状态为 `PUBLISHED / confirmed_success`。
- 同账号作品管理页已找到准确标题，作品卡片明确显示锁和“私密”；机器校验 `title_found=true`、`private_found=true`。作品按约定未自动删除。

## 2026-07-18 发送中心平台隔离、登录同步与真实发布流程修复

- 发送中心新增统一“抖音 / B站”平台上下文；内容准备、排期计划、执行记录、账号管理、补充任务和批量操作只处理当前平台，切换平台会立即清空选择。
- 发布任务的平台创建后不可修改；单条、批量目标更新和批量排期会拒绝跨平台或混合平台请求，并返回 HTTP 409 中文原因。补充任务接口增加明确 `platform` 参数。
- 账号登录启动后立即显示“等待登录完成”，页面每 5 秒读取只读账号接口自动同步数据库；登录正常时显示“打开创作者中心 / 重新登录”，账号忙碌不会再被误判为登录失效。
- 调度器扫描到旧 `SCHEDULED + opencli_publish` 时统一转为 `NEED_REVIEW`，错误码为 `legacy_schedule_requires_confirmation`，不上传、不静默跳过。逐条“转换并发送”会保留旧记录并创建新的同平台 `local_browser` 任务；重复点击不会重复创建替代任务。
- Windows Playwright Publisher 已复用旧兼容流程中经过真实页面修正的 DOM 脚本：抖音恢复简介与话题、平台推荐封面、精确发布按钮和成功/风控判断；B站恢复草稿提示、上传完成、推荐封面、创作声明、分区、简介评分、默认标签和成功证据判断。
- 没有平台成功提示或作品管理证据时绝不写入 `PUBLISHED`；验证码、风控和结果不确定继续进入人工复核，不绕过平台限制。
- 本机 7 条过期抖音排期已安全转为 `NEED_REVIEW + legacy_schedule_requires_confirmation`，每条只记录 1 个复核事件，未创建替代任务；B站当前没有 `SCHEDULED / PUBLISHING` 任务。
- 单条验收目标 `e2f91ef6e5eb`（“胡瓜老婆丁柔安搭捷运公车…”）已预设为“仅自己可见”，仍保持 `NEED_REVIEW`，没有创建替代任务或触发上传。
- 未执行抖音或 B站最终投稿。全量测试 `290 passed`；Ruff、Python 编译、Node 语法、PowerShell 语法、Docker Compose 配置和本机 Chrome 页面验收均通过。

## 2026-07-18 Windows 发布 Worker 重复启动端口修复

- 修复 `start_publish_worker.ps1` 重复运行时偶发 `WinError 10048`：停止旧 Worker 后会等待 Windows 完全释放监听端口，再启动新进程。
- Worker 改为只监听本机 `127.0.0.1`，避免 Chrome 临时把 `8765` 用作出站连接源端口时与 `0.0.0.0:8765` 冲突；Docker Desktop 仍可通过 `host.docker.internal:8765` 正常访问，同时减少局域网暴露。
- 同一 PID 即使同时存在多个监听记录也只停止一次；如果端口被无关程序占用，仍保持安全退出，不会误关其他程序。
- Worker 启动检查改为最多等待约 10 秒并同时检查新进程是否提前退出，避免固定等待 2 秒造成慢启动误判或旧进程健康检查误判。
- 已验证：PowerShell 5.1 语法检查通过；连续两次运行启动脚本均成功；Worker 在 `127.0.0.1:8765` 返回健康状态；Docker 发送中心健康接口返回 `worker_available: true`；Ruff、Python 编译和 Docker Compose 配置检查通过；全量测试 `279 passed`。

## 2026-07-16 “立即发送”就绪检查与旧任务安全恢复

- 新增统一 `send_readiness` 计算：立即发送、单条排期、批量排期预览/保存和调度器领取复用同一组账号、登录态、平台、内容文件和 Worker 校验。
- 旧 `opencli_publish` 任务不再被调度器领取；真正执行前会改用 `local_browser`。同平台恰好一个账号时自动选择，没有账号时引导创建，多个账号时要求手动选择。
- 条件不足时 `POST /api/publish/jobs/{id}/publish-now` 返回 HTTP 409 和结构化阻塞原因，不会先把任务写入 `SCHEDULED`；Worker 离线时既不入队，也不会收到 `/v1/publish`。
- 排期卡片现在显示“未登录 / 没有账号 / 需选择账号 / 内容不完整 / Worker 未连接 / 旧任务待转换”等具体原因；条件不足时主按钮改为“打开登录窗口”“新增账号”“选择账号”“完善内容”或“连接 Worker”。
- 新增安全恢复接口 `POST /api/publish/jobs/{id}/repair-and-publish`：仅允许明确在上传前因 `opencli_fallback_disabled` 进入 `NEED_REVIEW` 的任务；原记录保持不变，创建新的 `local_browser` 替代任务。上传阶段已开始、已有远端结果或结果不确定的记录仍禁止自动重试。
- 保持 OpenCLI 兼容开关关闭；`manual_export` 继续是显式本地导出，不要求账号或 Worker，也不会被标记为平台已发布。
- 真实页面验证：Worker 正常；旧任务 `e2f91ef6e5eb` 显示账号“康熙来了”尚未登录和“打开登录窗口”，不再显示“立即发送”；页面控制台无 JavaScript 错误。未点击登录、未执行真实投稿。
- 已验证：`ruff check` 通过；Python/JavaScript 语法检查通过；全量 `279 passed`，包含浏览器排期预览与导出测试，以及无账号、唯一/多个账号、未登录、平台不匹配、Worker 离线、旧任务安全恢复和不确定结果禁重试测试。

## 2026-07-16 发送中心平台月历与 Worker 连接修复

- 排期计划新增“抖音排期 / B站排期”双卡片切换，分别统计待排期和已排期任务；平台切换只影响当前清单与月历，不改任务数据。
- 新增北京时间月历，按周一到周日展示当月 42 格日期和已排期任务；支持上月、下月、回到本月及点击日历任务定位清单。
- 重做中窄屏排期任务卡片布局，避免账号、状态和操作按钮被挤成竖排；移动端继续使用单列卡片。
- 调度器卡片新增 Worker 自动刷新、重新检测和一键启动说明；账号登录连接失败时返回可直接照做的中文处理步骤。
- `start_publish_worker.ps1` 现在会在启动 Worker 后自动同步正在运行的 Docker Web 容器，使新生成的本地 Token 和 Worker 地址立即生效；不会删除 SQLite、任务视频或账号记录。
- 本机验证 Worker 已监听 `0.0.0.0:8765`，Docker 已取得 Worker 地址和 Token，发送中心健康接口返回正常；账号“登录 / 重新登录”已成功打开独立 Chrome，未执行平台登录或最终投稿。

## 2026-07-15 v1.5.0 统一真实发布重构

- 新增 `app/services/publishers/` 分层和 Registry：`local_browser`、`manual_export`、显式 `opencli_publish` 兼容模式与抖音/B站平台 Publisher 职责分离；未知平台直接失败，不回退导出包。
- 实现 `LocalBrowserPublisher`：任务校验、平台注册、账号登录预检、Windows Worker 调用、统一结果转换和脱敏写回。
- 新增 `scripts/publish_host_worker.py` 和 `start_publish_worker.ps1`：使用系统 Chrome 持久化上下文、每个平台/账号独立目录、同账号串行锁、路径白名单、Docker `/workspace/tasks` 到 Windows `TASKS_DIR` 的映射、Bearer Token 与执行阶段日志。
- 实现抖音和 B站 Playwright 投稿流程，包含视频/内容校验、登录态、上传、平台表单、封面、发布按钮和成功证据判断；不绕过二维码、短信、滑块、验证码或风控。
- 立即发送和未来排期统一为 `SCHEDULED → PublishScheduler → Registry → Publisher`；SQLite 使用 `BEGIN IMMEDIATE` 和条件更新原子领取。
- 新增上传前安全重试和上传后禁止自动重试规则；重启时读取 Worker 执行日志，未知旧 `PUBLISHING` 进入 `NEED_REVIEW`。
- 手动重试会创建带 `retry_of_job_id` 的新任务；人工标记已发布只允许 `NEED_REVIEW` 且必须填写对应平台作品链接。
- 数据库兼容新增 Worker、阶段、时区、复核、时间和重试字段；账号增加登录态字段；新增 `publish_job_events` 事件表。迁移不删除旧字段和历史数据。
- 发送中心重构为“内容准备 / 排期计划 / 执行记录”，新增账号抽屉、精确排期预览确认、状态筛选和人工复核操作，保留 Jinja2 + 原生 JavaScript。
- 默认配置改为 `APP_TIMEZONE=Asia/Shanghai`、`PUBLISH_DEFAULT_MODE=local_browser`、5 秒 Scheduler；浏览器 Profile、Worker 日志、storage state 和发布产物加入 Git 忽略。
- 新增 37 项专项测试；全量结果 `272 passed, 0 failed, 0 skipped`。同时通过 `python -m compileall -q app scripts`、`node --check app/static/js/publish-center.js`、`ruff check app scripts tests` 和 `docker compose config --quiet`。
- 未执行真实平台最终投稿：当前没有本轮用户明确授权的账号和测试素材；必须按 `NEXT_STEPS.md` 逐平台单条灰度并在最终点击前确认。

## 2026-07-11 真实发布排期闭环与发送中心重构
- 分离目标平台与执行方式：`platform` 仅允许 `douyin` / `bilibili`，`publish_mode` 独立表示 opencli、发布包、API 或未实现的本地浏览器执行器。
- 全自动流水线直接创建最终 `opencli_publish` 任务，不再依赖“刷新发送队列”生成第二套记录；刷新操作只补缺，并尊重已有 `manual_export` / `local_browser` 任务。
- 新增统一 `execute_publish_job(job_id, force=False)` 入口；调度器使用条件更新原子抢占，opencli/API 成功进入 `PUBLISHED`，发布包成功进入 `EXPORTED`，失败进入 `FAILED`。
- `NEED_REVIEW` 可以保存排期但不能执行；“立即发送”支持 `WAITING`、`SCHEDULED`、`FAILED`，会先把 UTC 排期改为当前时间。
- 排期请求改为 `start_at_local + timezone + interval_minutes`；新增 `POST /api/publish/schedules/preview`，预览和保存复用同一计算函数，跨日按用户时区的每日窗口顺延。
- 数据库新增 `schedule_timezone` 与有效任务唯一索引；迁移旧平台/执行方式或重复任务前自动创建 SQLite 备份，保留已发布历史，只取消较旧的未发布重复项并记录原因。
- 调度器只恢复超过 `PUBLISH_JOB_STALE_MINUTES` 且没有成功结果的陈旧 `PUBLISHING`；阻塞执行使用工作线程；新增 `GET /api/publish/scheduler/health`。
- 发送中心改为“待安排 / 已排期 / 发送记录”三页签，使用紧凑列表、单一选择语义、底部批量栏和右侧排期抽屉；排期、取消和编辑均局部更新，不再整页刷新。
- 新增统一 `apiFetch`，运行时自动携带本地管理员 token；真实 token 不写入静态 JavaScript。
- 已验证：指定 36 项发布测试通过；全量 235 项 pytest 通过；Node 检查通过；真实 Chrome 浏览器集成测试与隔离页面可视化检查通过。

## 2026-06-25 全自动切片修复与发送中心批量排期
- 修复 AI 分析完成后候选片段“先写入、随后又被全部删除”的事务顺序错误；候选片段现在在单个 SQLite 事务内替换，任一新片段写入失败都会自动回滚并保留旧结果。
- 修复历史全自动任务卡在 AI 分析后的问题：任务可从最近一次 AI 分析历史恢复候选片段，并从自动选片阶段继续，不需要重新消耗一次 AI 分析。
- 自动选片不再读取独立的自动数量和 15-300 秒范围；目标数量统一使用任务的“候选片段数量”，时长上限统一使用“单条切片最长”，无效时间戳不会被强行送入 FFmpeg。
- 新建任务页的全自动区精简为一个开关；创建成功后后台立即启动完整流水线，任务详情隐藏手动“开始处理”，处理中自动刷新，失败或历史中断时显示重试/继续按钮。
- 全自动流水线生成的发布任务默认进入 `WAITING`，不再自行安排发布时间；旧 `auto_config_json` 字段继续保留，兼容历史任务和旧接口。
- 发送中心新增批量发布计划：勾选未发布任务后可设置起始时间、间隔小时和每日允许时段，超过当日结束时间会顺延到次日开始；支持批量清除排期，并在任务卡片和发布记录中显示计划时间。
- 新增批量排期 API `PATCH /api/publish/jobs/schedule-batch`；已发布和已取消任务禁止改期，`NEED_REVIEW` 可保存时间但仍保持人工复核状态。
- 静态资源版本更新为 `20260625-auto-pipeline-schedule`，避免浏览器继续使用旧版 JavaScript / CSS。
- 已验证：完整 232 项 pytest 测试通过；Python 编译、JavaScript 语法检查和 Ruff 检查通过；浏览器检查 `/tasks/new`、历史全自动任务详情和 `/publish` 正常。

## 2026-06-23 v1.4.0 定时发送与自动发布执行器
- 新增 `PublishScheduler`，应用启动时可自动后台扫描 `publish_jobs`，也支持 `python -m app.publish_scheduler run` 持续运行和 `python -m app.publish_scheduler run-once` 手动扫描一次。
- `publish_jobs` 补齐 v1.4.0 字段：`clip_id`、`caption`、`hashtags`、`cover_text`、`video_path`、`risk_flags`、`publish_result`、`remote_video_id`、`attempt_count`、`published_at` 等；旧字段继续兼容。
- 发布状态统一为 `DRAFT`、`SCHEDULED`、`WAITING`、`PUBLISHING`、`PUBLISHED`、`FAILED`、`CANCELLED`、`NEED_REVIEW`；没有 `scheduled_at` 的旧手动队列会进入 `WAITING`，避免被调度器误执行。
- 新增 `BasePublisher`、`ManualExportPublisher`、`LocalBrowserPublisher`；本轮完整实现 `ManualExportPublisher`，到点后导出 `outputs/publish_packages/{task_id}/{clip_id}/` 发布包。
- v1.3.0 自动流水线创建的发布任务现在默认写入 `manual_export` + `SCHEDULED`，到点后可由 v1.4.0 调度器自动导出发布包；有 `risk_flags` 的任务保持 `NEED_REVIEW`，不会自动发布。
- 新增发布队列 API：队列快照、run-once、立即发布、取消、跳过、失败重试、复核通过、修改发布时间、修改标题文案后重新入队；发送中心同步识别新状态并展示发布记录。
- 新增 `.env.example` 配置项：`PUBLISH_SCHEDULER_ENABLED`、`PUBLISH_SCHEDULER_INTERVAL_SECONDS`、`PUBLISH_SCHEDULER_DEFAULT_PLATFORM`、`PUBLISH_SCHEDULER_MAX_RETRY_COUNT`、`PUBLISH_SCHEDULER_EXPORT_DIR`、`PUBLISH_SCHEDULER_ALLOW_PUBLISH_WITHOUT_REVIEW`。
- 当前默认不接真实平台 API，不写死账号密码、cookie、token；`LocalBrowserPublisher` 仅预留结构，后续可接 Playwright、Selenium、opencli 或平台 API。
- 已验证：`.venv\Scripts\python.exe -m pytest tests\test_publish_scheduler.py -q` 通过，10 passed；`.venv\Scripts\python.exe -m pytest tests\test_publish_job_lifecycle.py tests\test_auto_pipeline.py -q` 通过，30 passed。Pydantic 旧 `@validator` 警告为既有警告。

## 2026-06-23 v1.3.0 全自动任务流水线
- 新增 `PipelineEngine`，把新建任务后的准备视频、读取/生成转写文本、AI 分析、自动选片、原片切割、标题文案、发布计划和发布任务创建串成一键全自动流程；引擎只做调度，继续复用现有转写、AI 分析、切片和发布任务数据结构。
- `tasks` 表新增 `auto_mode`、`auto_config_json`、`last_error`；全自动模式新增 `CREATED`、`PREPARING_SOURCE`、`TRANSCRIBING`、`AI_ANALYZING`、`CLIP_SELECTING`、`VIDEO_CUTTING`、`METADATA_GENERATING`、`SCHEDULE_CREATING`、`PUBLISH_JOB_CREATING`、`READY_TO_PUBLISH`、`COMPLETED` 以及对应 `FAILED_*` 状态。
- 新建任务页新增“全自动模式”开关、自动切片数量、片段时长范围和发布计划配置；`auto_mode=false` 时保留原手动流程，`auto_mode=true` 时通过后台任务自动启动流水线。
- 全自动模式会优先读取 `transcripts/transcript.md` 或已有文本/字幕文件；没有文本时才调用现有转写流程。转写文本仅用于 AI 分析，本轮明确跳过加字幕、字幕样式渲染、字幕叠加和字幕烧录。
- 自动选片会校验时间戳和最小/最大时长，跳过非法片段并记录原因；自动切片继续输出到 `05_clips/`，单个切片失败不会阻断其他成功切片进入后续文案和发布任务创建。
- 新增 `MetadataGenerator` 和全自动发布任务创建服务，为每条成功切片生成结构化 `title`、`caption`、`hashtags`、`cover_text`、`platform`、`risk_flags`；有风险标记的发布任务进入 `NEED_REVIEW`，不影响其他切片。
- 自动生成 `auto_publish_metadata.json`、`auto_publish_schedule.json`、`05_clips/clip_metadata.json` 和 `analysis/task_summary.json`；发布任务写入现有 `publish_jobs`，本轮只创建任务，不执行真实定时发送。
- 新增 `tests/test_auto_pipeline.py` 覆盖：手动模式不触发、自动模式触发、已有转写跳过、无转写调用转写、切片跳过字幕、AI JSON 异常记录失败、单切片失败不阻断、默认排期、发布任务创建、Windows 路径引用。
- 已验证：`.venv\Scripts\python.exe -m pytest tests\test_auto_pipeline.py -q` 通过，结果为 10 passed、3 个 Pydantic 既有弃用警告。

## 2026-06-15 v1.3 分支整理与集成发布
- 新建并验证 `codex/branch-integration-20260611` 集成分支，按顺序整合 `fix/p0-security-and-stability`、`fix/p1-1-db-performance`、`fix/p1-2-query-refactor`、`feature/p1-3-job-queue`、`feature/p1-4-split-task-service`、`feature/p1-5-versioned-products-rollback`、`feature/p2-1-engineering` 和 `feature/p2-2-architecture-docs`。
- 已处理分支之间的冲突：数据库初始化同时保留 `oauth_states`、`workflow_jobs`、`cut_runs` 等结构；`task_service.py` 保留兼容出口，页面查询实际迁移到 `task_query_service.py`；任务队列、服务拆分、产物版本化和工程化配置已统一集成。
- 项目版本更新为 `1.3.0`，页面侧边栏状态更新为 `v1.3 分支整合版`。
- 集成验证结果：`.venv\Scripts\python.exe -m pytest -v` 通过，结果为 202 passed、2 warnings；`.venv\Scripts\ruff.exe check app tests` 通过。
- 本轮按用户确认后的要求准备将集成分支合并到 `master`，并清理多余功能分支；不执行 force push，不删除 `master`。

## 2026-06-09 v1.2 分支收拢与 MVP 全流程确认
- 已将本地功能分支内容收拢到 `master`：抖音发送修复、B站发送修复、AI 接口设置、发送中心 AI 文案配置、Apple 风格 UI、Docker opencli 桥接和字幕相关历史均已纳入主分支历史。
- `codex/Releasefunction` 是较早的大分支，直接合并会回退新版发送中心、品牌资源和 UI；本轮保留当前 `master` 最新代码，手动补入其核心改动：远程 / 本地 AI 长视频统一分段分析、失败小段跳过、旧字段兼容和短错误提示。
- 项目版本更新为 `1.2.0`，侧边栏口径更新为 `v1.2 MVP 全流程`，新增根目录 `VERSION` 文件。
- 根目录 `AGENTS.md` 已更新为“默认自动提交到 GitHub，并创建 PR”的协作规则，同时保留禁止自动合并 PR、删除分支、force push、提交敏感信息和删除历史数据的安全边界。
- 当前项目状态记录为：上传视频、提取音频、远程 / 本地转写、AI 候选片段分析、片段审核、自动切割、自动加字幕、候选封面帧、发送中心队列和 opencli 辅助投稿链路均已实现 MVP。
- 已安装 GitHub CLI 2.93.0；本机当前可访问 `api.github.com`，但此前访问 `github.com:443` 超时，后续推送前需要重新检查网络和登录状态。

## 2026-06-09 Docker 8001 opencli 辅助服务
- 根据使用方式调整：Docker `http://127.0.0.1:8001` 继续作为唯一页面入口，不再要求打开 Windows 本地 `8002` 页面。
- 新增 `scripts/opencli_host_bridge.py`，在 Windows 主机上提供 opencli 辅助服务；Docker 后台找不到容器内 opencli 时，会通过 `OPENCLI_HOST_BRIDGE_URL` 把 opencli 命令交给 Windows 执行。
- 新增 `scripts/start_docker_opencli.ps1`，一键完成检查 opencli、启动辅助服务、`docker compose up -d --build` 刷新 Docker，并打开 `http://127.0.0.1:8001/publish`。
- `docker-compose.yml` 固定 `OPENCLI_LOCAL_BASE_URL=http://127.0.0.1:8001`，并配置 `OPENCLI_HOST_BRIDGE_URL=http://host.docker.internal:8765`；`.env.example` 同步更新。
- 发送中心顶部提示改为 Docker 8001 语义：继续使用 Docker 主页面，如果自动发送不可用，运行 `.\scripts\start_docker_opencli.ps1`。
- 已更新 `docs/PROJECT_GUIDE.md`、`docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`；已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py`、PowerShell 脚本语法检查通过；已运行 `.\scripts\start_docker_opencli.ps1 -NoBrowser`，确认 `http://127.0.0.1:8001/health`、`http://127.0.0.1:8765/health` 正常，并用浏览器打开 `http://127.0.0.1:8001/publish`。

## 2026-06-09 发送中心 opencli 自检和本地重启脚本（已被 Docker 8001 辅助服务替代）
- 说明：这一版曾尝试用 Windows 本地 `8002` 页面解决 opencli 检测问题；随后已按实际使用方式改为上一节的 Docker `8001` 主页面 + Windows opencli 辅助服务。
- 发送中心 opencli 检测增加 npm 全局目录兜底：当后台 PATH 不完整时，会继续读取 `APPDATA\npm`、用户 npm 目录、`npm root -g` 和 `npm config get prefix`，减少 Windows 本地服务误判“没有检测到 opencli”的情况。
- `/publish` 顶部错误提示改为新手可执行说明：如果已经安装 opencli，优先运行 `.\scripts\restart_opencli_local_server.ps1 -Port 8002` 重启 Windows 本地后台，再打开本地发送中心并按 `Ctrl + F5`。
- 新增 `scripts/restart_opencli_local_server.ps1`：脚本会确认 opencli 可用、停止指定端口旧后台、用 `.venv` 重新启动 FastAPI，并打开发送中心页面；默认端口为 `8002`。
- 点击“发送此条”或“开始发送全部”时，如果后台仍检测不到 opencli，会直接返回带重启脚本的清晰错误，不再让任务进入一串模糊启动失败。
- 已更新 `docs/PROJECT_GUIDE.md`、`docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`；已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py`、PowerShell 脚本语法检查通过。

## 2026-06-08 全页面 Apple 风格视觉美化
- 设计前已确认 Git 保存点：`2e055bd56acf3499f95c323a7646188b7c3ec133`，并在 `codex/feature-apple-ui-redesign` 分支继续页面美化。
- `app/templates/base.html` 更新全局应用骨架：左侧导航改为更紧凑的图标式导航项，顶部栏增加 `Local Studio` 状态、Windows 本地后台标识和个人工作区提示，静态资源版本号同步升级，方便浏览器刷新到新样式。
- `app/static/css/styles.css` 新增 2026-06-08 视觉层：统一浅色玻璃拟态背景、白色卡片、蓝色主按钮、柔和阴影、表格/表单/状态标签/审核页播放器/发送中心卡片等组件质感。
- 移动端导航改为横向紧凑条，并隐藏侧边栏说明卡；手机首屏能更快看到页面标题和主要操作，不再被长侧边栏占满。
- 片段审核页桌面断点已校正，默认桌面宽度保持左侧候选列表 + 右侧视频预览双栏，不提前折叠为上下布局。
- 已更新 `docs/UI_REFERENCE.md`、`NEXT_STEPS.md`，并新增 `design-qa.md` 记录本轮设计验收。
- 已验证：`.venv\Scripts\python.exe -m compileall app` 通过；临时启动 `http://127.0.0.1:8010` 后，用浏览器检查 `/`、`/tasks`、`/tasks/new`、`/system`、`/publish`、任务详情、片段审核和 `/subtitles`，桌面与手机宽度均无横向溢出。

## 2026-06-08 B站发送流程和投稿页面保留
- 发送成功后不再执行 `opencli browser <session> close`，抖音和 B站自动投稿完成后都会保留 OpenCLI Browser 页面，方便继续查看平台结果。
- B站 opencli 发送链路不再使用 `upload input[type='file']` 这类模糊选择器，改为页面脚本读取本地 `/media` 视频并注入视频上传控件，避开页面中多个 file input 导致的 `selector_ambiguous`。
- B站流程新增自动处理本地未提交草稿提示、等待上传完成、选择页面推荐封面、固定选择“内容无需标注”、按需保留/补充分区、填写发送中心简介、点击“立即投稿”并等待投稿成功信号。
- B站标签不再由发送中心强行写入，保持 B站页面默认/推荐标签；封面不再上传本地封面文件，改用 B站页面生成的推荐封面。
- 已更新 `docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`；已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py`、`node --check app\static\js\app.js` 通过。

## 2026-06-07 抖音发送封面确认和发布状态修复
- 抖音 opencli 发送链路把“AI 推荐封面”从单个长脚本拆成等待推荐图、点击推荐图、确认“是否确认应用此封面？”弹窗、验证封面已应用四步，减少页面弹窗或重绘导致的 `Detached while handling command`。
- 新增发布前校验：点击最终发布前会检查标题、作品描述、右侧投稿预览和封面状态；描述未写入、预览未出现或封面未确认时会返回明确错误码。
- 封面相关步骤增加最多 2 次自动重试，只处理 opencli 页面断开的瞬时错误，不绕过验证码、登录失效、风控或平台人工确认。
- 针对真实失败 `douyin_cover_confirm_not_found` 追加修复：封面候选图会排除抖音页面 logo、头像、icon 和顶部静态图片，避免误点左上角 logo 后跳到作品管理页；如果点击封面后跳转到作品管理，会返回 `douyin_cover_click_navigated` 方便定位。
- 发布成功后会自动执行 `opencli browser <session> close`，关闭本次自动投稿打开的 OpenCLI Browser 标签；关闭失败只记录到 `cleanup_outputs`，不会把已经发布成功的任务改成失败。
- 发送中心新增居中的“正在发布”状态框；点击单条发送或批量发送后立即显示，已有 `publishing` 任务时页面也会显示并自动刷新。
- 右侧手机投稿预览改为跟随当前卡片编辑内容实时更新，包括标题、平台话题、正文简介和封面帧。
- 已更新 `docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`；已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py`、`node --check app\static\js\app.js` 通过。

## 2026-06-07 DeepSeek Pro 整集分析空正文修复
- 修复远程 DeepSeek Pro 分析长视频时返回空 `message.content`，导致页面报“AI Chat Completions 响应中没有文本内容”的问题。
- `app/services/ai/remote_responses_provider.py` 对 DeepSeek Chat Completions 请求显式加入 `thinking: {"type": "disabled"}`，让整集切片分析直接输出严格 JSON，不把输出预算消耗在推理内容上；非 DeepSeek 的 OpenAI-compatible 接口不加该专属字段。
- `app/services/ai/base.py` 增强 Chat Completions 空正文报错信息，会带上 `finish_reason`、返回字段和推理内容长度，后续如果接口异常能更快定位。
- 新增 `scripts/test_remote_ai_chat_payload.py`，离线验证 DeepSeek 请求会关闭 thinking、普通接口 payload 不受影响、空正文错误包含诊断信息。
- 已验证真实 DeepSeek 连通测试通过，并对任务 `d38b9158aba1`（测试5 - 康熙来了）重新执行远程 AI 分析，成功生成 12 条候选片段，任务已进入片段审核流程。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_remote_ai_chat_payload.py`、`.venv\Scripts\python.exe scripts\test_remote_ai_transcript_input.py`、`.venv\Scripts\python.exe scripts\test_ai_json_validation.py`、`.venv\Scripts\python.exe scripts\test_transcript_markdown_format.py`、`.venv\Scripts\python.exe scripts\test_remote_ai_connection.py` 均通过。
- 本次不调整页面结构，因此不需要同步更新 `docs/UI_REFERENCE.md`。

## 2026-06-06 三类 AI 接口配置拆分
- 系统状态页的 AI 配置从弹窗改为页面内直接编辑，按 `1. 音频转写`、`2. 分析文字稿，生成候选切片`、`3. 发送中心生成发布文案` 三块展示。
- API Key 输入框改为普通文本框，页面会完整回显当前 `.env` 中的 Key，适合本机个人使用。
- `.env` 保存逻辑改为按键更新并保留原文件内容，避免保存 AI 配置时误删 `VOLCENGINE_ASR_API_KEY`、存储路径或其他本地配置。
- 新增 `AI_ANALYSIS_REMOTE_*` 和 `AI_PUBLISH_REMOTE_*` 独立远程接口配置；旧 `AI_REMOTE_*`、`AI_REMOTE_PUBLISH_MODEL` 仍作为兼容默认值读取。
- 任务详情页远程按钮文案统一为“远程 AI 分析”，发送中心发布文案固定使用 `AI_PUBLISH_REMOTE_*` 接口。
- 新增 `scripts/test_ai_config_service.py`，验证保存配置不会删除火山转写 Key 和其他无关 `.env` 内容。

## 2026-06-06 发送中心 DeepSeek 发布文案模型配置
- 新增 `AI_REMOTE_PUBLISH_MODEL` 配置，默认 `deepseek-v4-flash`，用于发送中心 AI 标题、话题和简介生成。
- 发送中心点击“AI 补齐标题/话题”或“重新生成标题/话题”时固定使用远程 DeepSeek 发布文案模型，不再跟随 `AI_DEFAULT_PROVIDER` 切到本地 Ollama。
- 系统状态页的“AI 配置”弹窗新增“发布文案模型”下拉框，保存后会写入 `.env` 并立即应用到当前运行服务。
- 保留失败回退：DeepSeek Key 缺失、接口失败或返回异常时，发送中心继续使用本地规则生成发布文案，并把错误记录到 metadata。
- 新增 `scripts/test_send_center_opencli_queue.py` 回归用例，确认默认 Provider 为 local 时发送中心 AI 文案仍走 DeepSeek 发布文案模型。

## 2026-06-06 远程转写与 DeepSeek 失败确认机制
- 调整转写 Provider 逻辑：任务详情点击“开始处理 / 继续处理”后默认走远程转写；远程不可用时任务会暂停并展示失败原因，不再自动切到本地 faster-whisper。
- 任务详情页新增“改用本地模型转写”按钮，只在远程转写失败且未生成转写文件时显示；点击前会二次确认，确认后才会以 `provider=local` 重新执行转写。
- 转写状态接口新增 `local_retry_available` 标记，前端据此控制本地重试按钮；动态恢复的“重新远程转写”按钮也会补绑定点击事件。
- 任务列表的“当前状态”列会在失败任务下方显示简短失败原因，避免只看到“失败”但不知道远程服务哪里不可用。
- DeepSeek AI 分析也取消自动降级本地 Ollama：远程 DeepSeek 报错时会暂停 AI 分析并提示原因，用户需要手动点击“本地 AI 分析”才会使用本地模型。
- `.env.example` 和默认配置已同步：`TRANSCRIPTION_FALLBACK_PROVIDER` 默认留空，避免新环境默认自动本地兜底。
- 已更新 `docs/UI_REFERENCE.md`、`docs/TASK_FLOW.md` 和 `NEXT_STEPS.md`，记录新的确认式本地模型流程。
- 已验证：`.venv\Scripts\python.exe -m py_compile app\services\transcript_service.py app\services\task_service.py app\routers\tasks.py scripts\test_volcengine_transcription_provider.py`、`.venv\Scripts\python.exe scripts\test_volcengine_transcription_provider.py`、`.venv\Scripts\python.exe scripts\test_transcript_background_start.py`、`node --check app\static\js\app.js` 均通过。

## 2026-06-04 AI 置信度分数兼容修复
- 修复远程 AI 返回 `confidence_score` 为 8.9、7.8 这类十分制分数时，Pydantic 校验要求 0 到 1 导致 `AI 返回非法 JSON，安全重试后仍失败` 的问题。
- `app/services/ai/ai_clip_analyzer.py` 在 AI JSON 进入字段校验前会统一规范化置信度：0 到 1 原样保留，1 到 10 自动除以 10，10 到 100 自动除以 100，非法值兜底为 0.7，最终夹在 0 到 1 范围内。
- `scripts/test_ai_json_validation.py` 新增十分制和百分比格式回归测试，覆盖 `8.9 -> 0.89` 和 `92% -> 0.92`，避免后续远程 AI 再因同类分数字段失败。
- 已验证：`.venv\Scripts\python.exe scripts\test_ai_json_validation.py` 通过；`.venv\Scripts\python.exe -m py_compile app\services\ai\ai_clip_analyzer.py scripts\test_ai_json_validation.py` 通过。
- 本次不调整页面结构，因此不需要同步更新 `docs/UI_REFERENCE.md`。

## 2026-06-04 任务详情日志侧栏精简
- 按浏览器标注反馈，移除任务详情页右侧“日志 / 元信息”路径清单，避免和基础信息、运行日志重复。
- 右侧侧栏现在只保留“运行日志”，继续使用原有 `runtime-log-lines` 和 `runtime-log-state` 节点读取并刷新 `logs/process.log`。
- 同步更新 `docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`，记录新的页面结构和验收方式。

## 2026-06-04 AI 远程分析旧字段兼容修复
- 修复远程 AI 返回旧字段导致分析失败的问题：当 AI 输出 `clip_key`、`viral_value`、`reason`、`editing_suggestion` 等旧字段时，程序会在 Pydantic 校验前自动转换为当前需要的 `clip_id`、`spread_value`、`highlight_reason`、`suggested_editing`。
- 新增 AI 片段字段保底逻辑：缺少 `clip_id` 时自动生成 `clip_001` 这类编号；缺少 `summary`、`spread_value`、`suggested_editing` 时填入可审核的安全默认值；缺少 `duration_seconds` 但有起止时间时自动计算片段时长。
- `scripts/test_ai_json_validation.py` 新增旧字段回归用例，覆盖本次报错里的 `clip_key` / `viral_value` / `reason` 格式。
- 本次不调整页面结构、不扩大 Prompt 或模型接入范围，因此不需要同步更新 `docs/UI_REFERENCE.md`。

## 2026-06-02 DeepSeek AI JSON 解析稳定性修复
- 修复使用 2 号“康熙来了综艺短视频切片专家”Prompt 运行 DeepSeek AI 分析时，AI 返回接近 JSON 但存在尾随逗号、Markdown 代码块、未加双引号字段名或 Python 风格布尔值，导致 `AI 返回非法 JSON，安全重试后仍失败` 的问题。
- `app/services/ai/ai_clip_analyzer.py` 新增 AI JSON 提取和轻量修复流程：先尝试标准 JSON，再提取正文里的 JSON 主体，并兼容常见 AI 输出瑕疵；修复后仍会继续走 Pydantic 字段校验、时间范围校验和片段时长校验。
- `app/services/ai/remote_responses_provider.py` 将 DeepSeek Chat Completions 的 `max_tokens` 从 4096 提高到 8192，并加入较低 `temperature`，降低长 Prompt 输出半截 JSON 或格式漂移的概率。
- `scripts/test_ai_json_validation.py` 新增本地回归测试，覆盖标准 JSON、Markdown fenced JSON、尾随逗号、未加双引号字段名和 Python literal 五种情况。
- 已验证：`.venv\Scripts\python.exe scripts\test_ai_json_validation.py` 通过；`.venv\Scripts\python.exe -m py_compile app\services\ai\ai_clip_analyzer.py app\services\ai\remote_responses_provider.py scripts\test_ai_json_validation.py` 通过；`.venv\Scripts\python.exe scripts\test_remote_ai_connection.py` 通过。

## 2026-06-02 停止转写卡住修复
- 修复任务详情页点击“停止转写”后一直停留在“正在停止 / 转写中”的问题。
- 原因：如果后台服务重启过，内存里的运行中任务标记会丢失，但 `transcript_progress.json` 仍可能停在 `cancelling`，前端会持续轮询并一直显示正在停止。
- `app/services/task_service.py` 新增停止转写自愈逻辑：当状态查询或再次点击停止时，发现 `cancelling` 进度已经没有真实后台任务承接，会自动写成 `cancelled`，并把任务状态退回可重新处理的 `pending_processing`。
- `scripts/test_transcript_background_start.py` 新增回归测试，覆盖服务重启后遗留 `cancelling` 进度的自动收尾场景。

## 2026-05-30 火山引擎远程转写请求修复
- 已定位“测试4”远程转写失败原因：程序把 MP3 二进制直接 POST 到火山引擎接口，服务端按 JSON 解析时遇到 MP3 文件头 `ID3`，返回 HTTP 400：`invalid character 'I' looking for beginning of value`。
- `app/services/transcript_service.py` 已改为按火山引擎极速版要求发送 JSON 请求体，将音频内容 base64 后放入 `audio.data`，并设置 `Content-Type: application/json`、`X-Api-Sequence: -1`。
- 新增火山引擎业务状态码检查；当分段转写遇到 `20000003` 无有效人声且当前允许空结果时，会跳过该空白小段继续处理，避免静音片段导致整条视频失败。
- `scripts/test_volcengine_transcription_connection.py` 已兼容当前 `.env` 中的 `VOLCENGINE_ASR_APP_KEY` / `VOLCENGINE_ASR_ACCESS_KEY` 配置，不再只检查 `VOLCENGINE_ASR_API_KEY`。
- 已运行 `.venv\Scripts\python.exe scripts\test_volcengine_transcription_provider.py`、`.venv\Scripts\python.exe -m compileall app scripts`。
- 已用“测试4”的音频做真实火山引擎连通性测试：前 8 秒静音片段按空结果跳过，前 60 秒可成功识别 16 句。

## 2026-05-30 分支合并确认
- 已检查当前目录为 `C:\Users\10578\Documents\New project 2`，当前分支原本为 `codex/subtitlefunction`，工作区干净，没有未提交改动。
- 本地没有 `main` 分支，当前项目主分支为 `master`。
- 已确认 `codex/subtitlefunction` 已经是 `master` 的祖先分支，执行 `git merge codex/subtitlefunction` 后 Git 返回 `Already up to date.`，说明当前分支内容已经合入主分支。
- 当前已停留在 `master` 分支；本次合并没有产生代码冲突，也没有改动业务文件。

## 2026-05-27 片段审核功能优化
- 片段审核页新增候选片段删除能力：每张候选卡片提供“删除”按钮，点击后立即从页面移除，并通过 `DELETE /api/tasks/{task_id}/clips/{clip_id}` 写入数据库隐藏状态。
- `clip_candidates` 表新增 `is_deleted` 和 `deleted_at` 字段；候选片段列表、启用片段统计和自动切片流程默认排除已删除片段，删除不会影响源视频、转写文件或已生成切片文件。
- 候选片段卡片改为更紧凑的审核视图：保留标题、起止时间、摘要和常用操作按钮，推荐理由、传播价值、剪辑建议和 AI 来源收进可展开区域，方便连续浏览多个片段。
- 前端删除成功后会自动禁用空列表下的“保存修改 / 生成切片”按钮，避免误操作。

## 2026-05-27 AI 片段完整性优化
- 新增“综艺访谈完整上下文专家”Prompt，面向康熙来了、综艺和访谈内容，要求 AI 选择包含铺垫、核心爆点 / 观点、反应和自然收尾的完整短视频单元；数据库初始化优先写入空的 2 号方案，如果 2 号已有内容则写入空的 3 号方案，不覆盖用户已编辑 Prompt。
- 默认切片最长建议从 2 分钟调整为 5 分钟；新建任务页补充提示：直播干货建议 3-5 分钟，综艺访谈建议 4-6 分钟。
- AI 分析结果继续只做格式、时间范围和最大时长校验；新增低于 45 秒候选片段的日志提醒，提示可改用 2 号 Prompt 或调高最大切片时长后重跑。
- 片段审核页文案调整为“AI 结果检查入口”，AI 分析完成后提示可检查后直接生成切片，不再把审核页描述为必须逐条补剪的人工环节。

## 2026-05-26 字幕页片段内剪切重构
- 重构 `/subtitles/{task_id}` 的“修改剪切”弹窗。弹窗现在加载当前已生成的短视频片段，不再加载整段源视频；剪切时间轴改为片段内相对时间，默认范围为 `00:00` 到片段结尾。
- 字幕页剪切交互改为复用项目已有的 noUiSlider 双把手滑块，保留类似手机剪切的左侧播放按钮、黑色帧条、黄色选区和左右把手；保存时会把片段内相对入点 / 出点换算回原视频绝对时间，并写回片段审核数据。
- 已验证：`node --check app/static/js/app.js` 通过；`python -m compileall app` 通过；浏览器已确认页面加载新版 `app.js`、noUiSlider 资源和当前输出片段媒体地址。

## 2026-05-26 片段审核源监视器滑块升级
- 将片段审核页源监视器的手写时间轴拖拽替换为本地静态依赖 `noUiSlider 15.8.1`，保留现有深色弹窗、视频预览、入点 / 出点按钮和“应用到片段”流程。
- noUiSlider 负责双手柄范围选择、蓝色选区、刻度、1 / 5 / 15 秒步长、最小时长和任务最大片段时长限制，拖动时会实时同步入点、出点、片段时长和播放头。
- 新增本地 vendor 文件 `app/static/vendor/nouislider/`，不依赖 CDN，也不引入 npm / React / Vue。
- 已验证：`node --check app/static/js/app.js` 通过；`.venv\Scripts\python.exe -m compileall app` 通过。

## 2026-05-25 抖音 + B站发布后台接入
- 新增独立 `/publish` 发布中心，左侧导航增加“发布中心”，字幕工作台的推送入口改为跳转到发布中心。
- 新增抖音后台和 B站后台两个页签，每个后台包含开放平台应用配置、发布账号、待发布切片、批量配置和发布记录。
- 新增 `publish_platform_configs`、`publish_accounts`、`publish_jobs` 三类发布数据表，保存平台配置、授权账号、发布任务、平台返回 ID、审核状态、错误码和重试次数。
- 新增 `/api/publish/...` 系列接口，支持保存平台配置、测试配置、抖音 OAuth 授权链接、保存账号、创建草稿、创建人工发布任务、真实接口发布、失败重试和人工标记状态。
- 新增 `DouyinPublishProvider` 和 `BilibiliPublishProvider`；抖音 provider 已准备官方上传视频和创建视频调用链路，B站 provider 先完成配置、字段和状态适配，等待开放平台投稿接口权限后补齐真实调用。
- 安全边界：不保存平台账号密码、不使用 Cookie、不模拟网页登录；真实发布前前端会二次确认，未配置账号或凭证时后端会拦截且不会创建错误任务。
- 已验证：`.venv\Scripts\python.exe -m py_compile ...` 通过；`/publish`、`/api/publish/platforms`、`/api/publish/accounts`、`/api/publish/jobs` 返回 200；未配置账号时调用真实抖音发布返回 400，发布任务数量不增加。

## 2026-05-25 字幕工作台剪切弹窗升级
- 按浏览器标注和参考截图，把 `/subtitles/{task_id}` 的“修改剪切”从简单预览改为真正的入点 / 出点编辑弹窗。
- 弹窗改为读取源视频时间线，显示黑色帧条、左侧播放区、粉色外框和黄色选区，左右黄色手柄可拖动调整入点 / 出点。
- 时间轴会围绕当前片段自动放大显示，避免 40 分钟以上源视频里选区太窄不好拖。
- 弹窗新增入点 / 出点输入框、“设当前位置为入点 / 出点”、“预览这一段”和“保存入点 / 出点”；保存会调用现有候选片段更新接口写回数据库。
- 保存入点 / 出点后，需要回到片段审核页重新生成切片，字幕工作台里的旧切片视频才会被新时间范围替换。
- 已验证：`node --check app/static/js/app.js` 通过；`python -m compileall app` 通过；浏览器打开 `http://localhost:8001/subtitles/f8e1edf6a57f` 后，弹窗可打开并显示放大的黄色剪切选区。

## 2026-05-25 片段审核源监视器
- 片段审核页每条候选片段新增“编辑出入点”按钮，可打开深色源监视器弹窗。
- 源监视器支持源视频预览、当前时间码、时间轴缩放、蓝色入点 / 出点范围、播放头、左右手柄拖拽和 1 / 5 / 15 秒微调。
- 支持“设为入点”“设为出点”“跳到入点”“跳到出点”“预览当前片段”和“应用到片段”；应用后先回填当前卡片，仍需点击“保存修改”写入数据库。
- 片段转写接口支持传入页面上未保存的 `start_time` / `end_time`，方便调整后立即核对对应原文。

## 2026-05-25 运行日志浅色化
- 任务详情页右侧“运行日志”从黑色终端风格改为浅色玻璃卡片风格，和当前 Apple 风格后台页面保持一致。
- 日志内容区域改为浅蓝白背景、深灰文字和蓝色细滚动条，保留自动换行、固定高度和滚动查看最新日志的能力。
- 本次只调整前端样式文件，不改变 AI 分析、日志读取和任务处理逻辑。

## 2026-05-24 AI 分析日志和进度条
- 任务详情页 AI 分析区域新增独立进度条，点击 DeepSeek AI 分析或本地 AI 分析后会立即显示当前阶段和百分比。
- 右侧“日志 / 元信息”侧栏新增运行日志框，会读取 `logs/process.log` 的最新内容，AI 分析时前端每 3 秒自动刷新一次。
- 新增 `GET /api/tasks/{task_id}/ai-analysis-status`，统一返回 AI 分析状态、进度、任务日志尾部和分析文件是否存在。
- AI 分析接口改为在线程池中执行，避免长时间 AI 请求阻塞同一服务里的状态 / 日志查询。

## 2026-05-24 长视频 AI 分析稳定性修复
- 远程 DeepSeek 和本地 Ollama 现在统一使用 3 分钟左右的小段分段分析，不再把 40 分钟或 1 小时以上转写全文一次性塞给 AI，避免长 JSON 输出被截断。
- 新增 AI JSON 兼容层：可自动把 `clip_key` 转为 `clip_id`，从 `viral_value` 等字段补齐 `spread_value`，并为缺失的摘要、推荐理由、剪辑建议提供保底值。
- 分段分析改为“部分失败可跳过”：单个小段失败会写入任务日志，只要其他小段生成了可用候选，就会合并、去重、排序并进入人工审核。
- DeepSeek Chat Completions 的 `max_tokens` 提高到 8192，同时页面端会把超长技术错误压缩成可读摘要，详细错误继续写入任务日志。
- 已同步更新当前 SQLite 中的 2 号“康熙来了综艺短视频切片专家”Prompt，让它直接输出程序需要的 `clip_id`、`spread_value` 等字段。
- 新增 `scripts/test_ai_long_analysis_resilience.py`，覆盖远程分段、旧字段兼容和部分分段失败仍保留可用候选。
- 已运行 `.venv\Scripts\python.exe -m compileall app scripts`、`.venv\Scripts\python.exe scripts\test_ai_json_validation.py`、`.venv\Scripts\python.exe scripts\test_local_ai_chunked_analysis.py`、`.venv\Scripts\python.exe scripts\test_ai_long_analysis_resilience.py`。

## 2026-05-24 国内远程转写服务商接入
- 新增可切换转写服务商配置：`TRANSCRIPTION_PROVIDER`、`TRANSCRIPTION_FALLBACK_PROVIDER`，默认优先使用火山引擎远程转写，失败后可自动退回本地 faster-whisper。
- 接入火山引擎大模型录音文件极速版识别：分段压缩为 16k 单声道 MP3/OGG 后请求 `https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash`，再把 `utterances` 转成现有 `transcript.md` 时间戳格式。
- 预留阿里云、腾讯云、讯飞 Provider 名称；当前版本只完整实现 `volcengine` 和 `local`，未实现厂商会给出明确错误提示。
- 任务详情页转写进度会显示实际来源，例如“火山引擎远程转写 / volc.bigasr.auc_turbo / remote / mp3”。
- 任务详情页新增“停止转写”按钮；后台任务会在当前分段结束后停止，并把进度标记为 `cancelled`，方便重新生成转写。
- 新增 `scripts/test_volcengine_transcription_provider.py` 验证火山引擎结果解析和远程失败本地兜底；新增 `scripts/test_volcengine_transcription_connection.py` 用真实短音频检查火山引擎 Key 和接口。
- 已运行 `.venv\Scripts\python.exe -m compileall app scripts`、`scripts/test_transcript_chunking.py`、`scripts/test_transcript_background_start.py`、`scripts/test_volcengine_transcription_provider.py`。

## 2026-05-24 自动加字幕功能完善
- 将 `/subtitles` 调整为“字幕任务列表”一级页面，切片生成后按任务分组展示，用户需要先进入具体任务再处理字幕，避免不同视频混在一个工作台里。
- 新增 `/subtitles/{task_id}` 单任务字幕工作台，展示该任务下每条输出切片、原视频预览、字幕状态、带字幕成片预览和后续推送入口。
- 新增 `subtitle_style_presets` 和 `subtitle_jobs` 数据表，字幕样式从浏览器本地保存改为写入 SQLite，字幕生成状态也会落库。
- 自动加字幕已接入真实 FFmpeg 流程：从任务转写文本按切片时间范围生成 `.ass` 字幕文件，再输出到任务目录 `06_subtitled`。
- 新增带字幕视频访问接口 `/media/tasks/{task_id}/subtitled-clips/{output_clip_id}`，页面可直接对比原切片和带字幕成片。
- “修改剪切”改为弹出剪切预览窗口，提供类似手机剪切时间轴的滑动预览入口；真正保存起止时间仍回到片段审核页执行。
- 修复 Windows 本地服务读取 Docker 路径 `/workspace/tasks/...` 的兼容问题，会自动映射回当前任务存储目录。
- 已验证：`python -m compileall app` 通过；`/subtitles` 和 `/subtitles/37601f8548fd` 页面返回 200；任务 `37601f8548fd` 已成功生成 1 条带字幕视频并能通过媒体接口访问。

## 2026-05-24 v1.1 清理与呈现优化

- 将 FastAPI 版本号更新为 `1.1.0`，侧边栏文案更新为“v1.1 本地切片全流程”。
- 删除根目录重复 UI 图片副本，保留 `docs/design/live_streaming_slicing_workflow_ui_16x9.png` 作为正式设计参考。
- 移除旧的 `/api/tasks/{task_id}/clips/generate` 切片占位接口，页面继续统一调用真实切割接口 `/api/tasks/{task_id}/process/cuts`。
- 删除早期未引用的 `app/services/ai_clip_service.py` 占位服务，当前 AI 分析以 `app/services/ai/` 为主。
- 工作台、任务列表和字幕推送页新增或强化输出切片、待加字幕、待推送数据呈现。
- 重写 `docs/DATABASE_SCHEMA.md`，修复乱码并同步当前 `tasks`、`clip_candidates`、`output_clip`、AI Prompt 和 AI 分析历史表结构。
- 同步更新 README、UI 参考、任务流、架构说明、片段审核说明和下一步计划。

## 2026-05-23 新增字幕、推送功能 demo

- 新增 `/subtitles` 字幕与推送工作台，切片生成后的 `output_clip` 记录会进入这里作为后续字幕工作流队列。
- 新增切片输出视频预览接口 `/media/tasks/{task_id}/output-clips/{output_clip_id}`，字幕工作台可直接播放本地已生成切片。
- 左侧导航新增“字幕推送”，任务列表新增“后续工作流”列，切片输出存在时会提示“待加字幕”。
- 片段审核页生成切片操作区新增“去字幕推送”入口，生成成功提示用户继续进入字幕、打码和发布配置。
- 字幕工作台预留视频查看、删除、返回修改剪切、自动加字幕、字幕样式保存、打码区域和抖音 / B站一键推送入口。
- 新增 `docs/SUBTITLE_AND_PUBLISH_PLAN.md`，记录字幕渲染、打码和平台推送的后续落地计划。

## 2026-05-23 Ai分析预览修改和数目修改

- 新增 `ai_analysis_runs` 历史表，每次 AI 分析完成都会保存一次完整结果，包括分析编号、AI 来源、模型、Prompt 方案、目标候选数量、整体总结、降级提示和候选片段 JSON。
- 任务详情页 AI 分析区域新增“候选片段数量”输入框，默认 5 条，分析前会自动保存到任务配置。
- AI 分析预览改为可持久显示：刷新页面后会读取最新历史记录；旧任务如果还没有历史记录，会回退读取当前 `analysis/candidate_clips.json`。
- 新增“历史分析结果”面板，可查看每次分析的来源、模型、Prompt 方案、时间、候选条数和总结，并支持一键恢复整次历史分析结果。
- 恢复历史分析时会重新写入当前 `analysis/candidate_clips.json` 和 `clip_candidates` 表，片段审核页会立即使用恢复后的候选片段。

## 2026-05-23 Ai分析UI修改和审核问题修改

- 任务详情页的 AI Prompt 方案从三张并排卡片改为文件夹标签式布局，顶部只显示 1、2、3 号方案，下面只展示当前选中方案的名称和可拉长 Prompt 输入框。
- AI 分析完成后不再立即刷新页面，而是在 AI 分析区域下方显示本次选取结果摘要，包括实际 AI 来源 / 模型、候选数量、每条片段标题、开始时间、结束时间和视频长度。
- AI 分析结果摘要新增“转到片段审核”按钮，可直接进入当前任务的片段审核页。
- AI 分析接口补充返回 `provider_label`、`model`、`analysis_summary`、`clip_summaries` 和 `/clips/review` 审核地址；远程失败自动降级本地时会把原因返回给页面。
- 片段审核页继续读取 `analysis/candidate_clips.json` 中的 `analysis_meta` 展示 AI 来源；如果远程不可用后自动降级，审核页显示本地 Ollama 属于实际执行结果。
- 远程分析按钮文案改为“DeepSeek AI 分析”，本地 `.env` 已按 DeepSeek OpenAI-compatible 配置更新为 `https://api.deepseek.com`、`deepseek-v4-flash`、`chat_completions`，并已通过远程连通性测试。
- 更新全局静态资源版本号，避免 Docker 重建后浏览器继续使用旧版 CSS / JS 缓存，导致 AI Prompt 标签页仍按旧三列样式显示。
- Docker 本地开发模式改为挂载 `app/` 和 `prompts/` 到容器内，并使用 `uvicorn --reload` 启动；后续修改页面、样式、JS、Python 或 Prompt 后，刷新网页即可看到，Python 改动会自动重载服务。
- `/static` 静态资源增加本地 no-cache 响应头，避免浏览器继续使用旧 CSS / JS。

## 2026-05-23 Docker 名称统一与旧镜像清理

- `docker-compose.yml` 新增 Compose 项目名 `live-streaming-slicing-workflow`，避免 Docker Desktop 继续按目录名显示为 `newproject2`。
- `workflow` 服务新增固定镜像名 `live-streaming-slicing-workflow:latest`，后续重新构建会覆盖这个明确命名的镜像，不再继续生成 `newproject2-workflow`。
- 保留容器名 `live-streaming-slicing-workflow`、端口 `8001:8001`、项目内数据库挂载和 E 盘任务产物挂载，避免影响已有数据和视频文件。
- 确认 Docker Desktop 中多个 2.39GB 的 `<none>` 主要来自历史重新构建留下的悬空旧镜像；普通启动不会每次复制一份 2GB 项目内容。
- 本轮清理策略只处理旧 Compose 项目资源、悬空镜像和无用构建缓存，不删除当前可运行镜像、数据库、任务目录或 E 盘产物。

## 2026-05-23 Ai分析prompt制定
- 任务详情页的“AI 偏好”升级为“AI Prompt 方案”：提供 1、2、3 号全局共用方案卡片，可编辑方案名称和完整 Prompt。
- 新增全局 `ai_prompt_presets` 表，并为任务增加当前选中的 `ai_prompt_preset_id`；数据库初始化时自动写入 1 号默认直播切片分析专家 Prompt。
- AI 分析时改为读取当前任务选择的 Prompt 方案，再注入 `{{MAX_CLIP_DURATION}}`、`{{TARGET_CLIP_COUNT}}`、`{{AI_PREFERENCE}}`、`{{TRANSCRIPT_TEXT}}`。
- 远程 AI 分析前新增二次确认，提醒会使用当前 Prompt 方案重新生成候选片段并覆盖已有 AI 候选结果。
- AI 返回 JSON 允许不包含 `task_id`，程序会自动补当前任务 ID，兼容新的默认 Prompt 输出格式。
- 已运行 `python -m compileall app scripts`、`scripts/test_ai_json_validation.py`、`scripts/test_mock_transcript_analysis.py` 和 `scripts/test_local_ai_chunked_analysis.py`。

## 2026-05-21 列表序列变更
- 根据浏览器标注调整左侧导航菜单顺序：工作台、新建任务、任务列表、片段审核、系统状态。
- 本次只调整全局基础模板 `app/templates/base.html` 的导航排列，不改变页面路由和功能逻辑。
- 同步更新 `NEXT_STEPS.md` 和 `docs/UI_REFERENCE.md`，记录当前菜单顺序。

## 2026-05-21 第二十二轮：Docker 一键启动接入

- 新增 `Dockerfile`，使用可从微软镜像源拉取的 Python 3.12 Bookworm 镜像构建后端运行环境，并在容器内安装 `ffmpeg`，让 `ffmpeg/ffprobe` 可在 Docker 里直接使用。
- 构建时会移除基础镜像里本项目不需要的 Yarn apt 源，避免 Yarn 签名问题阻断 FFmpeg 安装。
- 新增 `docker-compose.yml`，固定映射 `8001:8001`，容器名为 `live-streaming-slicing-workflow`，并读取本地 `.env`。
- Docker 版数据库继续挂载到项目 `data/`，任务产物继续挂载到 E 盘 `E:\直播间切片工作流存储`，容器内路径为 `/workspace/tasks`。
- `app/core/config.py` 新增 `DATA_DIR`、`DATABASE_PATH`、`STORAGE_ROOT`、`TASKS_DIR` 环境变量读取能力，本地运行默认值保持不变。
- 新增 `.dockerignore`，避免 `.venv`、真实 `.env`、数据库、任务产物、大视频和音频文件进入 Docker 镜像。
- 更新 `.env.example`、`README.md`、`docs/PROJECT_GUIDE.md` 和 `NEXT_STEPS.md`，补充 Docker 启动、停止、查看端口和测试说明。
- 修复任务详情页已完成转写后反复自动刷新的问题：转写轮询完成后只停止轮询，不再自动刷新整页，并给 `app.js` 增加版本参数避免浏览器继续使用旧缓存。

## 2026-05-20 第二十一轮：AI 调用配置诊断与本地降级修复

- 远程 AI Key 读取改为兼容 `AI_REMOTE_API_KEY` 和 `OPENAI_API_KEY`；系统状态页会提示 Key 是否看起来有效，避免把 1 位或空密钥误认为已配置。
- AI URL 拼接增加防呆：`https://ai.oneinfinityai.com/v1` + `/v1/responses` 不会再拼成重复 `/v1/v1/responses`，本地完整 endpoint 也不会重复拼接 `/chat/completions`。
- 片段审核远程分析明确使用 `AI_REMOTE_REVIEW_MODEL`，默认仍为 `gpt-5.5`、Responses 协议、`xhigh` 推理强度和不保存响应内容。
- 本地 AI 默认模型改为 `qwen3:8b`，并新增 `AI_LOCAL_HEALTH_TIMEOUT_SECONDS`；远程失败自动降级本地前会先检查 Ollama 是否在线、目标模型是否已安装。
- 新增 `scripts/diagnose_ai_environment.py`，用于一次性检查远程 Key、远程 JSON 调用、本地 Ollama 模型和本地 JSON 调用。
- 已清空本机 `.env` 里明显无效的 `AI_REMOTE_API_KEY=1`，避免它覆盖后续 `OPENAI_API_KEY` 兜底；没有把聊天中暴露过的真实 Key 写入文件。
- 本地 AI 片段审核改为分段分析：先从 `transcript.md` 提取时间戳正文，再按约 3 分钟小段分别请求 Ollama，最后合并、去重、按置信度筛选候选片段，避免完整 19 分钟转写一次性塞进 4096 context。
- 诊断脚本支持传入任务 ID，例如 `scripts\diagnose_ai_environment.py 37601f8548fd`，会显示真实任务转写长度、分段数量和最大 prompt 字数。
- 新增 `scripts/test_local_ai_chunked_analysis.py`，用模拟本地 Provider 验证长转写会被拆段、逐段分析并合并候选。
- 已用真实任务 `37601f8548fd` 跑通本地分段 AI 分析，生成 5 条候选片段，任务状态进入 `pending_review`。

## 2026-05-20 第二十轮：任务详情一键处理与 AI 自动降级

- 任务详情顶部操作改为“一键处理”：没有音频时自动提取音频，随后启动后台转写；已有 `transcript.md` 时不会重复转写。
- 保留“重新生成转写”作为明确的二级操作，点击前需要确认，避免误触后覆盖已有转写。
- 新增完整转写原文页面 `/tasks/{task_id}/transcript`，转写预览卡片可直接打开完整 `transcript.md`。
- 从任务详情顶部移除“生成切片”，切片生成只保留在片段审核页，并改为调用真实切割接口。
- 远程 AI 分析遇到 403、网络、超时、权限或余额类错误时，会自动降级尝试本地 AI，并在页面和日志中记录远程 / 本地结果。
- 调整任务详情布局和长路径换行，右侧日志 / 元信息栏更宽，不再轻易截断。

## 2026-05-20 第十九轮：转写 0% 卡住排查与稳定优先修复

- 将本地转写默认配置从 `large-v3 / cuda / float16` 改为 `medium / cpu / int8`，先保证 Windows 本地 MVP 可以稳定跑通；CUDA 加速仍可通过 `.env` 手动开启。
- 默认分段从 10 分钟缩短为 2 分钟，并在进度文件中写入模型、设备、计算类型、分段时长和更细的阶段说明，避免第 1 段长时间显示 0%。
- 新增 `/api/tasks/{task_id}/transcript-status`，任务详情页会自动轮询转写进度和预览内容；失败、完成或进度长时间不更新时会显示更明确的状态。
- 点击“生成转写 MD”前会清理旧的转写临时分段目录；如果发现过期的后台转写状态，会允许重新开始。
- 新增 `scripts/diagnose_transcription_environment.py` 和 `scripts/test_real_transcription_smoke.py`，用于检查 FFmpeg / FFprobe / faster-whisper / Python 环境，并用真实短音频验证转写是否真的可用。

## 2026-05-19 第十八轮：长音频后台分段转写

- 将本地 faster-whisper 转写从“一次性整段识别”改为“10 分钟分段 + 5 秒重叠 + 时间戳回填 + 自动拼接 Markdown”，降低 1 小时以上音频的失败风险。
- `/api/tasks/{task_id}/process/transcript` 现在会立即返回后台启动结果，不再让网页一直等待长时间转写完成。
- 新增 `transcripts/transcript_progress.json`，记录转写状态、当前分段、总分段数、百分比、进度说明和更新时间；任务详情页会显示转写进度条。
- CUDA / cuBLAS 失败后仍会自动 CPU 兜底，CPU 兜底模型改为 `medium / int8`，比 `large-v3` 在 CPU 上更适合长音频。
- 新增 `TRANSCRIPTION_CPU_FALLBACK_MODEL`、`TRANSCRIPTION_CHUNK_SECONDS`、`TRANSCRIPTION_CHUNK_OVERLAP_SECONDS` 配置示例。
- 新增 `scripts/test_transcript_chunking.py` 和 `scripts/test_transcript_background_start.py`，覆盖 65 分钟分段、时间戳偏移、Markdown 拼接、后台启动和重复点击保护。

## 2026-05-17 第十七轮：修复本地转写 CUDA 依赖缺失兜底

- 定位异常 `Library cublas64_12.dll is not found or cannot be loaded` 的原因：当前本地 faster-whisper 默认使用 `TRANSCRIPTION_DEVICE=cuda` 和 `TRANSCRIPTION_COMPUTE_TYPE=float16`，但 Windows 环境缺少 CUDA 12 运行库 DLL，导致 GPU 转写无法启动。
- 修改 `app/services/transcript_service.py`：当 GPU/CUDA/cuBLAS/cuDNN/NVIDIA 相关加载或转写异常出现时，自动切换到 `cpu / int8` 再执行一次转写，避免任务直接失败。
- `transcript.md` 的任务信息中，转写设备现在记录实际运行配置，例如 `cuda / float16` 或自动兜底后的 `cpu / int8`。
- 新增 `scripts/test_transcript_cpu_fallback.py`，用模拟的 `cublas64_12.dll` 缺失错误验证 GPU 失败后会自动请求 CPU 兜底模型。
- 已通过 `python -m compileall app` 和 `python scripts/test_transcript_cpu_fallback.py` 验证。

## 2026-05-17 第十六轮：项目文档整理、Git 安全提交准备

- 重写 `README.md`，改为清晰的项目入口、文档索引、启动方式和敏感信息说明。
- 新增 `docs/PROJECT_GUIDE.md`，用新手友好的方式说明项目用途、启动步骤、页面测试和命令行测试。
- 新增 `docs/SECURITY_AND_GIT.md`，说明 `.env`、API Key、任务产物、大视频和 Git 提交流程。
- 强化 `.gitignore`，继续忽略真实 `.env`，并补充 `.env.*`、数据库、视频、音频、日志等本地生成物规则。
- 提交前确认 `.env` 未被 Git 追踪，真实 API Key 不会保存到 Git。

## 2026-05-17 第十五轮：本地 faster-whisper 真实转写

- 将 `transcripts/transcript.md` 从占位 Markdown 升级为本地真实语音转写输出。
- 新增 `faster-whisper` 依赖，默认使用 `large-v3`、中文、CUDA、float16，适配本机 RTX 显卡优先的本地工作流。
- 转写阶段只做语音识别和文本整理，不调用 AI、不生成内容总结。
- `transcript.md` 现在包含“分钟级转写”和“逐句时间戳原文”两部分；分钟级内容只是按分钟拼接真实原文，后续 AI 分析仍由独立 AI 分析按钮负责。
- 转写失败时会把任务状态更新为 `failed`，并记录清晰错误信息，避免把失败误认为成功。
- 新增 `scripts/test_transcript_markdown_format.py`，用于验证分钟级整理、逐句时间戳 Markdown 和页面预览解析。
- 同步更新 `.env.example`、`NEXT_STEPS.md`、`docs/ARCHITECTURE.md`、`docs/TASK_FLOW.md` 和 `docs/UI_REFERENCE.md`。

## 2026-05-17 第十四轮：新建任务表单简化与 AI 偏好迁移

- 新建任务页的视频来源简化为“上传本机视频”，移除 NAS / 本地文件选择、路径输入、浏览路径和打开目录入口。
- 重新设计上传视频入口，改为更清爽的自定义上传按钮，并在选择文件后显示文件名。
- “单条切片最长”从下拉选择改为分钟数输入框，支持用户直接填写 1-60 分钟。
- 新建任务页移除 AI 偏好输入；右侧小贴士说明 AI 偏好会在任务详情的“AI 分析”板块填写，并解释它只影响候选片段推荐方向，不直接决定最终成片。
- 任务详情页新增“AI 分析”板块，支持保存 AI 偏好，并在点击远程 / 本地 AI 分析前先保存偏好。
- 新增 `PATCH /api/tasks/{task_id}/ai-preference`，用于更新任务的 AI 分析偏好。
- 同步更新 `NEXT_STEPS.md` 和 `docs/UI_REFERENCE.md`。

## 2026-05-17 第十三轮：任务详情页信息定位、地铁进度线与转写预览优化

- 任务详情页标题区新增任务定位信息：任务 ID、写入时间、主题和当前状态，方便对照本地任务文件夹。
- 将“处理进度时间线”合并进“状态概览”卡片，改为地铁路线图节点表达。
- 进度节点颜色调整为：绿色表示已通过，蓝色表示当前阶段，黄色表示异常或待处理问题。
- 转写预览不再直接展示 Markdown 元信息，只解析带时间戳的真实转写文本。
- 如果转写文件只是占位内容，页面会明确提示“还没有真实语音转写内容”，避免误以为已经完成真实转写。
- 已通过 `python -m compileall app`、详情页 HTTP 访问和转写预览解析小测试。
- 尝试使用 Codex in-app Browser 做视觉验证时连接超时，本轮已改用本地 HTTP 返回内容确认页面结构。

## 2026-05-17 第十一轮：AI 配置弹窗修复与默认配置预置

- 修复系统状态页 AI 配置弹窗关闭体验：关闭按钮、取消按钮、点击遮罩和 ESC 都可以关闭弹窗。
- 修复 Jinja 模板中 `ai_config.values.xxx` 与字典 `values()` 方法冲突的问题，AI 配置表单现在能正确回显默认值。
- AI 配置弹窗改为固定头部和底部操作区，避免内容过高时找不到取消/保存按钮。
- 远程中转站默认配置为 `https://ai.oneinfinityai.com`、`gpt-5.5`、Responses 协议、`xhigh` 推理强度，并在 Responses 请求中设置不保存响应内容。
- 本地 AI 默认改为 Ollama：`http://127.0.0.1:11434/v1`、`chat_completions`，页面只需要选择模型。
- 本地 Ollama 模型选项预置为 `qwen3:8b`、`gemma3:12b`、`qwen3:14b`。
- 继续保持真实 `.env` 被 `.gitignore` 忽略，页面保存配置时 API Key 留空会保留旧密钥，不会写入 Git 管理文件。
- 同步更新 `.env.example`、`docs/AI_ANALYSIS.md`、`docs/UI_REFERENCE.md` 和 `NEXT_STEPS.md`。

## 2026-05-17 第十轮：运行入口、任务隐藏、AI 配置与空白页补齐

- 定位上传失败原因：电脑上同时存在旧服务 `http://127.0.0.1:8000` 和新服务 `http://127.0.0.1:8001`，旧服务未加载 `/api/tasks/upload`、`/clips`、`/system`、`/api/files/browse`，所以会出现 `Method Not Allowed` 或 404。
- 本轮固定后续验证地址为 `http://127.0.0.1:8001`，并在系统状态页展示推荐运行地址，避免误开旧端口。
- `tasks` 表新增软隐藏字段 `is_deleted`、`deleted_at`，任务列表、工作台、片段审核总览默认不显示已隐藏任务。
- 新增 `DELETE /api/tasks/{task_id}`，只隐藏任务记录，不删除原视频、切片文件和任务目录。
- 任务列表新增“隐藏”按钮，点击前会二次确认，并明确提示文件不会删除。
- 新增 AI 配置读写能力：`GET /api/settings/ai` 和 `POST /api/settings/ai`，配置保存到项目根目录 `.env`，API Key 留空时保留旧值。
- 系统状态页新增 AI 配置弹窗，可保存默认 Provider、请求超时、远程 AI、本地 AI 相关字段，并展示 AI 配置是否已保存。
- `/clips` 片段审核总览升级为审核工作台，展示待 AI、待审核、可切片、已完成、异常任务，并提供详情和审核入口。
- 单任务片段审核页没有候选片段时，显示更清晰的空状态和下一步按钮：返回详情、生成转写、运行远程 AI 分析。
- 同步更新 `NEXT_STEPS.md`、`docs/UI_REFERENCE.md`、`docs/AI_ANALYSIS.md`、`docs/DATABASE_SCHEMA.md`。

## 2026-05-16 第七轮：AI 片段分析模块

- 新增 `.env.example`，补充远程中转站 API 和本地大模型 API 配置项，真实 `.env` 继续由 `.gitignore` 忽略。
- `app/core/config.py` 新增根目录 `.env` 读取能力，支持 `AI_REMOTE_*`、`AI_LOCAL_*` 和默认 Provider 配置。
- 新增 `prompts/clip_analysis_prompt.txt`，把 AI 片段分析 Prompt 从 Python 代码中拆出，并支持最大时长、候选数量、AI 偏好、转写文本变量注入。
- 新增 `app/services/ai/` Provider 抽象层，包含远程 Responses Provider、本地模型 Provider 和 AI 片段分析编排。
- AI 输出现在会经过严格 JSON 解析、Pydantic 字段校验、片段时长校验、转写时间范围校验；非法 JSON 会自动安全重试一次。
- 补齐 `clip_candidates` 表兼容迁移字段：`clip_key`、`highlight_reason`、`suggested_editing`、`confidence_score`、`selected_by_default`、`reviewed`。
- 新增 `/api/tasks/{task_id}/process/ai?provider=remote|local` 接口，流程为 `pending_ai -> ai_analyzing -> pending_review`，失败进入 `failed` 并记录错误。
- 任务详情页新增“远程 AI 分析”“本地 AI 分析”按钮，并展示 AI 分析 JSON 文件路径状态。
- 片段审核页继续读取 `clip_candidates` 表，展示推荐理由、传播价值、剪辑建议和 AI 置信度。
- 新增 `scripts/test_remote_ai_connection.py`、`scripts/test_local_ai_connection.py`、`scripts/test_ai_json_validation.py`、`scripts/test_mock_transcript_analysis.py`。
- 新增 `docs/AI_ANALYSIS.md`，并同步更新架构、任务流、UI 参考、数据库结构和下一步计划。

## 2026-05-16

- 检查项目目录，确认 `C:\Users\10578\Documents\New project 2` 已存在。
- 确认当前目录已是 Git 仓库，但还没有提交。
- 确认没有旧 Node.js 原型结构，没有 `package.json`、`src`、`public`。
- 保留现有 `REAMD.txt` 与根目录 PNG 图片。
- 将现有 PNG 复制为 `docs/design/live_streaming_slicing_workflow_ui_16x9.png`，作为后续 UI 参考图。
- 创建 FastAPI + Jinja2 + SQLite 项目骨架。
- 创建工作台、任务列表、新建任务、任务详情、片段审核五个页面的首版占位页面。
- 创建任务模型字段草案、SQLite 初始化模块和服务接口占位。
- 创建 README、PRD、架构、任务流、UI 参考、下一步计划和 Codex 协作说明文档。
- 创建 `.venv` 虚拟环境并安装首版依赖。
- 修复新版 Starlette / FastAPI 下 `TemplateResponse` 参数调用方式。
- 本地启动服务并验证 `/`、`/tasks`、`/tasks/new`、`/tasks/demo-001`、`/tasks/demo-001/clips`、`/health` 均可访问。
- 查看 UI 设计图 `ChatGPT Image 2026年5月16日 11_54_05.png`，确认视觉方向为浅色 Apple 风格后台。
- 读取 `docs/UI_REFERENCE.md`、`docs/PRD_v0.1.md`、`docs/ARCHITECTURE.md`、`docs/TASK_FLOW.md`、`AGENTS.md`。
- 根据 UI 图升级 5 个页面：工作台、任务列表、新建任务、任务详情、片段审核。
- 扩充模拟任务数据和候选片段数据，用于前端页面展示。
- 系统性升级 `app/static/css/styles.css`，补充浅色背景、左侧导航、统计卡片、流程线、筛选栏、表格、片段审核卡片和视频预览占位样式。
- 使用本地 Chrome 无头截图验证主要页面渲染正常，截图保存于 `data/screenshots/`，该目录内容不提交到 Git。
- 实现任务管理基础闭环：新建任务表单提交到 `/api/tasks`，写入 SQLite，任务列表页读取真实数据库任务，任务详情页按 `task_id` 查询真实任务。
- 将任务状态改为英文状态码保存，并在页面层转换为中文展示。
- 为后续处理流程预留 `update_task_status(task_id, new_status)` 状态更新方法。
- 新增 `docs/DATABASE_SCHEMA.md`，记录当前 `tasks` 表字段和状态码。
- 通过 API 创建“闭环验证任务”，验证“创建任务 → 列表展示 → 详情查看 → 状态更新方法”可用。
- 将任务资料根目录切换为 `E:\直播间切片工作流存储`，数据库仍保留在项目内 `data/workflow.sqlite3`。
- 新建任务时会在 E 盘为每个任务创建 `source`、`audio`、`transcripts`、`analysis`、`clips`、`logs` 子目录。
- 接入真实上传接口 `/api/tasks/upload`，上传视频会保存到任务目录 `source/`。
- 接入 NAS / 本地路径任务创建校验，首版只记录原视频路径，不复制大视频。
- 新增目录浏览接口 `/api/files/browse`，用于在页面中浏览 Windows 可访问的视频文件。
- 新增 FFmpeg 音频提取接口 `/api/tasks/{task_id}/process/audio`，输出 `audio/source.wav`。
- 新增转写 Markdown 生成接口 `/api/tasks/{task_id}/process/transcript`，输出 `transcripts/transcript.md`。
- 任务详情页新增“提取音频”“生成转写 MD”操作，并展示任务目录、音频路径、转写路径和日志路径。
- 片段审核页改为优先读取真实候选片段，没有候选时显示空状态，不再误用模拟数据。
- 新增系统状态页面 `/system`，展示 E 盘存储、FFmpeg、FFprobe、数据库、任务数量和最近异常。
- 使用临时 1 秒测试视频验证“创建任务 → 提取音频 → 生成转写 MD → 状态推进到待 AI 分析”流程可用，并清理临时测试数据。
- 第八轮开发：将片段审核页接入真实 `clip_candidates` 数据，不再依赖静态 UI 或占位候选片段。
- 为 `clip_candidates` 表补充 `clip_key`、`highlight_reason`、`suggested_editing`、`confidence_score`、`selected_by_default`、`reviewed` 字段迁移，并兼容旧字段 `reason`。
- 新增 `/tasks/{task_id}/clips/review` 页面入口，保留 `/tasks/{task_id}/clips` 兼容旧入口。
- 片段审核页顶部展示任务名称、候选片段总数、启用片段数和最大切片时长。
- 片段审核页支持筛选“全部片段 / 仅启用 / 高传播价值”，支持按“推荐分 / 置信度”和“时间顺序”排序。
- 每条候选片段展示并读取真实字段：启用状态、标题、开始时间、结束时间、时长、摘要、推荐理由、传播价值、剪辑建议、AI 置信度。
- 新增候选片段编辑保存接口 `/api/tasks/{task_id}/clips/{clip_id}/update` 和 `/api/tasks/{task_id}/clips/batch-update`。
- 保存时校验时间格式、结束时间大于开始时间、自动重算 `duration_seconds`，并限制片段时长不得超过任务最大切片时长。
- 新增 `/api/tasks/{task_id}/clips/generate` 生成切片预留接口，当前返回“待视频切割模块接入”提示，不改变任务状态。
- 更新任务详情页、任务列表页、片段审核总览页的审核入口到 `/tasks/{task_id}/clips/review`。
- 新增 `docs/CLIP_REVIEW.md`，记录片段审核页真实数据来源、人工编辑能力、保存逻辑和后续切割流程。
- 第九轮开发：实现自动切割视频能力，`/api/tasks/{task_id}/process/cuts` 会读取启用片段并调用 FFmpeg 输出短视频。
- 新增 `output_clip` 表，用于逐条记录输出视频文件名、路径、状态和失败原因。
- 将切割逻辑封装到 `app/services/video_cut_service.py`，支持 FFmpeg 可用性检查、原视频存在检查、时间校验、安全文件名、重复文件自动加序号和批量切割。
- 切片输出目录约定为任务目录下 `05_clips/`，不覆盖已有文件。
- 新增任务状态 `completed_with_errors`，用于表示部分切片成功、部分切片失败。
- 片段审核页保留真实切割结果展示区域；按第八轮边界，“生成切片”按钮当前暂接 `/api/tasks/{task_id}/clips/generate` 预留接口，提示“待视频切割模块接入”。
- 任务详情页已展示切片输出数量、切片目录和已生成视频列表。
- 使用 8 秒测试视频验证 2 条正常片段成功生成 mp4，1 条结束时间早于开始时间的错误片段被单独记录为失败，任务状态更新为 `completed_with_errors`。
- 新增 `docs/VIDEO_CUTTING.md`，记录自动切割流程、FFmpeg 依赖、输出目录、错误处理和测试方式。
- 第十轮开发：解决片段审核页视频预览无法显示的问题。
- 新增 `/media/tasks/{task_id}/source-video` 本地源视频读取路由，按任务 ID 校验源视频存在后以内联视频响应给浏览器播放。
- 片段审核页右侧从静态视频占位改为真实 `<video controls>` 播放器；点击左侧候选片段“预览”按钮会跳到对应开始时间，并在片段结束时间自动暂停。
- 第十一轮开发：按浏览器标注优化片段审核卡片内容。
- 片段卡片中将“AI 置信度”改为“AI 来源 / 模型”，优先读取分析结果元信息和任务日志，展示远程 AI 或本地 Ollama 以及对应模型名。
- 新增 `/api/tasks/{task_id}/clips/{clip_id}/transcript-excerpt`，按候选片段起止时间读取逐句转写原文。
- 片段审核页新增右侧可折叠转写抽屉，点击“查看这一段转写”即可查看对应片段原文；播放预览按钮改为更明确的可点击按钮。
- 更新 AI 分析 Prompt，要求摘要、推荐理由和剪辑建议输出更完整、可判断价值的内容。
- 第十二轮开发：按浏览器标注调整视频预览尺寸。
- 片段审核页右侧预览栏从 380px 缩小到更稳的 340px，并限制播放器高度，竖屏视频会完整缩放显示，不再把页面撑出横向滚动。
- 第十三轮开发：修复 DeepSeek 远程 AI 分析配置。
- 远程 AI 默认配置改为 DeepSeek OpenAI-compatible Chat Completions：`https://api.deepseek.com`、`deepseek-v4-flash`、`chat_completions`，避免继续用 Responses 协议和 `gpt-5.5` 模型名请求 DeepSeek。
- 按用户要求将远程默认模型从 `deepseek-v4-pro` 调整为 `deepseek-v4-flash`。
- 系统状态页 AI 配置弹窗新增远程协议和 DeepSeek 模型选择；保存配置时如果检测到 DeepSeek，会自动使用 `chat_completions` 并让 Review Model 跟随 DeepSeek 模型。
# 2026-05-27 项目文件夹改名与回收站

- 已把 E 盘任务目录从短 ID 改为项目名：`测试2 - 19min`、`测试3 - 康熙来了20160104` 现在直接位于 `E:\直播间切片工作流存储` 根目录。
- 已新增 `E:\直播间切片工作流存储\_回收站`，并把已隐藏任务 `测试1`、`闭环验证任务` 移入回收站，文件没有删除。
- `tasks` 表新增 `task_dir_name` 字段，任务 ID 仍用于网页地址和数据库关联，本地文件夹名改由 `task_dir_name` 控制。
- 新建任务时会用项目名创建本地文件夹；遇到 Windows 不允许的字符会自动替换，遇到重名会自动加序号，避免覆盖旧项目。
- 任务列表删除动作已从“隐藏”改为“移入回收站”：页面仍会隐藏任务，但对应项目目录会移动到 `_回收站`。
- 新增一次性迁移脚本 `scripts/migrate_task_dirs_to_project_names.py`；默认 dry-run 只预览，带 `--apply` 才会真正移动文件夹并更新数据库路径。
- 已执行迁移，数据库备份为 `data/workflow.sqlite3.bak_20260527_011841`。

## 2026-05-29 封面 MVP 方案添加

- 发布中心新增封面 MVP：每条待发布切片都可以在发布前输入封面时间点并生成封面预览。
- 封面生成采用“视频截图 + 暗色遮罩 + 标题大字”的稳定方案，暂不调用 AI 生图。
- 新增发布封面接口：`POST /api/publish/covers` 可在创建发布任务前生成封面；`POST /api/publish/jobs/{job_id}/cover` 预留给已有发布任务重新生成封面。
- 封面文件保存到任务目录 `07_covers/`，并通过 `/media/tasks/{task_id}/covers/{file_name}` 以内联图片方式预览。
- 发布任务创建时会保存 `cover_file_path`，发布记录里能看到已生成封面缩略图。

## 2026-06-02 发送中心 2.0 版本

- 已把 `/publish` 从“发布中心”改为“发送中心”，页面不再展示抖音 / B站开放平台 API 配置、Client Key、Access Token、OAuth 和账号表单。
- 新页面以待发送队列为主：从已完成切片读取切好的原片、封面帧、AI 标题、AI 话题和平台状态，默认生成抖音 + B站双平台队列。
- 新增发送队列接口：`POST /api/publish/queue/refresh` 可从已完成切片生成 opencli 发送任务；当时使用的 `POST /api/publish/send/start` 和 `POST /api/publish/jobs/{job_id}/send` 已在 2026-07-19 随旧页面清理移除，当前统一使用单条 `publish-now` 与排期接口。
- 封面逻辑改为“从视频中选一帧”：新增 `POST /api/publish/covers/frames` 生成多张候选帧，页面可手动切换并保存，不再默认叠加标题大字。
- AI 元数据补齐已接入：优先使用切片标题，话题和简介可由 AI 根据标题、摘要、推荐理由和转写片段生成；缺失时页面可一键重新生成。
- 自动发送改为 opencli 网页自动化：抖音打开 `creator.douyin.com` 投稿页，B站打开创作中心投稿页；发送批次一次只执行一条，避免平台窗口互相抢焦点。
- 已新增 `scripts/test_send_center_opencli_queue.py`，验证抖音 / B站 opencli 命令组装和备用元数据生成；已通过页面渲染检查，确认 `/publish` 不再出现 API 配置词。

## 2026-06-04 牛马片场品牌更新

- 项目品牌从“直播切片工作流”更新为“牛马片场 / NiuMa Studio”，副标题为“本地 AI 高光生产后台”。
- 新增牛马吉祥物 logo：`app/static/img/brand/niuma-studio-logo.png`，使用蓝色片场灯和圆角应用图标风格，页面文字由 HTML 渲染。
- 左侧导航品牌位已改为吉祥物图标 + `牛马片场`，不再显示 `LS` 文字标。
- `app/core/config.py` 的应用名改为 `NiuMa Studio` / `牛马片场`，Docker Compose 项目名、镜像名和容器名改为 `niuma-studio`。
- 暂时保留 E 盘历史存储目录 `E:\直播间切片工作流存储`，避免影响已有任务、数据库记录和视频产物。

## 2026-06-04 浏览器 favicon 更新

- 已基于牛马吉祥物主 logo 生成浏览器图标资源：`niuma-studio-favicon.ico`、`niuma-studio-favicon-32.png`、`niuma-studio-apple-touch-icon.png`、`niuma-studio-icon-192.png` 和 `niuma-studio-icon-512.png`。
- `base.html` 的 `<head>` 已新增 `rel="icon"`、32x32 PNG、Apple touch icon 和 `theme-color`，用于浏览器标签页、收藏夹和保存快捷方式。
- `app/main.py` 新增 `/favicon.ico` 路由，兼容浏览器默认请求根路径 favicon 的行为。

## 2026-06-04 发送中心 opencli 检测修复

- 修复 Windows 本地环境下发送中心误判“没有检测到 opencli”的问题：检测逻辑现在会优先识别 `opencli.cmd`、`opencli.exe`、`opencli` 和 `opencli.ps1`。
- 当普通 PATH 检测不到时，会额外检查 npm 全局安装目录：`%APPDATA%\npm` 和 `%USERPROFILE%\AppData\Roaming\npm`。
- opencli 自动发送命令现在会使用检测到的完整可执行文件路径，避免后台服务环境变量不完整时启动失败。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，覆盖 Windows npm 目录里的 `opencli.cmd` 备用检测。

## 2026-06-04 发送中心 opencli 参数修复

- 修复自动发送第一步报错 `unknown option '--window'` 的问题。
- opencli `browser open` 命令已改为 `opencli browser <session> --window foreground open <url>`，不再把 `--window` 放到网址后面。
- 已补充发送中心测试，确认抖音 / B站打开页面命令里的 `--window` 参数位置正确。

## 2026-06-04 抖音 opencli 上传修复

- 修复抖音上传视频时报错 `{"code":-32000,"message":"Not allowed"}` 的问题：不再使用 OpenCLI 的 `upload input[type='file']` 直接塞文件。
- 新流程改为抖音页面脚本从本机 `OPENCLI_LOCAL_BASE_URL` 读取 `/media` 视频文件，构造浏览器 File 对象并触发上传控件 change 事件。
- 本机 `/media` 和 `/static` 响应已增加抖音 / B站页面读取所需的 CORS 响应头，用于 opencli 自动发送时读取本地切片文件。
- 抖音流程暂时跳过强制封面上传，先使用抖音默认/自动封面，避免封面 input 再次触发浏览器拒绝。

## 2026-06-04 自动字幕中文方块修复

- 修复自动加字幕后中文显示成小方块的问题：ASS 字幕生成会优先使用已保存的中文字体，并在字体不可用时兜底到 Windows 本机可用中文字体。
- FFmpeg `subtitles` 滤镜现在会显式传入 `C:\Windows\Fonts` 作为字体目录，避免 libass 找不到微软雅黑、黑体、Noto Sans SC 等中文字体。
- 字幕样式页面补充 `Noto Sans SC` 和 `Source Han Sans CN` 选项，方便后续选择更稳定的中文字体。
- 已验证：`python -m compileall app` 通过。

## 2026-06-04 发送中心标题话题安全与自动封面

- 发送中心新增本地内容安全清洗：AI 或人工填写的标题、平台话题和简介会自动规避低俗脏话、死亡血腥、暴力恐怖、色情、赌博博彩、诈骗引流、绝对化夸张等高风险表达。
- AI 元数据 Prompt 已明确要求 `tags` 返回平台 `#话题` 关键词，不再把标题重新解释成话题；页面字段同步改为“平台 #话题”。
- 刷新发送队列时会自动为每条切片截取一张默认封面帧，并写入发送任务；已有任务如果没有封面，刷新队列也会自动补封面。
- 保留“更换封面帧”能力：需要人工挑图时仍可生成多张候选帧并切换。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，覆盖敏感词清洗、平台 #话题格式和旧脏数据发送前清洗。
- 已验证：`python -m compileall app`、`python scripts/test_send_center_opencli_queue.py` 通过。

## 2026-06-04 发送中心标题选择器修复

- 修复 opencli 上传视频后填写标题时报错 `selector_ambiguous` 的问题：不再使用 `input[placeholder*='标题'],textarea[placeholder*='标题']` 这种容易匹配多个元素的直接 `fill` 命令。
- 抖音和 B站标题填写改为浏览器脚本：自动寻找当前页面里可见、可编辑的标题输入框，并触发 `input` / `change` 事件，让平台页面能正常感知标题变化。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，确认抖音 / B站发送命令不会再生成模糊标题选择器。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py` 通过。

## 2026-06-04 发送中心本机服务重启确认

- 用户再次遇到旧报错后，检查确认代码里已经没有旧的直接 `fill input[placeholder*='标题'],textarea[placeholder*='标题']` 命令。
- 发现 `127.0.0.1:8002` 仍由旧 Python 后台进程监听，因此浏览器实际调用的还是修复前的运行态代码。
- 已停止旧的 `8002` uvicorn 进程，并用当前项目代码重新启动本机服务；`http://127.0.0.1:8002/publish` 已返回 HTTP 200。
- 后续如果再次看到完全相同的旧 CSS selector 报错，优先确认是否访问的是 Windows 本机 `8002`，并重启后台服务后再测。

## 2026-06-04 发送中心简介填写修复

- 修复抖音填写简介/描述时 opencli 返回 `filled: true` 但 `verified: false` 的问题：不再使用直接 `fill textarea[placeholder*='简介'],textarea[placeholder*='描述'],div[contenteditable='true']` 命令。
- 抖音简介和 B站简介都改为浏览器脚本填写，会优先选择可见、可编辑输入框，并对 `contenteditable` 输入框模拟插入文本，必要时再写入完整文本。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，确认抖音 / B站简介不会再生成容易被严格校验卡住的直接 `fill` 命令。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py` 通过。

## 2026-06-05 抖音话题封面和发布按钮修复

- 抖音作品描述改为只填写发送中心的“正文 / 简介”，不再把平台 `#话题` 直接拼成普通文本塞进描述框。
- 抖音话题改为单独写入编辑器的 `data-mention="#"` 话题块结构，目标是在抖音页面显示为蓝色话题块；写入失败会返回 `douyin_topic_insert_failed`。
- 抖音封面改为等待并选择“AI智能推荐封面”区域第一个可用推荐图；如果 60 秒内没有可选推荐图，会返回 `douyin_ai_cover_not_ready`，不再使用发送中心封面兜底。
- 抖音发布按钮改为脚本精确点击文本等于“发布”的底部按钮，避免 `--name 发布` 同时匹配“高清发布”和“发布”导致 `semantic_ambiguous`。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，覆盖描述/话题分离、AI 推荐封面命令和精确发布按钮命令。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py` 通过。

## 2026-06-05 片段审核播放器固定与操作按钮优化

- 片段审核页右侧预览栏从窄固定栏改为约占页面内容区三分之一，源视频播放器高度同步放大，并继续使用 `object-fit: contain` 避免竖屏视频被裁切。
- 右侧预览栏现在保持 sticky 固定在视口内，向下审核候选片段时播放器、时间提示和审核操作按钮会一直留在当前画面附近。
- “保存修改”“生成切片”“去字幕推送”改成更醒目的审核操作按钮，其中“生成切片”保持主按钮视觉。
- 小屏或窄窗口下仍然上下堆叠；点击列表下方片段的“播放预览”时，如果播放器不在可视区域，会自动平滑滚到播放器。

## 2026-06-05 抖音 opencli 话题封面发送链路修复

- 修复抖音发送到“插入话题”步骤时报 `SyntaxError: Unexpected token ')'`、`gt 不是命令` 的问题：Windows npm 的 `opencli.cmd` 会通过 `%*` 拼接参数，导致 JS 里的 `&`、`<`、`>` 被 `cmd.exe` 当成命令符号。
- opencli 执行入口现在会优先改为 `node ...\node_modules\@jackwener\opencli\dist\src\main.js`，避免复杂浏览器脚本再被 Windows 批处理拆坏。
- 抖音话题脚本改为用 DOM API 创建 `data-mention="#"` 话题节点，不再拼接包含 HTML 实体的大段字符串。
- 抖音 AI 推荐封面选择脚本增强为按“AI智能推荐封面 / 智能推荐封面 / 推荐封面”文案和可见图片兜底查找，减少页面 class 变化导致选封面失败。
- 已补充 `scripts/test_send_center_opencli_queue.py` 测试，覆盖 `.cmd` 改走 Node 入口、话题脚本不再含 `&gt;`、AI 推荐封面文案兜底。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py` 通过。

## 2026-06-05 抖音封面确认与话题简化

- 抖音发送链路暂时取消单独插入蓝色话题块，改为把发送中心的 `#话题` 文本直接追加到作品简介中，例如 `#小S自夸 #美国往事 #陈亦飞爆料 #姐妹情深 #可爱自恋`。
- 发送中心的话题格式化现在支持空格分隔的 `#话题` 字符串，不会再把中文话题拆坏。
- 抖音 AI 推荐封面选择后会继续查找并点击“确定 / 确认 / 应用”按钮，处理“是否确认应用此封面？”弹窗，然后再进入最后的发布按钮步骤。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py` 通过。

## 2026-06-06 抖音发送简介话题和 AI 推荐封面流程修复

- 抖音发送链路彻底移除残留的“蓝色话题块”插入脚本，不再生成 `data-mention="#"` 结构，避免复杂 JS 在 Windows / opencli 命令链路里再次触发 `SyntaxError: Unexpected token ')'` 或 `gt 不是命令`。
- 作品描述现在只走一次填写：把发送中心的“正文 / 简介”和平台 `#话题` 合并后直接写入抖音简介框，支持类似 `#小S自恋名场面 #青春回忆杀 #明星搞笑日常` 的空格分隔话题。
- 抖音 AI 推荐封面流程增强为先选择横封面，再点击“设置竖封面”并选择竖封面，最后点击“完成”后再继续发布。
- 已补充 `scripts/test_send_center_opencli_queue.py`，覆盖用户提供的真实正文 + 话题格式，并确认发送命令里不再包含话题块插入痕迹。
- 已验证：`.venv\Scripts\python.exe -m compileall app`、`.venv\Scripts\python.exe scripts\test_send_center_opencli_queue.py` 通过。

## 2026-06-07 AI 接口配置落地

- 已把本地 `.env` 配置切换为三段式远程接口：音频转写使用火山引擎远程转写，文字稿分析使用 DeepSeek Pro，发送中心发布文案使用 DeepSeek Flash。
- 本次只在 `.env` 写入真实 API Key；`.env` 已被 `.gitignore` 忽略，不会提交到 Git。项目文档只记录配置结果，不记录密钥明文。
- 已备份修改前的 `.env` 到本地 `.env.backup_20260607_122557`，方便需要时恢复。
- 已重启 Windows 本地 `8002` 后台服务，让当前运行页面和后台任务重新读取最新配置。
- 已验证：`python -m compileall app`、`scripts/test_ai_config_service.py`、`scripts/test_volcengine_transcription_provider.py` 均通过；DeepSeek 分析接口和发送中心文案接口均完成远程 JSON 连通性测试。

## 2026-06-07 火山引擎 API Key 修正

- 用户真实任务转写时报错 `Invalid X-Api-Key`，确认原先填入的不是豆包语音控制台生成的新版 API Key。
- 已把 `.env` 中的 `VOLCENGINE_ASR_API_KEY` 替换为新版控制台 API Key，并清空旧版 `VOLCENGINE_ASR_APP_KEY` / `VOLCENGINE_ASR_ACCESS_KEY`。
- 项目当前接入的是火山引擎极速版 `recognize/flash` 接口，因此继续使用 `VOLCENGINE_ASR_RESOURCE_ID=volc.bigasr.auc_turbo`，不切换到标准版 `submit/query` 文档里的 `volc.seedasr.auc`。
- 已重启 Windows 本地 `8002` 后台服务，并用 1 秒静音 mp3 做远程烟测；接口不再返回 401，静音音频返回 0 句属于预期。

## 2026-06-07 转写原文结构与 DeepSeek 分析输入调整

- 新生成的 `transcripts/transcript.md` 不再写入本地拼接的“分钟级转写”，只保留“逐句时间戳原文”作为唯一权威原文。
- 远程 DeepSeek 分析继续整集一次提交，但提交内容改为只取逐句时间戳原文；旧任务文件如果仍有分钟级章节，分析时会自动忽略分钟级重复内容。
- 本地 AI 分析仍保留分段策略，继续按逐句时间戳原文拆成小段后合并候选片段。
- 任务详情页转写预览也优先读取逐句时间戳原文，避免旧文件先显示分钟级聚合文本。

## 2026-06-07 抖音 AI 推荐封面等待选择修复

- 抖音 opencli 发送链路改为等待平台侧“AI智能推荐封面 / 智能推荐封面 / 推荐封面”区域生成图片，最长等待 150 秒。
- 生成完成后会选择该区域最左边第一张真实图片，并尝试点击“设为封面 / 使用封面 / 确定 / 确认 / 应用”，再继续点击发布。
- 已移除旧的“设置横封面 -> 设置竖封面 -> 完成”强制流程，避免页面已选好智能封面但因为找不到“完成”按钮而报 `douyin_cover_finish_not_found`。

## 2026-06-07 抖音发布按钮查找加固

- 用户继续测试时出现 `douyin_publish_button_not_found`，判断为封面确认后发布按钮文案或可见位置与旧脚本不一致。
- 抖音封面脚本现在必须点到“设为封面 / 使用封面 / 确认”等按钮或检测到“封面效果检测通过”才继续；如果只看见图片但没确认，会返回 `douyin_cover_confirm_not_found`。
- 最后发布脚本会滚动到页面底部，等待并识别“发布 / 立即发布 / 确认发布 / 发布作品”，同时避开左侧“高清发布”和右侧“发布助手”。

## 2026-06-07 抖音真实发布确认与简介去重

- 用户反馈作品管理没有已发送内容，确认不能再以“点击发布按钮成功”作为任务已发布依据。
- 抖音发送链路新增发布结果确认步骤：点击发布后必须等到“发布成功 / 已提交审核 / 审核中 / 投稿成功”等平台提示，或在作品管理看到对应标题，才会把本地任务标记为已发布。
- 如果页面出现验证码、登录失效、发布失败、风控等提示，会返回 `douyin_publish_blocked`；如果超时没有成功信号，会返回 `douyin_publish_not_confirmed`。
- 抖音简介填写改为优先定位“作品描述 / 简介 / 描述”附近的编辑框，避开标题框，并在写入后检查重复内容，减少平台简介区出现重影或重复粘贴。

## 2026-07-27 全自动 AI 封面秒数与一键补齐

- 修复全自动流水线创建 Windows Chrome 发布任务时没有生成本地封面的问题。根因是 AI 候选片段结构没有封面秒数，而且全自动发布任务直接把 `cover_file_path` 留空。
- AI 候选片段新增 `cover_time_seconds`，含义为“相对于裁剪后短视频开头的封面秒数”。程序会在所有 Prompt 后追加字段约束，不覆盖用户已保存的自定义 Prompt；缺失、负数或超出片段时长时统一使用片段中点。
- SQLite 的 `clip_candidates` 新增可空 `cover_time_seconds` 字段，旧任务保持空值，不删除或改写历史候选片段。
- 全自动元数据阶段会按 AI 秒数调用 FFmpeg 生成 JPG；同一切片的抖音和 B站任务复用同一张封面。截帧失败会明确中止元数据步骤，不再静默创建缺封面的新发布任务。
- 发送中心新增“一键补充所有封面”，覆盖全部平台中尚未发布且封面为空的任务；按切片分组生成，旧任务使用 50% 中点，已有封面、发布中、已发布、失败和取消记录均不会被覆盖。
- 批量补充完成后页面原地更新封面预览、封面路径和内容完整状态，不刷新页面，因此不会丢失尚未保存的标题或简介。
- 新增 AI 兼容、数据库迁移、全自动双平台复用、批量补充与浏览器交互测试。

## 2026-07-27 跨午夜排期与新任务默认值调整

- 修复发送中心把 `06:00 → 00:00` 判定为非法时段的问题；每日结束早于开始现在表示跨午夜，开始和结束相同表示全天。
- 批量排期的第 1 条严格使用用户选择的北京时间，不再被每日时段静默改写；后续任务按间隔计算，落入禁发空档时顺延到下一个每日开始时间。
- 排期抽屉把“起始时间”改为“第 1 条发布时间”，补充跨午夜说明，并在抽屉内显示预览/保存的处理中、成功和失败信息；参数变化后旧预览立即失效。
- 新任务的单条切片最长默认值由 5 分钟改为 10 分钟，候选片段数量由 5 条改为 12 条；JSON、上传表单、页面和运行时兜底保持一致。
- 现有任务数据没有批量更新；历史任务已保存的 5 分钟、5 条等自定义值继续保留。
- 新增跨午夜、首条精确时间、全天窗口、新建默认值、上传默认值、历史值保护及 10 条浏览器排期测试。
- 已验证专项测试 28 项、完整测试 `333 passed`；Python 编译、两个 JavaScript 文件语法检查和 Ruff 均通过。

## 2026-07-27 取消发送返回内容准备

- 修复排期任务点击“取消”后从发送中心消失的问题。根因是普通取消被写成终止状态 `CANCELLED`，而“内容准备”只展示仍可编辑和排期的任务。
- 普通“取消发送”现在把任务恢复为 `WAITING`，清空已选排期、调度占用和本次执行错误，但保留视频、标题、简介、话题、封面、平台和账号等准备内容。
- 排期计划中的按钮改为“取消发送并返回准备”；操作成功后页面会自动切回“内容准备”、展开对应任务组并滚动到返回的任务，不需要刷新页面。
- “移出内容准备”仍是用户主动隐藏记录，“跳过任务”仍是终止当前任务；二者继续使用 `CANCELLED`，不会被普通取消逻辑误恢复。
- 兼容历史数据：数据库初始化时，只把旧版错误标记为“用户取消任务”的最后一条记录安全恢复到 `WAITING`；若同一切片和平台已经有活跃替代任务，则不会制造重复任务。
- 新增状态机、历史数据恢复和浏览器交互回归测试；专项测试 `19 passed`、完整测试 `336 passed`，Python 编译、两个 JavaScript 文件语法检查、Ruff 和差异检查均通过。

## 2026-08-02 E 盘统一视频存储与任务永久删除

- 新增 `UPLOAD_TEMP_DIR`，并在应用启动时把当前进程的 `TEMP`、`TMP` 和 Python 临时目录指向 E 盘；大于 1 MB 的浏览器上传不再先写入 C 盘系统临时目录。
- 手动发布包默认目录从项目 `outputs/publish_packages` 调整到 `E:\直播间切片工作流存储\_发布包`；任务原片、音频、切片、字幕和封面继续统一使用 `TASKS_DIR`。
- 任务列表“移入回收站”改为“永久删除”：只删除系统托管目录，外部 NAS / E 盘原片保留，数据库历史隐藏保留；运行中的处理和发布任务禁止删除。
- 新增 `scripts/purge_deleted_task_media.py`，默认只预览；`--apply` 会先创建 SQLite 元数据备份，再清理已隐藏任务的 E 盘目录、发布包和精确匹配的旧版 C 盘任务目录。
- 新增大文件 multipart 临时目录、失败上传回滚、外部原片保护、路径越界、运行中拦截、删除失败回滚、幂等删除和旧任务清理测试。
- 已对真实数据先预演再执行清理：15 条已隐藏任务共删除 16 个托管目录，释放 `6,213,311,934` 字节（约 6.21 GB）；4 条有效任务目录清理前后均完整，外部测试原片保留，清理后再次预演为 0 个残留目录。
- 清理前 SQLite 元数据备份完整性检查为 `ok`；最终完整测试 `395 passed`，Python 编译、Ruff、JavaScript 语法和差异检查均通过。

## 2026-08-02 任务详情进度局部自动刷新

- 新增 `GET /api/tasks/{task_id}/live-status` 轻量接口，统一返回任务状态、进度、10 步时间线、日志、候选/输出数量和可用操作，不执行 FFprobe。
- 全自动任务详情页打开后立即读取一次状态，处理中每 3 秒局部更新状态卡、顶部状态、基础计数和右侧运行日志；页面切回前台时会立即补一次更新。
- 完成或失败后停止轮询，并原地显示发送中心、片段检查、重试或继续入口；重试/继续全自动流程也不再触发整页刷新。
- 网络短暂异常时保留当前页面数据并继续重试，不会清空用户正在编辑的 AI Prompt 或改变滚动位置。
- 新增处理中、失败、完成、404 和前端无整页刷新测试；专项测试 `21 passed`、完整测试 `407 passed`，JavaScript 与 Python 语法检查通过。浏览器实测从 AI 分析中切换到失败状态时，状态卡、日志和重试按钮在 3 秒内更新，未保存输入保持不变。

## 2026-08-11 关机后发布恢复与排期重排

- 检查 Docker、Windows Worker、工作台日志和正式 SQLite 数据库；关机前最后一条确认成功发布于 2026-08-08 16:00:20（北京时间）。
- Docker Desktop 恢复后，`restart: unless-stopped` 自动拉起工作台并处理了 3 条过期任务；3 条均取得抖音“发布成功”证据，成功时间分别为 2026-08-11 01:48:32、01:48:50、01:49:08，未重复加入新排期。
- 在继续领取第 4 条前停止容器，核对 Worker execution journal 后把第 3 条成功结果回写正式数据库，再重新启动工作台。
- 发现 `idx_tasks_status_created` 索引漏 1 行；保留原始备份后只重建该索引，未删除任务或发布记录，最终 `PRAGMA integrity_check` 为 `ok`。
- 剩余 14 条抖音任务从 2026-08-11 07:00 起按北京时间每 3 小时发送，每日时段为 07:00～22:00；8 月 11 日 6 条、8 月 12 日 6 条、8 月 13 日 2 条，最后一条为 2026-08-13 10:00。
- 重排前备份：`backups/niuma-studio-before-schedule-date-correction-20260811-015125.sqlite3`；备份与正式数据库完整性检查均通过。
- 发送中心页面已验证：调度器正常、Windows Worker 正常、已排期 14、发送中 0，14 条均显示“发送条件已满足”。

## 2026-08-16 Windows 启停脚本编码兼容修复

- 定位到 `scripts/start.ps1` 和 `scripts/stop.ps1` 在未提交修改中丢失 UTF-8 BOM，导致桌面批处理调用 Windows PowerShell 5.1 时把中文误读为乱码，并产生字符串、数组索引和大括号解析错误。
- 恢复两个入口脚本的 UTF-8 BOM，保留现有启动 Worker、启动 Docker、连接检查和停止流程，不修改脚本参数、Docker 配置、数据库或存储数据。
- 增加入口脚本 BOM 回归测试，弥补 PowerShell 7 语法检查无法发现 Windows PowerShell 5.1 编码误读的问题。
- 项目正式访问地址仍为 `http://127.0.0.1:8001/`；旧地址 `http://localhost:6866/` 不再提供服务。
- Windows PowerShell 5.1 解析检查为 0 个错误，专项测试 `5 passed`；修复后的停止脚本正常退出且未删除正式数据。默认联网构建因 Docker Hub 认证地址暂时不可达而失败，随后使用本地 `niuma-studio:latest` 镜像执行 `start.ps1 -NoBuild` 成功，容器为 `healthy`、首页 HTTP 200、Worker 连接正常。
## 2026-08-21 Windows 原生日常模式与 Docker 回退

- 新增 `scripts/run_native.ps1`、`scripts/start_native.ps1` 和 `scripts/stop_native.ps1`：FastAPI 默认使用 `127.0.0.1:8001`，Windows 发布 Worker 默认使用 `127.0.0.1:8765`，SQLite 与 E 盘任务目录继续复用原数据。
- 原生启动器支持自定义端口、存储目录、数据目录和数据库路径；状态文件记录 PID、启动时间、项目根和端口，停止前做多重身份校验，避免误关其他进程。
- 修复 PowerShell 5.1/7 的目录创建参数、ISO UTC 时间 roundtrip 解析、Worker 子脚本 `$LASTEXITCODE` 遗留值等实机问题，并增加静态回归测试。
- 切换前停止 Docker 写入并生成 `data/backups/workflow-pre-native-cutover-20260821-150620.sqlite3`，备份 `PRAGMA integrity_check=ok`，包含 16 张表；未备份或输出 `.env`。
- 已禁用但未删除计划任务 `NiuMa Studio Docker Watcher`，避免登录时自动重启 Docker Worker；Docker Compose 配置和镜像保留为回退入口，未执行 volume/prune。
- 隔离端口 `18001` 使用在线 SQLite 快照完成 `/health`、首页、任务、片段和发送中心冒烟，均为 HTTP 200；正式原生 `8001` 的相同页面和调度器健康检查通过，Windows Worker 状态正常。
- 当前 Web 进程由 Alter 以 `Niuma-Studio` 托管并启用崩溃自动重启；发布 Worker 仍是本项目的 Windows 原生进程。
- 专项测试 `11 passed`，Windows PowerShell 5.1 与 PowerShell 7 三个脚本均为 0 个解析错误；未触发任何真实投稿。
