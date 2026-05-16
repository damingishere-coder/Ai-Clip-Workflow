from app.models.task import ClipCandidate


def generate_candidate_clips_placeholder(task_id: str) -> list[ClipCandidate]:
    return [
        ClipCandidate(
            id="clip-001",
            task_id=task_id,
            title="高密度观点片段",
            start_time="00:12:08",
            end_time="00:13:54",
            duration_seconds=106,
            summary="主播在短时间内连续输出多个适合二次传播的观点。",
            reason="信息密度高，开头明确，结尾完整。",
            spread_value="适合做知识类短视频切片。",
        ),
        ClipCandidate(
            id="clip-002",
            task_id=task_id,
            title="情绪峰值片段",
            start_time="00:38:20",
            end_time="00:40:05",
            duration_seconds=105,
            summary="直播讨论进入冲突和反转，互动感较强。",
            reason="有明确情绪波动，容易吸引停留。",
            spread_value="适合做引发讨论的短视频。",
        ),
    ]
