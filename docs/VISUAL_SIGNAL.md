# v2.5 Visual Signal 实施记录

## 当前阶段：候选采样与图像能力验证

正式运行版本仍为 v2.4.0。`frame_sampling_service.py` 新增独立采样服务，尚未接入 Analyzer、任务 API、评分或后台 Job，不新增数据库迁移。生产视觉能力保持关闭；后续 PR 才接 VisualProvider、checkpoint/证据及 Judge/UI/清理。

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
