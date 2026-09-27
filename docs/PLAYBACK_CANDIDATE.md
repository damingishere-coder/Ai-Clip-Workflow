# 播放目标候选规则：离线使用说明

状态：`playback-v1-draft`，仅候选，未启用。此交付不改变正式 Prompt、Content Profile、旧任务评分、排期或发布。没有新增模型调用，也没有重新召回或裁切媒体。

## 候选改变

|维度|旧权重|候选权重|候选判断重点|
|---|---:|---:|---|
|开头 hook|10%|25%|原始起点是否容易理解、有继续看的理由|
|新鲜度 novelty|10%|20%|具体处境与关系反差|
|互动 interaction_reaction|20%|20%|持续推进和信息增量|
|完整度 completeness|20%|20%|结果兑现、必要回应与闭环|
|幽默 humor|30%|10%|有证据的笑点与反差|
|标题 title|10%|5%|片内证据能够兑现标题|

这不仅是调权；部分维度含义改变，因此旧维度直接调权只作为对照。规则不是抖音算法结论，也不是播放量预测。

充分召回、逐条评审、允许少选。45—150秒是候选硬范围，60—90秒仅是偏好；不机械要求前三秒或15秒内出现最终爆点，不为达到60秒补无关内容。继续只做连续截取，保留解释和当事人回应。

新分不套用旧78分或笑点75分门槛。前五组只展示排名变化，不产生正式推荐。时码、原意、核心事件、待核事项和人工成片验收另作门槛。未知声音或画面效果明确待核，不伪装成已检验，也不直接按零分处理。

## 文件与复现

- `prompts/candidates/playback_v1/prompt.txt`：完整候选 Prompt，保留现有生产协议的说明，但本轮没有接入生产。
- 同目录 `rules.json`：机器可读权重和边界，`activation_allowed=false`。它不是可直接传给现有系统的 Content Profile。
- `scripts/compare_playback_candidate.py`：标准库文件工具，不导入应用、不连接数据库、网络、模型或发布服务。
- `tests/test_playback_candidate_offline.py`：完整性、结果隔离、评分、并列、HTML转义及不覆盖证据的回归。

在项目根目录用项目 Python 执行，路径换成自己的本地证据路径。下面 `SOURCE_SHA256` 必须是已冻结源文件的64位SHA-256，不是任意占位值：

```powershell
python scripts/compare_playback_candidate.py prepare `
  --evidence data/analysis/your-source/evidence.json `
  --evidence-sha256 SOURCE_SHA256 `
  --transcripts-root 'E:\直播间切片工作流存储' `
  --output data/analysis/your-comparison/packet.json `
  --review-input data/analysis/your-comparison/review-input.json

python scripts/compare_playback_candidate.py compare `
  --packet data/analysis/your-comparison/packet.json `
  --reviews data/analysis/your-comparison/reviews.json `
  --output-dir data/analysis/your-comparison/results-v1
```

`prepare`读取本次分析的 evidence 格式和逐句 Markdown 转写，校验源证据、历史 Prompt 与路径范围，从有关分析任务取完整候选池。输出路径显式指定且必须尚不存在。

评分时只交付 `review-input.json` 和候选 Prompt。这个材料没有旧分、默认推荐或播放/赞/评。`packet.json`包含比较需要的历史分和平台结果，不能作为复评模型的输入。两者用候选key及输入哈希关联。当前 Astra 已参与最初的数据分析，因此本次仍是非盲评；分开材料不能消除先验认知。

`reviews.json`结构：

```json
{
  "packet_sha256": "从材料复制",
  "prompt_sha256": "从材料复制",
  "mode": "astra_text_review_not_blind",
  "reviewer": "实际评审来源",
  "items": [{
    "key": "run_id/clip_id",
    "input_sha256": "从该条复制",
    "scores": {"hook": 80, "novelty": 70, "interaction_reaction": 70, "completeness": 80, "humor": 60, "title": 80},
    "rationale": "说明开头、发展、结尾如何支持评分",
    "opening_evidence": "时间戳及原始开头台词",
    "uncertainty": "无法由文本确认的部分"
  }]
}
```

必须逐条覆盖整个候选池；拒绝漏项、重复、未知key、缺失维度、非有限数值和证据哈希变更。不靠成绩填分。工具不会生成评审意见或调用模型。Prompt或规则改动后须重新准备和评审，不能给旧评审换哈希冒充新结果。

`comparison.json`保留全量证据与六维分；`comparison.html`可直接在浏览器打开，按节目列出旧文本分、旧维度仅调权分、按新定义文本复评分。点击片段可展开原文、理由和平台结果。并列共享名次，不以字典排序打破推荐并列。

## 本轮证据解释与下一步

本轮从最新50条发布数据关联到8个分析任务，完整复评84条原候选；50条发布记录对应49个不同成片。旧总分全部可由历史六维公式复算。全量明细、平台数据、转写、评审和结果保存在本机忽略目录 `data/analysis/20260927-playback-candidate/`，不提交仓库。

只改权重改变41条期内排名，新定义复评改变66条；每期前五组共进入10条、离开10条。它们只说明排序发生变化。固定区间限制、转写错误、缺少原音画面、发布时间差异和样本少均未消除，未入最新50条不能认定未发布或零播放；有一组缺历史Profile版本，保持缺失。

下一步先看升降条目的实际成片，判断新理由是否符合编辑目标。正式接入还需受支持的评分配置版本、独立选片/发布门槛及明确启用确认，不能仅把候选Prompt粘贴进旧Challenger后声称权重已生效。待确认试验方案后，使用新的素材和预先约定的相同发布观察窗口，主看播放量，赞/播放与评/播放作辅助解释；保留反例，不将本轮回看当作效果验证。

本次不需要部署或重启8001/Worker。代码合并不等于策略启用。
