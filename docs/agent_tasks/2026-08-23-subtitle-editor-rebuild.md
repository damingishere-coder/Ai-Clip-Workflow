# PR 3 执行任务：字幕数据层与专业编辑器重构

## 背景

现有字幕工作台从 `transcript.md` 最多读取 120 行，按整秒时间生成固定 1080×1920 ASS，并在 HTTP 请求内同步烧录。长直播需要以原片毫秒时间轴为事实来源，切片字幕必须继承且不能覆盖人工编辑。

## 目标

1. 建立 `subtitle_tracks / subtitle_revisions / subtitle_cues` 统一字幕模型。
2. 保存 output clip 的不可变原片起止毫秒快照，完成原片→切片本地时间换算。
3. 使用 `pysubs2==1.9.0` 实现 SRT/VTT/ASS 导入导出与 ASS 序列化。
4. 本地固定 `wavesurfer.js@7.12.11`，使用 Regions/Timeline 和服务端 peaks 构建长视频编辑器。
5. 提供文本、毫秒时间、说话人、增删、拆分、合并、批量位移、搜索替换、撤销重做、自动保存和质量告警。

## 允许修改范围

- 字幕数据库表、output_clip 快照和 subtitle_jobs revision 引用。
- 新字幕服务、模型、API 路由、媒体 peaks 接口。
- 字幕工作台模板、专用 JS/CSS 和固定版本 vendor 文件。
- requirements、第三方许可证、测试和项目文档。

## 禁止修改范围

- 不实现自动说话人分离、翻译、卡拉 OK 或 AI 自动覆盖。
- 不复制 GPL-3.0 VideoCaptioner 源码；仅借鉴流程。
- PR 3 不接入自动流水线审核暂停和异步批量烧录，这属于 PR 4。
- 不删除旧 `subtitle_jobs` 或旧带字幕成片；旧同步烧录入口暂时兼容。
- 不读取或写入 secrets，不合并 PR，不改写 Git 历史。

## 已确定实现要求

- revision 内容创建后不可原地改写；每次人工保存创建子 revision 并切换 active。
- 渲染 Job 增加 `revision_id`，后续只能引用固定 revision。
- source track 来自结构化转写 checkpoint；无结构化结果时完整读取 Markdown，不再截断 120 行。
- clip track 使用 `source_start_ms/source_end_ms` 快照截取 source cues 并换算本地毫秒。
- 未人工编辑 clip 自动跟随 source active revision；已有人工 revision 只标记 `pending_sync`。
- 质量规则只返回 warning/error，不自动修改文字。
- 波形 peaks 服务端缓存并限制点数，浏览器不解码完整 6 小时音频。
- 样式序列化使用实际视频宽高，并提供安全区与说话人样式。

## 验收标准

- SRT/VTT/ASS 往返保留毫秒时间，中文不乱码。
- 超过 120 cues 不截断；重叠、时长、间隔、行长和阅读速度规则正确。
- 原片→切片边界截取准确；人工 clip revision 不被 source 更新覆盖。
- 数千 cues 范围查询分页；前端实现虚拟滚动及编辑操作。
- 六小时 peaks 输出点数受控且有缓存。
- 旧字幕任务、通用/综艺/长直播流程不回归。

## 测试命令

```powershell
.venv\Scripts\ruff.exe check app tests
.venv\Scripts\python.exe -m compileall app tests
node --check app/static/js/subtitle-editor.js
.venv\Scripts\python.exe -m pytest tests/test_subtitle_editor.py tests/test_split_services.py tests/test_versioning_rollback.py -q
.venv\Scripts\python.exe -m pytest tests/ -q
```

## 返回格式

- 数据模型、API 和编辑器能力摘要
- 第三方版本与许可证
- 专项/全量测试证据
- 中文 commit、推送分支、堆叠 PR 链接
