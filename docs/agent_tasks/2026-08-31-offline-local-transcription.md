# 完全离线长音频转写实施任务

## 背景

火山引擎长音频转写成本过高。本机具备 RTX 4070 Ti 12GB 和 64GB 内存，项目已包含 faster-whisper、分块 checkpoint、持久化任务和时间戳输出，但默认 Provider 仍偏向远程，且 Windows GPU 运行库与固定模型缓存没有形成可验证的离线闭环。

## 目标

1. 默认使用固定版本 `large-v3 / cuda / float16`，正式转写不上传音频、不产生云端 ASR 费用。
2. 首次联网初始化后只从 E 盘固定缓存加载模型，缺模型时明确失败，不在任务中静默下载。
3. 保留 120 秒分块、5 秒重叠、SQLite checkpoint、词级时间戳及 medium CPU 本地兜底。
4. 系统状态页和任务状态 API 提供模型、GPU、离线锁和实际版本证据。

## 允许修改范围

- 转写配置、Provider 解析、运行时加载、checkpoint 指纹与转写状态 API。
- 系统状态页、相关 JavaScript、一次性初始化/诊断脚本。
- 转写定向测试、项目进度文档和 UI 参考文档。
- Windows GPU 可选依赖清单。

## 禁止修改范围

- 不删除或回显火山 Key、`.env`、历史 transcript、数据库记录和模型缓存。
- 不增加说话人分离、降噪、人声分离或新的云端 ASR Provider。
- 不改变下游字幕、AI 分析、切片、排期和发布数据格式。
- 不重启正式 Web/Worker，不触发真实平台发布或远程转写。

## 已确定实现要求

- 主模型锁定 `Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478`。
- CPU 兜底锁定 `Systran/faster-whisper-medium@08e178d48790749d25932bbc082711ddcfdfbc4f`。
- Windows 固定安装 `nvidia-cublas-cu12==12.9.2.10` 及其 NVRTC 依赖；继续使用 CTranslate2 自带 cuDNN 9，不再安装第二套 cuDNN。
- 完全离线锁开启时，远程 Provider 在请求前失败；HTTP 路由返回 409。
- 转写 checkpoint 使用完整音频 SHA-256，其他视频/封面指纹行为不变。
- 转写 run 的 model 字段记录 `模型名@revision前8位`，旧记录保持可读。

## 验收标准

- 默认、普通任务、自动流水线都选择 local；离线锁下无法创建远程转写任务。
- 模型未缓存、cuBLAS 缺失、CUDA 不可用时返回准确中文诊断。
- 已完成分块可恢复，模型 revision 或音频内容改变时不复用旧 checkpoint。
- 系统状态页不泄露 Secret，显示离线、模型、GPU、设备和外部费用 ¥0。
- 20 秒真实中文音频在 GPU 上完成；代表性长音频验证另行记录，未获确认前不切换正式运行配置。

## 测试命令

- `.venv\\Scripts\\python.exe -m pytest tests/test_offline_transcription.py tests/test_long_live_foundation.py tests/test_transcription_checkpoint_resilience.py -q`
- `.venv\\Scripts\\python.exe -m pytest -q`
- `.venv\\Scripts\\python.exe -m ruff check app tests scripts`
- `.venv\\Scripts\\python.exe -m compileall -q app scripts`
- `node --check app/static/js/app.js`
- `git diff --check`

## 返回格式

- 返回分支、提交、远端 SHA、PR、CI、测试数量与真实 GPU 冒烟证据。
- 明确区分代码/自动化验证、模型下载、真实音频验收和正式运行时切换状态。
