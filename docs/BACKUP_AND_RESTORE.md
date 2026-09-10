# 数据备份、恢复与升级保护

> v2.2 起，恢复会同时校验 SQLite `integrity_check`、`foreign_key_check`、迁移账本 checksum 和已应用迁移的关键索引。检测到活动 Web 服务或无法取得数据库独占锁时会拒绝恢复；请先停止 Web、Scheduler 和活动发布任务，不要绕过该保护。

牛马片场把任务、候选片段、输出片段、账号关联和发布记录保存在 SQLite 中；API Key、Token、路径和本地运行配置通常保存在 `.env` 中。

本指南说明如何创建可校验备份、恢复数据库与配置，以及升级代码前如何保留回滚点。

> [!IMPORTANT]
> 默认备份包含 `.env`，因此可能包含 API Key、Token 和本地路径。备份包没有自动加密，只应保存在可信的本地磁盘、加密硬盘或受保护的私人存储中，不能上传到 Issue、聊天群或公开网盘。

## 备份包包含什么

默认运行：

```powershell
.\scripts\backup.ps1
```

生成的 ZIP 包包含：

```text
manifest.json
├── 应用版本
├── Git commit
├── 创建时间
├── 文件大小与 SHA-256
└── 任务、候选片段、输出片段、发布任务数量

database/workflow.sqlite3
└── 通过 SQLite Backup API 创建的一致性快照

config/.env
└── 本地配置、API Key 与 Token
```

默认不包含：

- 原始长视频
- 生成的短视频
- 音频与字幕文件
- Chrome Profile
- Cookie 和 storage state
- 发布失败截图与运行日志
- Docker 镜像和 Python 虚拟环境

## 一、创建普通备份

在仓库根目录打开 PowerShell：

```powershell
.\scripts\backup.ps1
```

备份默认保存到：

```text
backups/niuma-studio-manual-YYYYMMDD-HHMMSS.zip
```

脚本会：

1. 检查主数据库是否存在。
2. 执行 `PRAGMA quick_check`。
3. 使用 SQLite Backup API 创建快照，兼容 WAL 模式。
4. 再次检查快照完整性。
5. 记录四类核心数据数量。
6. 为每个文件计算 SHA-256。
7. 压缩后重新验证整个备份包。

服务正常运行时也可以创建数据库快照；SQLite Backup API 会生成一致副本。但为了减少升级期间的变化，建议升级前先停止正在运行的批处理任务。

### 自定义备份目录

```powershell
.\scripts\backup.ps1 -OutputDirectory D:\NiuMaBackups
```

### 自定义标签

```powershell
.\scripts\backup.ps1 -Label before-model-change
```

标签只能包含：

```text
英文字母、数字、点、下划线、连字符
```

## 二、不备份 `.env`

不希望备份 API Key 和 Token 时：

```powershell
.\scripts\backup.ps1 -ExcludeEnv
```

这种备份仍然包含完整 SQLite 数据，但更换电脑后需要重新配置 `.env`。

## 三、选择性包含原视频和生成文件

默认备份不包含视频，因为媒体目录可能非常大。

明确需要完整迁移时运行：

```powershell
.\scripts\backup.ps1 -IncludeMedia
```

它会将 `TASKS_DIR` 下的普通文件加入：

```text
media/tasks/
```

安全限制：

- 遇到符号链接会中止，避免越界读取其他目录。
- 大型视频会显著增加时间和磁盘占用。
- 浏览器登录态、Cookie 和失败截图不会被加入。
- 建议先确认目标磁盘有足够空间。

## 四、升级代码前自动保护

升级前运行：

```powershell
.\scripts\pre_upgrade.ps1
```

它会：

1. 创建标签为 `pre-upgrade` 的备份。
2. 记录当前 Git commit。
3. 检查工作区是否存在未提交修改。
4. 不自动执行 `git pull`，避免覆盖本地代码。

备份成功后再执行：

```powershell
git pull --ff-only
.\scripts\doctor.ps1
.\scripts\acceptance.ps1
```

需要连媒体一起保护：

```powershell
.\scripts\pre_upgrade.ps1 -IncludeMedia
```

## 五、验证已有备份

```powershell
python -m scripts.backup_restore verify backups\niuma-studio-manual-20260803-160000.zip
```

验证内容包括：

- ZIP 是否可读取
- 是否存在重复或越界路径
- 清单格式版本
- 文件数量与大小
- 所有文件 SHA-256
- SQLite `quick_check`
- 核心表数量是否与清单一致
- 是否存在清单外文件

备份校验失败时，不要尝试手工解压后覆盖正式数据库。

