# v2.5 Visual Signal 实施记录

## 当前阶段：候选采样与图像能力验证

正式运行版本仍为 v2.4.0。`frame_sampling_service.py` 新增独立采样服务，尚未接入 Analyzer、任务 API、评分或后台 Job，不新增数据库迁移。生产视觉能力保持关闭；后续 PR 才接 VisualProvider、checkpoint/证据及 Judge/UI/清理。

## PR2：可选视觉请求与恢复证据（开发中）

PR #100 已通过最终 Linux、Windows、Docker CI，合并 `d7a29b7`。随后从主干建立 `codex/v2.5-visual-evidence`。本 PR 增加 `VisualProvider` 契约、现有 Codex 的显式图像方法，以及候选证据服务；尚未接入 Analyzer/API，不开放生产视觉选项。上节的“无数据库迁移”仅指 PR1，本 PR 新增增量迁移。

- `CodexCliProvider.generate_visual_json` 沿用当前可执行文件、模型、登录配置和只读临时目录，保留原文字接口与 Prompt 渲染。1–8 张 JPEG 先核对 SHA/单帧 2 MiB 上限，再将校验的字节复制为私有临时附件；不能从素材中的指令读取其他文件。视觉调用串行，等待执行槽也计入最多 90 秒时限，超时终止本次进程树。事件流缺失或出现工具事件即判失败；只容忍已实测的 CLI 技能目录描述缩短提示，仍要求完成事件与有效结果。不自动重试、不自动更换 Provider。
- 调用前使用进程参数临时禁用 shell/unified exec、插件/apps、浏览器/电脑、额外图片读取、搜索和代理等入口，再枚举有效 MCP、逐项禁用并二次核验；隔离失败不调用模型。这些开关仅作用于该子进程，不写全局配置、不更换模型/登录。实测空 `mcp_servers={}` 会继续合并旧配置，不能当作清空；CLI 的 `-c` 路径也不支持 TOML quoted key，使用已验证的安全名称，否则降级。相关官方定义见 [配置参考](https://learn.chatgpt.com/docs/config-file/config-reference#configtoml)。
- `VisualResponse` 是严格结构：观察类型、说明、置信等级与附件索引，必须含限制说明。引用不存在/重复附件、未知字段、过长证据或错误 JSON 不能成为成功缓存。离散帧不能证明完整心理状态、身份或视频节奏，不提供自动发布判断。
- `prepare_candidate_visual` 在有效 Job 租约的事务中冻结采样、Prompt/schema、Provider/模型指纹和相对缓存目录。随机目录/文件名不进入模型请求身份；帧内容、实际 PTS、顺序、采样规则和提示词进入身份。恢复先查 `get_candidate_visual`，使用原附件记录，不能重新抽帧后替换旧请求。
- `analyze_candidate_visual` 使用 `_ai_analysis_units_v1` 的独立 `optional-visual-v1` namespace。`input_fingerprint` 是整轮有效策略/原片/文字候选的冻结指纹，各候选共享，`candidate_key` 是该轮稳定候选标识。请求开始前证据表记录 pending；成功后记录经过校验的结构化证据/响应哈希。结果不确定、缓存/输入损坏、模型变化、调用后 checkpoint 丢失均降级并保留记录，不重新计费。
- `call_status` 记录调用状态，`status` 记录可选证据是否可用；部分抽帧成功且模型成功是 partial。不可用状态不会改变任务、文字单元数、coverage、analysis_incomplete 或 quality_degraded。无 lease 不调用，lease 失效原样传播，旧进程不能继续写入或提交。
- 图片预校验失败、等待执行槽或工具隔离失败均记录为已知未发模型的 retryable_failed；默认不自动重试，只有服务的显式 `retry_unbilled=True` 才续试同一冻结请求。它不能重试 uncertain，也不能更换旧请求。证据已完成但 checkpoint 变成可重试状态属于矛盾证据，拒绝再次调用。未知结果保留原记录。
- `candidate_visual_evidence` 只保存可查询的证据和缓存引用，不复制候选事实或创建后台队列。恢复执行事实仍在现有 AI unit checkpoint，最终通过现有 AI 原子提交事务关联 Run；该原子关联函数目前尚未被生产调用。新增表的备份/回滚/幂等/并发迁移沿用现有执行器。

生产启用前还必须完成：总轮次预算、Analyzer 插入点、Run 原子关联实际调用、证据 UI、七天/一天清理和活动/不确定/人工固定引用保护。当前不启动自动清理、不修改正式数据库，不把基础模块视为视觉分析上线。

### PR2 独立实际链路验收

2026-09-14，在隔离数据库/存储中，用程序生成的两张测试图制作两秒视频并加静音音轨，复用任务媒体预检、候选采样、现有 Codex / gpt-6-astra、严格 VisualResponse 和 Job checkpoint。仅发起一次视觉请求，44.875 秒完成；两帧 OCR `A27F` / `B63C` 与红圆/蓝矩形变化均正确，附件索引正确，包含离散帧限制。随后禁止 Provider 再调用，成功复用原 checkpoint 与结果哈希。隔离数据库 integrity=ok、外键异常=0，无真实任务或发布操作。

证据位于本机忽略目录 `data/acceptance/v25-evidence-probe/`。响应 SHA-256：`3a27bdad0749b5dcbc6f50b55373d51acc14e55e4476512fa7e02efcc9534689`。初次准备测试素材因无音轨被现有媒体预检拒绝，尚未调用模型；补静音音轨后才进行了唯一一次实际调用。该验收证明新实现的图像/持久化/恢复技术链路，不能替代真实节目质量验收。

随后针对新增的调用前工具隔离，另建 `data/acceptance/v25-evidence-probe-restricted/` 做独立验证：本机有效 MCP 三项均被临时禁用，23 条进程参数覆盖、不写全局配置；两帧观察、OCR、严格结构与 checkpoint 复用再次通过，37.485 秒、一个模型调用。响应 SHA-256：`45c6ba4343dd103575520231eab491ead829a0387675308bf431ddb4222cfcc9`。两次技术验证均有各自冻结请求与已完成记录，没有重新发送不确定请求。

### 可复用能力与时间约定

- 读取原片复用 `storage_service.validate_source_video_path` 允许根目录检查；写入仅限当前任务 `analysis/visual/<随机运行标识>`，解析路径后拒绝符号链接逃逸。
- 复用 `ensure_ffmpeg_available` 和受控进程组/进程树终止语义。等比例缩小至最长边不超过 960，不放大、不使用发布封面的 16:9 裁切，不修改发布服务。
- `plan_candidate_frames` 接收原片绝对秒数。现有 `key_moment_time` 为绝对时间；`cover_time_seconds` 是相对片段开头的偏移，调用方不能直接当成原片时间。
- 当前候选没有可靠的 hook/reaction 时间字段，音频代理信号也不能给出精确反应时刻。缺少时间时记录 `interval_fallback`；关键时刻后两秒记录 `key_plus_2s`，不是已检测到的反应。
- 默认六帧、硬上限八帧；优先明确关键时刻与候选边界，补少量区间点。输入 seek 与输入时长约束限制解码范围；保留请求时间和 FFmpeg 首帧实际 PTS，实际帧越出候选即丢弃。低帧率素材末尾可能没有可取帧，失败明确记录。

### 预算、证据与失败

一次分析复用一个 `CandidateFrameSampler`：最多 20 个候选、64 MiB 图片、600 秒采样预算，单个 FFmpeg 调用不超过现有封面超时和剩余总预算。原片完整 SHA-256 每轮计算一次，不能用现有头尾摘要冒充完整文件哈希；计算耗时计入预算，文件大小/mtime/ctime 在读取前后核对。原片读取本身复用现有完整哈希函数，不能在单次阻塞文件读取中立刻取消；后续 Job 接入需要保留取消与 lease 检查边界。

每个候选保存采样计划、忽略的非法时刻、实际帧时间、文件 SHA-256/大小、跳过原因及 `completed / partial / unavailable`。超时终止本次进程树；不能确认进程终止时沿用已有异常处理，不继续悄悄启动更多进程。黑帧、完全重复图片、错误 JPEG、越界帧、超预算与失败图片立即清理，原片变化会清空该候选已取得的图片。

当前只去除完全相同图片，不用过强的缩略图相似度阈值丢掉细微表情或字幕变化；也没有扫描全视频的镜头检测。候选范围内的场景变化辅助可在后续证据需求明确后补充。成功缓存的七天/失败一天保留、活动 Job 保护与人工固定功能将在清理 PR 接入，在此之前采样服务没有生产调用入口。

文字 coverage、`analysis_incomplete`、`quality_degraded`、康熙评分和 hard gates 本 PR 均不改变。后续可选视觉失败不得伪装为文字失败；文字召回和 Judge 等必需阶段依然 fail closed。

## 2026-09-14 图像接口技术验收

官方 [Codex 命令说明](https://learn.chatgpt.com/docs/developer-commands?surface=cli) 和本机 `codex exec --help` 已核对；本机支持 `--image` 与 `--output-schema`。API 图像输入的通用限制见 [官方 Images and vision](https://developers.openai.com/api/docs/guides/images-vision)，不把 API 模态说明当作本机 CLI 已实测的证据。

一次隔离实际调用沿用现有 Codex Provider、`gpt-6-astra` 与登录配置，使用两张程序生成的图片，禁止工具/其他文件访问。结果在 30.88 秒内返回：两图顺序、四位可见代码、形状、颜色及请求指定的时间戳映射全部正确，JSON 与目标结构/值一致，工具调用为 0。时间戳来自调用方的映射，不是模型从图片自行检测出的时间。

实际命令沿用 `exec -C <隔离目录> --sandbox read-only --skip-git-repo-check --ephemeral --model gpt-6-astra --json --output-schema <schema> --output-last-message <result> --image <图一> --image <图二> -`，通过 stdin 传递限定提示词。未修改 Provider、模型、认证或全局配置；只有一次调用，不自动重试。CLI 模型列表刷新曾记录 timeout warning，但本次图像请求正常完成，该警告与成功响应分别留证。

本机 `data/acceptance/v25-image-probe/` 保存图片、Prompt/schema/响应哈希、事件流和状态。响应 SHA-256 为 `379660261cfefaaeb1f4423e13b55113028349dba4845cae2d5d1313fdaf64bb`。这只证明接口与简单视觉事实可用，不证明节目表情理解、OCR 全面准确或选片质量改善。

另在隔离存储中对既有康熙候选做本地采样：六帧有效、无跳过、实际时间均位于候选，完整原片 SHA 与 v2.4 清单相同，AI 调用为 0。图片仅在本机 `data/acceptance/v25-real-frame-check/`，没有修改生产任务或提交媒体。

## 接下来的独立交付

本 PR 工程验证：1104 项完整回归全部通过（含 26 项采样与浏览器测试），Ruff 和 Python 编译检查通过。独立真实图像调用与真实候选抽帧的范围如上，不将工程通过表述为人工选片质量通过。

1. VisualProvider 与图像 checkpoint：复用受控 CLI，校验输出后才缓存；记录不确定失败，禁止自动重发。
2. SQLite 可选证据与分析 Run 关联：保存 Provider/模型/请求与响应哈希、失败和清理状态，不另建候选事实库。
3. 各 Profile 的可选 Judge 插入点、证据 UI、总预算及缓存清理；关闭视觉保持原路径，失败正常降级，验收后再进入 v2.5.5。
