from app.models.task import ClipCandidate


def generate_candidate_clips_placeholder(task_id: str) -> list[ClipCandidate]:
    return [
        ClipCandidate(
            id="clip-001",
            task_id=task_id,
            title="刚需痛点：内容切片的核心要素",
            start_time="00:01:35",
            end_time="00:04:35",
            duration_seconds=180,
            summary="主播用具体案例说明直播切片要抓住痛点、结论和行动建议。",
            reason="观点明确，结构完整，开头能快速进入主题。",
            spread_value="高，适合做知识型口播切片。",
        ),
        ClipCandidate(
            id="clip-002",
            task_id=task_id,
            title="互动问答：如何提升完播率",
            start_time="00:12:10",
            end_time="00:15:40",
            duration_seconds=210,
            summary="观众提问后，主播拆解了标题、前 3 秒和节奏控制。",
            reason="问答感强，用户关注度高，容易形成评论讨论。",
            spread_value="高，适合做运营技巧类内容。",
        ),
        ClipCandidate(
            id="clip-003",
            task_id=task_id,
            title="案例分享：爆款片段复盘",
            start_time="00:25:20",
            end_time="00:29:50",
            duration_seconds=270,
            summary="主播复盘一个高播放片段，说明选题与剪辑节奏的关系。",
            reason="案例具体，适合配合画面做图文提示。",
            spread_value="中，适合做系列内容补充。",
        ),
    ]
