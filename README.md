# 牛马片场 / NiuMa Studio

牛马片场是一个运行在 Windows 本地的 AI 高光生产后台，用来把直播录像、综艺访谈、长视频素材整理成可转写、可分析、可审核、可切割、可加字幕、可进入发送中心的短视频生产任务。

当前版本：`1.5.0`。

v1.5.0 将抖音和 B站的立即发送、定时发送统一到 `PublishScheduler → Registry → LocalBrowserPublisher → Windows Worker → 平台 Publisher`。FastAPI 或 Docker 负责排期，Windows Worker 使用系统 Chrome 的独立账号目录执行真实投稿。只有读取到平台作品 ID、稿件 ID 或明确成功链接才进入 `PUBLISHED`；登录失效、验证码、风控和结果不确定进入 `NEED_REVIEW`，不会自动重复上传。

## 定时发送快速说明

- `platform` 只表示目标平台：`douyin` / `bilibili`。
- `publish_mode` 表示执行方式：默认 `local_browser`；`manual_export` 只能显式选择；旧 `opencli_publish` 只有设置 `PUBLISH_ENABLE_OPENCLI_FALLBACK=true` 才能执行。
- `local_browser` 会按 `platform` 选择 `DouyinPublisher` 或 `BilibiliPublisher`，失败时绝不静默回退到 `manual_export`。
- 浏览器取得平台成功证据才进入 `PUBLISHED`；本地发布包导出成功进入 `EXPORTED`，两者含义不同。
- 前端固定显示北京时间；无时区输入按 `Asia/Shanghai` 解释，数据库统一保存带 `+00:00` 的 UTC ISO 8601。
- “立即发送”只写入当前时间并设为 `SCHEDULED`；到点后与未来排期使用同一个 Scheduler 和 Publisher。
- 调度器健康状态：`GET /api/publish/scheduler/health`。
- 浏览器账号使用 `data/browser_profiles/{platform}/{account_id}` 独立目录；Cookie、storage state、截图和 Worker 日志均被 Git 忽略。

### 单条真实灰度发布

1. 在项目目录运行 `./scripts/start_publish_worker.ps1`，启动带 Token 的 Windows Worker。
2. 打开 `/publish` 的“内容准备 → 账号管理”，分别新增抖音和 B站账号，再点击“打开登录窗口”。
3. 在系统 Chrome 独立窗口内完成二维码、短信或平台要求的人工验证，然后回到页面点击“检查登录”。
4. 只选择一条用户确认可发布的短测试视频，核对标题、正文、话题、封面、平台账号和可见范围。
5. 点击“立即发送”后，任务先进入 `SCHEDULED`，再由 Scheduler 领取为 `PUBLISHING`；平台确认成功后进入 `PUBLISHED`。
6. 若出现登录、验证码、风控或结果不确定，任务应进入 `NEED_REVIEW`，先打开平台创作者中心核对，不能直接重试。

### 排期 API

预览与保存使用同一个请求体：

```json
{
  "job_ids": ["job-a", "job-b"],
  "action": "apply",
  "start_at_local": "2026-07-12T09:00",
  "timezone": "Asia/Shanghai",
  "interval_minutes": 180,
  "daily_start_time": "09:00",
  "daily_end_time": "21:00",
  "confirmed_schedule": []
}
```

- 先调用 `POST /api/publish/schedules/preview`，读取每条任务的 `scheduled_at_local`、`scheduled_at_local_display` 和 `scheduled_at_utc`。
- 用户确认后，将预览返回的精确时间列表作为 `confirmed_schedule` 调用 `PATCH /api/publish/jobs/schedule-batch`，后端逐条校验后写库。
- 清除排期时提交 `action=clear`；普通任务回到 `WAITING`，`FAILED` 保持失败，`NEED_REVIEW` 保持复核状态。

## 当前状态

- 后端：FastAPI 可启动，当前 API 版本为 `1.5.0`。
- 前端：HTML + CSS + JavaScript + Jinja2 后台页面，已完成 Apple 风格全页面美化。
- 数据库：SQLite，保存任务、候选片段、输出片段、字幕任务、发送任务和 AI 配置等信息。
- 视频处理：已接入 FFmpeg / FFprobe，用于音频提取、切片、封面帧和字幕成片。
- 转写：支持火山引擎远程转写和本地 faster-whisper。
- AI 分析：支持远程 OpenAI-compatible / DeepSeek 和本地 Ollama；长视频会按小段分析再合并候选片段。
- 发送中心：分为内容准备、排期计划、执行记录；抖音 / B站真实发布由统一 Scheduler 和 Windows Chrome Worker 执行。
- 安全边界：不会绕过验证码、登录失效、平台风控或人工确认；不会保存账号密码、cookie 或真实 API Key。
- 配置安全：真实 `.env` 已被 Git 忽略，不会提交真实 API Key。
- 品牌说明：当前页面主名为“牛马片场”，英文代号为 `NiuMa Studio`，Docker 技术名为 `niuma-studio`。

## 新手启动方式

第一次使用请先阅读：

```text
docs/PROJECT_GUIDE.md
```

里面按”准备环境、启动项目、打开页面、测试功能”的顺序写好了。

---

## 本地开发启动

如果你要在本地直接运行（不通过 Docker），按以下步骤操作：

### 1. 准备环境

- 安装 Python 3.12 或更高版本
- 安装 FFmpeg（视频处理必需）

### 2. 创建虚拟环境并安装依赖