## 六、恢复数据库

恢复属于高风险操作，必须先停止正在运行的服务。

推荐命令：

```powershell
.\scripts\restore.ps1 `
  -BackupPath .\backups\niuma-studio-manual-20260803-160000.zip `
  -ConfirmRestore `
  -StopServices
```

默认只恢复数据库，不覆盖当前 `.env`。

恢复流程：

1. 完整验证备份包。
2. 展示备份时间、版本和数据数量。
3. 检查 `127.0.0.1:8001` 是否仍有服务运行。
4. 为当前数据库与 `.env` 自动创建 `pre-restore` 回滚包。
5. 将待恢复数据库解压到临时目录并再次校验。
6. 使用同目录临时文件进行原子切换。
7. 恢复后再次检查完整性和数量。
8. 输出回滚包路径。

未添加 `-ConfirmRestore` 时，脚本只展示恢复计划并停止，不会覆盖数据。

## 七、同时恢复 `.env`

```powershell
.\scripts\restore.ps1 `
  -BackupPath .\backups\niuma-studio-manual-20260803-160000.zip `
  -RestoreEnv `
  -ConfirmRestore `
  -StopServices
```

恢复旧 `.env` 后必须核对：

- API Key 是否仍然有效
- `LOCAL_ADMIN_TOKEN`
- `PUBLISH_WORKER_TOKEN`
- `NIUMA_STORAGE_PATH`
- `STORAGE_ROOT` 与 `TASKS_DIR`
- 当前电脑盘符和用户名路径
- Chrome Profile 与 Worker 路径

跨电脑迁移时，通常建议先只恢复数据库，再手工合并旧 `.env` 中仍然需要的配置。

## 八、恢复媒体文件

媒体文件只能恢复到一个不存在或完全为空的目录，避免覆盖当前视频。

```powershell
.\scripts\restore.ps1 `
  -BackupPath .\backups\niuma-studio-full-20260803-160000.zip `
  -MediaDestination D:\NiuMaRestoredMedia `
  -ConfirmRestore `
  -StopServices
```

恢复完成后，再根据实际位置修改 `.env`：

```env
NIUMA_STORAGE_PATH=D:/NiuMaRestoredMedia
STORAGE_ROOT=D:/NiuMaRestoredMedia
TASKS_DIR=D:/NiuMaRestoredMedia
```

## 九、恢复后的检查

恢复完成后运行：

```powershell
.\scripts\doctor.ps1
.\scripts\start.ps1
```

检查：

1. 工作台任务数量是否符合备份清单。
2. 候选片段是否可以打开。
3. 输出文件路径是否仍然存在。
4. 发送中心记录是否正确。
5. 平台账号只检查关联状态，不要立即重复投稿。
6. 跨电脑迁移时重新登录 Chrome 平台账号。

## 十、回滚恢复操作

每次恢复前都会自动创建：

```text
backups/niuma-studio-pre-restore-YYYYMMDD-HHMMSS.zip
```

发现恢复内容不对时，先停止服务，再把该 `pre-restore` 包作为新的恢复来源：

```powershell
.\scripts\restore.ps1 `
  -BackupPath .\backups\niuma-studio-pre-restore-YYYYMMDD-HHMMSS.zip `
  -RestoreEnv `
  -ConfirmRestore `
  -StopServices
```

## 十一、建议的备份频率

- 每次 `git pull` 或版本升级前：创建 `pre-upgrade` 备份。
- 大批量导入视频前：创建普通备份。
- 大规模修改候选片段或排期前：创建普通备份。
- 批量真实发布前：创建普通备份。
- 每周至少保留一份最近验证通过的异盘备份。
- 重要项目结束后：根据需要创建包含媒体的完整备份。

## 十二、不应做的事情

- 不要直接复制正在运行状态下的 `workflow.sqlite3`、`-wal` 和 `-shm` 后假定它们一定一致。
- 不要把包含 `.env` 的 ZIP 提交到 Git。
- 不要把备份上传到公开 Issue。
- 不要在服务运行时手工覆盖数据库。
- 不要跳过备份校验。
- 不要用旧 `.env` 覆盖当前配置后直接真实投稿。
- 不要把浏览器 Profile 和 Cookie 当作普通迁移文件分享。

## 自动化验证

CI 会运行备份恢复往返测试，确认：

- 数据库和 `.env` 可以恢复。
- 恢复前会生成回滚包。
- 四类核心数据数量保持一致。
- 媒体恢复不会覆盖非空目录。
- 损坏或缺失文件的备份不会替换当前数据库。
