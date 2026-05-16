# Git 与敏感信息安全说明

这份文档只说明一件事：怎么把项目保存到 Git，同时不把真实 API Key、视频文件和本地数据上传。

## 1. 真实 API Key 放在哪里

真实 API Key 只允许放在项目根目录：

```text
.env
```

不要把真实 API Key 写进：

- README
- docs 文档
- Python 代码
- JavaScript 代码
- Prompt 文件
- Git 提交说明

## 2. 为什么 `.env` 不会上传

项目根目录的 `.gitignore` 已经包含：

```text
.env
.env.*
!.env.example
```

意思是：

- `.env` 不提交。
- `.env.local`、`.env.production` 等真实配置不提交。
- `.env.example` 作为空模板可以提交。

`.env.example` 只能放示例值或占位文字，不能放真实密钥。

## 3. 本次提交前的安全检查

提交前需要确认：

```powershell
git status --short
git ls-files -- .env
git check-ignore -v .env
```

期望结果：

- `git ls-files -- .env` 没有输出，说明 `.env` 没有被 Git 追踪。
- `git check-ignore -v .env` 显示 `.gitignore` 规则，说明 `.env` 已被忽略。

还可以扫描仓库里的可疑密钥文本：

```powershell
rg -n --hidden --glob '!\.git/**' --glob '!tasks/**' --glob '!data/**' --glob '!.env' "(\bsk-[A-Za-z0-9]{16,}|Authorization: Bearer|Bearer [A-Za-z0-9_\-\.]{16,}|SECRET_KEY\s*=|api_key\s*=|password\s*=|token\s*=)" .
```

如果只看到 `.env.example`、文档里的占位说明、代码里的变量名，属于正常情况。

## 4. 哪些本地数据不会上传

这些内容已经被忽略：

```text
.venv/
data/*
tasks/*
*.sqlite
*.sqlite3
*.db
*.mp4
*.mov
*.mkv
*.avi
*.webm
*.mp3
*.wav
*.log
```

保留的只有：

```text
data/.gitkeep
tasks/.gitkeep
```

它们只是空占位文件，用来让 Git 记住目录存在。

## 5. 保存 Git 的标准流程

每次保存前先检查：

```powershell
git status --short
```

确认没有 `.env` 后再添加文件：

```powershell
git add .
```

再次检查：

```powershell
git status --short
```

提交：

```powershell
git commit -m "docs: organize project documentation"
```

推送到远程仓库：

```powershell
git push
```

如果还没有远程仓库，需要先在 GitHub 创建仓库，再添加远程地址：

```powershell
git remote add origin 你的仓库地址
git push -u origin master
```

## 6. 如果不小心提交了真实 Key

立刻做三件事：

1. 去 API 服务后台撤销旧 Key。
2. 生成新 Key，重新填写到本地 `.env`。
3. 告诉我，让我帮你清理 Git 历史记录。

只删除文件再提交一次不够安全，因为旧 Key 仍可能留在 Git 历史里。
