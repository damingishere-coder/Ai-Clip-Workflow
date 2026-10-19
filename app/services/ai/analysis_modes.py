"""显式分析模式；缺失字段的历史任务继续原流程。"""

STAGED_MODE = "staged_v1"
FULL_MODE = "codex_full_transcript_v1"


def validate_analysis_mode(mode, *, provider, profile_id, visual_enabled=False, rules_version=None):
    if mode == STAGED_MODE:
        return
    if mode != FULL_MODE:
        raise ValueError("不支持的 AI 分析模式，不能回退为其他算法")
    if provider != "codex" or profile_id != "variety_comedy" or visual_enabled:
        raise ValueError("全文一次分析仅支持 Codex 棚内综艺，且须关闭可选视觉分析")
    if rules_version is not None and rules_version != "comedy-playback-v1":
        raise ValueError("全文一次分析须使用已冻结的综艺播放目标 v1 规则")
