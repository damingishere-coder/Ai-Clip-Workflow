# 堆叠 PR CI 修复任务

## 背景

当前 CI 只在目标分支为 `master` 的 Pull Request 上运行。项目近期使用依赖有序的堆叠 PR，导致目标为功能分支的 PR 没有任何自动检查，无法证明 Linux、Windows 和 Docker 三条验收链路通过。

## 目标

- 保留向 `master` 推送时的 CI。
- 让所有 Pull Request（包括目标为功能分支的堆叠 PR）运行同一套 CI。
- 不改变任何测试步骤、权限、凭据或发布流程。

## 允许修改范围

- `.github/workflows/ci.yml`
- `CI_STACKED_PR_TASK.md`
- `DEVELOPMENT_LOG.md`
- `NEXT_STEPS.md`

## 禁止修改范围

- `app/`、`tests/`、数据库和运行配置。
- CI Job 内容、依赖版本、Secrets、权限与真实发布流程。
- 现有 PR 的目标分支或 Git 历史。

## 已确定实现要求

1. `push` 继续只监听 `master`。
2. `pull_request` 不再限制目标分支，从而覆盖堆叠 PR。
3. 不使用 `pull_request_target`，避免提升来自 PR 代码的权限。

## 验收标准

- Workflow YAML 可解析。
- 本 PR（目标为 `master`）的三项现有 CI 全部通过。
- 后续堆叠 PR 的目标分支不为 `master` 时，也会生成 CI 检查。
- `git diff --check` 通过，提交不包含范围外文件或敏感信息。

## 测试命令

```powershell
git diff --check
git diff -- .github/workflows/ci.yml
```

## 返回格式

- 修改文件清单。
- 本地检查与 GitHub CI 结果。
- 分支、提交 SHA、远端 SHA、PR 地址和合并状态。
