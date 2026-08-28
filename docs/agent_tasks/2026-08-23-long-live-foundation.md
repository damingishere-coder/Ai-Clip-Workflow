# PR 1 执行任务：长直播基础设施

## 背景

NiuMa Studio 现有链路能按固定窗口处理长素材，但新建任务默认写死综艺模式，已有文件入口未接入页面，转写分块结果不能恢复，长任务依赖 Web 进程内后台任务，也缺少媒体与磁盘预检。

## 目标

在 `feature/long-live-foundation` 分支完成可独立验收的长直播基础层，为后续长直播选片、字幕编辑器和字幕自动流水线提供稳定数据与任务基础。

## 允许修改范围

- `app/models/`、`app/routers/`、`app/services/`、`app/db/`、`app/templates/`、`app/static/`
- `tests/` 中与任务创建、媒体预检、Job、转写有关的测试
- `docs/`、`DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、依赖声明

## 禁止修改范围

- 不批量修改已有任务的 `selection_profile`
- 不删除或移动用户视频、数据库、日志和配置
- 不改变现有 AI 选片算法和字幕工作台业务逻辑
- 不覆盖明确配置的 faster-whisper CPU/CUDA 选项
- 不提交 `.env`、密钥、Cookie、媒体、数据库、日志、缓存或构建产物

## 已确定实现要求

1. 新建任务必须明确选择 `general`、`variety_comedy` 或 `long_live_talk`；页面、上传 API 和 JSON API 均不得静默兜底。
2. 长直播专用参数为每小时密度 1～10（默认 4）和总上限 1～50（默认 30），仅长直播模式使用。
3. 页面同时支持浏览器上传和受根目录保护的本地/NAS已有文件；浏览器上传仍默认限制 4 GB。
4. 创建任务前用 FFprobe 检查音视频流、时长、编码、分辨率、帧率与可读性，并按源文件、PCM、预计输出和安全余量检查可用空间；超过 6 小时只警告。
5. `workflow_jobs` 增加 lease、heartbeat、尝试次数、取消标志和 checkpoint；单 worker 接管过期任务。
6. `transcription_runs`、`transcription_chunks` 持久化每个块的结果、校验与错误；源文件指纹改变时创建新 run，不复用旧块。
7. 统一保存毫秒级段落、词时间戳和置信度；`transcript.md` 只保留兼容导出。
8. FFmpeg/ASR 支持进度、无进展超时和进程树终止；保留显式 CPU 配置，并提供可验证的 `auto` CUDA 选择。

## 验收标准

- 页面没有隐藏选片默认值；三种模式均可创建；缺失或非法模式返回中文错误。
- 已有文件可浏览并创建任务，且不会复制或删除外部唯一原片。
- 无音轨、不可探测、空间不足时拒绝创建；超过 6 小时显示非阻断提示。
- 第 N 个转写块失败后重试只处理缺失块；指纹改变后不复用旧 checkpoint。
- queued/running Job 在进程重启后可重新接管；取消和重试接口有持久状态。
- 历史任务行为与数据不变。

## 测试命令

```powershell
python -m pytest tests/test_selection_profile_default.py tests/test_media_storage_lifecycle.py tests/test_job_queue.py tests/test_split_services.py -q
python -m pytest tests/ -q
```

## 返回格式

主代理验收时报告：实际修改文件、迁移与兼容策略、测试命令和结果、diff/敏感信息检查、提交哈希、推送分支与 PR 链接。