打开终端（PowerShell），进入项目根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. 配置环境变量

```powershell
copy .env.example .env
```

然后打开 `.env`，填写你自己的 API Key 和本地路径。

### 4. 启动开发服务器

```powershell
uvicorn app.main:app --reload --port 8001
```

### 5. 打开页面

浏览器访问：

```text
http://127.0.0.1:8001
```

---

## v1.5.0 统一真实发布

- 应用启动时会自动启动 `PublishScheduler`，默认每 5 秒扫描一次 `publish_jobs`。
- 默认发布方式是 `local_browser`；Docker 中的 FastAPI 通过 `PUBLISH_WORKER_URL=http://host.docker.internal:8765` 调用 Windows Worker。
- 一键启动 Worker：`./scripts/start_publish_worker.ps1`；兼容脚本 `./scripts/start_docker_opencli.ps1` 现在也会先启动同一个 Worker，再启动 Docker。
- 可选的 `manual_export` 会把发布包导出到 `outputs/publish_packages/{task_id}/{clip_id}/`；发布包包含 `clip.mp4`、`title.txt`、`caption.txt`、`hashtags.txt`、`cover_text.txt`、`publish_plan.json` 和 `metadata.json`，成功状态为 `EXPORTED`。
- 手动执行一次扫描：

```powershell
.\.venv\Scripts\python.exe -m app.publish_scheduler run-once
```

- 持续运行独立调度器：

```powershell
.\.venv\Scripts\python.exe -m app.publish_scheduler run
```

- `NEED_REVIEW` 表示登录、验证、风控或平台结果不确定；必须先核对平台结果。确认未发布后标记失败，再创建新的重试任务。
- 当前仍然跳过加字幕、烧录字幕和字幕叠加，自动发布使用原片切割结果。
- 平台页面可能改版，真实灰度发布前必须用单条、低风险测试素材人工确认；自动测试全部使用 Mock，不会打开真实浏览器。

---

## 运行测试

项目使用 pytest 进行测试。在终端中执行：

```powershell
# 确保虚拟环境已激活
.\.venv\Scripts\Activate.ps1

# 运行全部测试
pytest -v

# 只运行某个测试文件
pytest -v tests/test_job_queue.py

# 运行测试并显示覆盖率（需要先 pip install pytest-cov）
pytest --cov=app --cov-report=term-missing
```

> 测试环境使用独立的 SQLite 数据库，不会影响你的真实数据。

---

## Docker 启动

推荐启动方式：Docker 一键启动。

```powershell
docker compose up --build
```

启动后在浏览器打开：

```text
http://127.0.0.1:8001
```

停止项目：

```powershell
docker compose down
```

Docker 启动说明：
- 容器内 Python 3.12，已预装 FFmpeg
- Windows Worker 必须在宿主机运行，容器不会保存 Chrome 登录态
- `.env` 文件会被自动加载（如果存在）
- 存储目录 `E:\直播间切片工作流存储` 会自动挂载到容器内
- 代码目录和 prompts 目录以 volume 方式挂载，支持热更新

---

## 安全注意事项

### API Key 保护

- **真实 API Key 只能放在 `.env` 文件里**，绝对不能硬编码在代码中
- `.env` 已写入 `.gitignore`，不会提交到 Git
- 提交代码前请确认没有无意中提交 `.env` 文件：

  ```powershell
  git status
  ```

- 仓库中只保留 `.env.example` 模板，方便以后重新配置

### Git 提交安全

- 每次提交前，确保没有包含以下内容：
  - 真实的 API Key / Token / Secret
  - `.env` 文件
  - 数据库文件（`*.sqlite3`、`*.db`）
  - 视频 / 音频文件
  - 日志文件

### 运行环境安全

- 本项目设计在**本地**运行，不要直接暴露到公网
- `LOCAL_ADMIN_TOKEN` 用于管理接口鉴权，生产环境请使用随机长字符串
- 定期更新依赖：`pip install --upgrade -r requirements.txt`

详细说明见：

```text
docs/SECURITY_AND_GIT.md
```

## 文档入口

```text
docs/PROJECT_GUIDE.md          新手项目总览与启动说明
docs/SECURITY_AND_GIT.md       API Key、.env、Git 提交安全说明
docs/ARCHITECTURE.md           系统架构
docs/TASK_FLOW.md              任务处理流程
docs/DATABASE_SCHEMA.md        数据库表结构
docs/AI_ANALYSIS.md            AI 分析配置与流程
docs/CLIP_REVIEW.md            候选片段审核说明
docs/VIDEO_CUTTING.md          自动切割说明
docs/UI_REFERENCE.md           UI 页面与设计参考
VERSION                        当前版本号
DEVELOPMENT_LOG.md             开发记录
NEXT_STEPS.md                  下一步计划
```

## 目录结构

```text
app/                 FastAPI 主应用
app/core/            配置读取
app/db/              SQLite 数据库连接
app/models/          数据模型
app/routers/         页面路由与 API 路由
app/services/        任务、存储、转写、AI、切割等服务
app/templates/       Jinja2 页面模板
app/static/          CSS 与 JavaScript
data/                本地数据库目录，真实数据不提交
tasks/               任务产物目录，真实视频和切片不提交
Dockerfile           Docker 镜像构建文件
docker-compose.yml   Docker 一键启动配置
docs/                项目文档
prompts/             AI 分析 Prompt
scripts/             本地测试脚本
```


