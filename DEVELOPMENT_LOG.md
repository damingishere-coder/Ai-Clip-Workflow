# Development Log

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
- 远程 AI 默认配置改为 DeepSeek OpenAI-compatible Chat Completions：`https://api.deepseek.com`、`deepseek-v4-pro`、`chat_completions`，避免继续用 Responses 协议和 `gpt-5.5` 模型名请求 DeepSeek。
- 系统状态页 AI 配置弹窗新增远程协议和 DeepSeek 模型选择；保存配置时如果检测到 DeepSeek，会自动使用 `chat_completions` 并让 Review Model 跟随 DeepSeek 模型。
