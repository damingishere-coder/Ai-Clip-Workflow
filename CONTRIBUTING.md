# Contributing to NiuMa Studio

感谢你愿意参与牛马片场。项目欢迎 Bug 修复、文档改进、测试补充、界面优化和平台兼容性维护。

## 开始之前

请先确认你的改动符合以下边界：

- 不绕过二维码、短信、验证码、滑块、登录失效或平台风控。
- 不提交真实 API Key、Cookie、浏览器 Profile、账号密码、数据库、视频或运行日志。
- 不让自动化测试连接真实平台账号或触发真实投稿。
- 不默认把本地服务暴露到公网。
- 涉及删除文件时，必须继续保护外部唯一原片和运行中的任务。

## 提交 Issue

### Bug

请尽量提供：

- Windows 版本、Python 版本和启动方式（Docker / 本地）。
- 牛马片场版本或对应 commit。
- 问题发生在哪个页面或流程步骤。
- 预期结果与实际结果。
- 已脱敏的错误信息和复现步骤。

不要上传包含 API Key、Cookie、手机号、账号信息、本地绝对路径或私人视频内容的截图和日志。

### 功能建议

请说明：

- 你正在解决什么实际问题。
- 当前流程为什么不够用。
- 期望的最小可用行为。
- 是否涉及真实平台账号、发布自动化或外部 API。

## 本地开发

推荐环境：

- Windows 10 / 11
- Python 3.12+
- FFmpeg / FFprobe
- Docker Desktop（可选）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip check
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8001
```

浏览器访问：

```text
http://127.0.0.1:8001
```

依赖升级规则见 [docs/DEPENDENCY_POLICY.md](docs/DEPENDENCY_POLICY.md)。

## 测试

提交前至少运行与你改动相关的测试。能够运行完整检查时，请执行：

```powershell
python -m compileall app scripts
ruff check app tests scripts/seed_demo_data.py
pytest -v
```

涉及启动与 Docker 的改动，还应执行：

```powershell
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.demo.yml config --quiet
.\scripts\acceptance.ps1
```

涉及前端 JavaScript、发布 Worker、Scheduler 或存储安全的改动，请补充相应回归测试。

## Pull Request

一个好的 PR 应当：

1. 只解决一个明确问题，避免混入无关重构。
2. 说明改动原因、实现方式和验证结果。
3. 标记是否影响数据库、环境变量、任务状态或平台发布流程。
4. 对新增配置同步更新 `.env.example` 和相关文档。
5. 对用户可见变化补充截图或录屏；截图必须脱敏。
6. 不提交生成文件、缓存、数据库、日志、视频和浏览器登录状态。
7. 依赖升级说明版本变化、原因和验证结果，不直接自动合并 Dependabot PR。

建议的提交信息：

```text
feat: 添加新的用户功能
fix: 修复明确缺陷
docs: 更新文档
test: 添加或调整测试
refactor: 不改变行为的代码整理
chore: 工具或维护性变更
```

## 平台适配变更

抖音和 B站页面会变化。平台适配 PR 还需要说明：

- 测试日期和页面入口。
- 使用的是 Mock、只读检查还是低风险真实灰度。
- 成功证据如何判断。
- 遇到登录、验证、风控或结果不确定时如何进入人工复核。

任何平台适配都不能把“页面动作执行完成”直接当作“发布成功”。

## 文档语言

主 README 使用中文，`README.en.md` 提供英文入口。核心产品能力、安装步骤和安全边界发生变化时，请尽量同步更新两份 README。
