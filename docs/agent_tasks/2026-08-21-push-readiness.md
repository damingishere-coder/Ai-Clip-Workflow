# 2026-08-21 推送前一致性修正任务

## 背景

当前工作区准备重启并推送到 GitHub。只读审计发现：Windows 启动脚本已改为默认尝试启动真实发布 Worker，但 `README.md` 与 `docs/PORTABLE_SETUP.md` 仍描述旧的 `-WithPublisher` 行为；使用 `-SkipWorker` 时启动成功提示也会误导为“需要安装 Chrome”。此外，新建任务页的任务名历史查询没有去重和数量上限。

## 目标

在不改变既定产品边界、不触碰正式数据和真实平台的前提下，修正文档与提示的一致性，并为任务名历史查询增加安全上限。

## 允许修改范围

- `app/services/task_service.py`
- `scripts/start.ps1`
- `README.md`
- `docs/PORTABLE_SETUP.md`
- `tests/test_task_name_history.py`
- `tests/test_publish_worker_autostart.py`
- `DEVELOPMENT_LOG.md`
- `NEXT_STEPS.md`

## 禁止修改范围

- 不修改数据库结构、正式 SQLite 数据、`.env`、视频或浏览器资料。
- 不修改发布调度、Publisher、Worker 的实际执行逻辑。
- 不修改 `scripts/stop.ps1`、Docker 配置、DeepSeek 配置或其他业务页面。
- 不运行 Git 提交、推送、历史重写、发布或真实平台操作。
- 不调用其他子代理，不访问项目外路径或 secrets。

## 已确定实现要求

1. `list_task_name_history()` 只返回非空、未删除的唯一任务名，按各名称最后一次创建时间从新到旧排列，并限制最多 100 条。
2. 为去重、顺序、隐藏任务和 100 条上限补充或调整测试。
3. `scripts/start.ps1` 保持现有默认 Worker 启动逻辑不变，但成功提示必须区分：`-SkipWorker`、`-Development`、缺少 Chrome；不得在 Chrome 已安装且主动跳过 Worker 时提示“需要安装 Chrome”。
4. `scripts/start.ps1` 必须继续保留 UTF-8 BOM，并保持 Windows PowerShell 5.1 可解析。
5. `README.md` 与 `docs/PORTABLE_SETUP.md` 明确：正式模式默认在检测到 Chrome 时启动 Worker；只启动工作台使用 `-SkipWorker`；Demo/Development 自动跳过 Worker；`-WithPublisher` 仅为兼容旧命令，不再是必需参数；停止脚本默认同时停止 Docker 与本项目 Worker。
6. 在 `DEVELOPMENT_LOG.md` 和 `NEXT_STEPS.md` 记录本次一致性修正与安全启动方法；不要宣称尚未运行的测试结果。

## 验收标准

- 任务名历史满足唯一、非空、最新优先、最多 100 条。
- 文档与启动脚本当前行为一致。
- `-SkipWorker` 的完成提示准确，且脚本 BOM/语法不退化。
- 没有范围外修改、TODO、debug 输出、临时文件或硬编码 secrets。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_task_name_history.py tests/test_publish_worker_autostart.py
.\.venv\Scripts\python.exe -m compileall -q app scripts
node --check app/static/js/app.js
docker compose config --quiet
```

另做只读 PowerShell AST 检查，确认 `scripts/start.ps1` 解析错误数为 0，并检查文件开头仍是 UTF-8 BOM。

## 返回格式

返回 `status`、修改文件列表、实现摘要、测试命令与结果、剩余风险。不要返回“已提交/已推送”。
