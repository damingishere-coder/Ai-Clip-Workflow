# 稳定 V1 P1A 边界加固任务书

## 背景

P0 已封住测试误删、外键异常和永久删除半成功等数据安全风险。Codemap 与源码交叉审查仍确认三类高风险边界：任务目录可被恶意 `task_dir_name` 越界、并发任务可能获得同一目录、媒体接口可能把数据库中的任意本地路径作为文件响应；Windows 子进程终止也没有确认进程树真实退出。

## 目标

1. 所有任务目录解析都必须限定在 `TASKS_DIR`，拒绝绝对路径、盘符、`..` 与符号链接逃逸。
2. 新任务目录名使用原子目录预占，避免并发上传或重试复用同一目录。
3. 源视频继续支持 `STORAGE_ROOT` / `TASKS_DIR` / `ALLOWED_MEDIA_ROOTS`，但切片、字幕成片和封面响应必须绑定到当前任务及对应产物子目录。
4. 子进程终止必须确认退出；无法确认时显式失败，调用方不得继续按“已终止”处理。

## 允许修改范围

- `app/services/storage_service.py`
- `app/routers/media.py`
- `app/services/managed_process_service.py`
- 与本轮边界直接相关的测试
- `DEVELOPMENT_LOG.md`、`NEXT_STEPS.md`、Codemap 和本任务书

## 禁止修改范围

- 不改变 AI Provider、模型、登录或认证配置。
- 不改变真实投稿、字幕、切片业务语义。
- 不访问真实 NAS 文件，不触发真实第三方请求或投稿。
- 不修改数据库 Schema，不删除历史数据。
- 不删除遗留 `move_task_directory_to_trash`，且不改变其目标必须尚不存在的语义。

## 已确定实现要求

- 路径判断使用解析后的父子关系，不使用容易出现前缀碰撞的字符串判断。
- 任务目录名允许普通文件名中的 `~`，但拒绝绝对路径、盘符、空路径、根目录和任何 `..` 跳转。
- 原子分配通过 `mkdir(exist_ok=False)` 完成；只有任务创建链显式启用预占。
- 数据库读取失败不得静默伪装为“无任务目录”。
- 切片只允许 `05_clips`（兼容旧 `clips`），字幕成片只允许 `06_subtitled`；封面只允许 `07_covers`。
- 对旧 C 盘任务目录只保留受控兼容读取，不扩大到任意路径。
- Windows `taskkill` 的超时、启动失败、非零退出码和退出确认失败均需有明确行为；若进程已并发退出可视为成功。

## 验收标准

- `..\\outside`、绝对路径、盘符和符号链接逃逸不能创建或读取任务目录。
- 两个并发同名目录预占获得不同名称，不发生文件覆盖。
- 数据库中指向任务目录外的切片/字幕路径返回 404；合法任务产物仍可读取。
- 外部白名单源视频读取保持兼容。
- 模拟 `taskkill` 成功、并发退出、非零失败、超时均有确定测试。
- 定向测试、全量测试、Ruff 和 Compileall 通过。

## 测试命令

```powershell
pytest -q tests/test_storage_boundaries.py tests/test_managed_process_service.py tests/test_media_storage_lifecycle.py
pytest -q
ruff check app tests scripts
python -m compileall -q app scripts
```

## 返回格式

- 修改文件与行为边界。
- 测试命令、通过数量与失败证据。
- 对外部原片、遗留目录和现有调用方的兼容结论。
- Commit、分支、Push、PR 状态和下一轮 P1 风险。
