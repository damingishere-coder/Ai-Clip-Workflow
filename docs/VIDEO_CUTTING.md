# 自动切片说明

自动切片负责把人工审核后的候选片段切成短视频文件。

## 1. 前置条件

必须满足：

- 任务有可读取的原始视频。
- AI 分析已生成候选片段。
- 至少一条候选片段处于启用状态。

只有 `enabled = 1` 且未删除的候选片段会参与切片。

## 2. 输出目录

当前官方输出目录是：

```text
05_clips/
```

历史兼容目录 `clips/` 仍可能被创建，但新文档、新功能和新产物统一使用 `05_clips/`。

## 3. 切片流程

```text
读取启用候选片段
→ 创建 cut_runs 记录
→ 逐条调用 FFmpeg
→ 写入 output_clip
→ 激活成功 cut_run
→ 更新任务状态
```

切片状态：

- 全部成功：`tasks.status = completed`
- 部分成功：`tasks.status = completed_with_errors`
- 全部失败：`tasks.status = failed`

## 4. 版本化和失败回滚

每次生成切片都会创建新的 `cut_runs`。

成功规则：

- 新 run 成功后会成为活跃 run。
- 旧 run 的 `output_clip` 会标记为非活跃。

失败规则：

- 如果新 run 全部失败，不会覆盖旧的活跃切片。
- 页面仍能看到上一轮成功的活跃结果。

## 5. FFmpeg

切片依赖 Windows 能直接执行：

```powershell
ffmpeg -version
```

相关超时配置：

```text
FFMPEG_CUT_TIMEOUT=600
```

如果切片长期无响应，通常要检查：

- FFmpeg 是否安装。
- 原视频路径是否存在。
- 路径是否包含无法访问的网络盘。
- 候选片段时间是否超出视频范围。

## 6. 数据库表

相关表：

- `clip_candidates`：输入候选片段。
- `cut_runs`：每轮切片运行。
- `output_clip`：每条输出切片。

## 7. 后续流程

切片完成后可以继续：

- 在字幕工作台生成带字幕视频，输出到 `06_subtitled/`。
- 在发送中心刷新队列，生成 `publish_jobs` 和封面帧。
