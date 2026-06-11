# 牛马片场 / NiuMa Studio

牛马片场是一个运行在 Windows 本地的 AI 高光生产后台，用来把直播录像、综艺访谈、长视频素材整理成可转写、可分析、可审核、可切割、可加字幕、可进入发送中心的短视频生产任务。

当前版本：`1.2.0`。

v1.2 已经实现 MVP 全部流程：新建任务、上传视频、提取音频、远程 / 本地转写、AI 分析候选片段、人工检查、自动切割、自动加字幕、候选封面帧、发送中心队列，以及通过 opencli 辅助打开抖音 / B站投稿页。

## 当前状态

- 后端：FastAPI 可启动，当前 API 版本为 `1.2.0`。
- 前端：HTML + CSS + JavaScript + Jinja2 后台页面，已完成 Apple 风格全页面美化。
- 数据库：SQLite，保存任务、候选片段、输出片段、字幕任务、发送任务和 AI 配置等信息。
- 视频处理：已接入 FFmpeg / FFprobe，用于音频提取、切片、封面帧和字幕成片。
- 转写：支持火山引擎远程转写和本地 faster-whisper。
- AI 分析：支持远程 OpenAI-compatible / DeepSeek 和本地 Ollama；长视频会按小段分析再合并候选片段。
- 发送中心：支持生成抖音 / B站待发送队列、AI 标题 / 简介 / 话题、候选封面帧，并通过 opencli 调用已登录 Chrome 辅助投稿。
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


