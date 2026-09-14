"""Policy descriptions frozen from master ffdb77f; not an analyzer registry.

No imports from DB, settings, or analyzers: importing these definitions cannot
seed a preset, migrate a database, reroute a task, or initiate an AI request.
Historical execution details remain owned by the existing analyzers for now.
"""

from app.models.content_profile import ContentProfile


BASELINE_COMMIT = "ffdb77f58a3f14c28994f2d013c5195f2b05e9e6"


def legacy_profile_baselines() -> tuple[ContentProfile, ...]:
    """Return independent immutable descriptions, never infer historical use."""
    common = {
        "rules_version": "legacy-2.3.0-ffdb77f",
        "prompt_preset_id": "preset_001",
        "selection_rules": ("允许少于目标数量", "分析不完整时禁止切片"),
    }
    comedy = ContentProfile.model_validate({
        **common,
        "id": "variety_comedy", "name": "棚内综艺·互动笑点",
        "description": "保留康熙来了现有三阶段分析，优先完整笑点、补刀和反转。",
        "recommended_scenes": ("康熙来了", "主持人与嘉宾互动的棚内综艺"),
        "analyzer_key": "variety_comedy",
        "duration": {"strategy": "sentence_bounds", "min_seconds": 45, "max_seconds": 150,
                     "recommended_min_seconds": 60, "recommended_max_seconds": 150},
        "recall": {"strategy": "comedy_windows", "preliminary_limit": 18, "windows": (
            {"provider": "non_local", "seconds": 300, "overlap_seconds": 60, "char_budget": 8000, "recall_limit": 3},
            {"provider": "local", "seconds": 180, "overlap_seconds": 45, "char_budget": 4000, "recall_limit": 3},
        )},
        "expansion": {"strategy": "comedy_context", "before_seconds": 120, "after_seconds": 150,
                      "remote_batch_size": 3, "local_batch_size": 1},
        "scoring": {"strategy": "comedy_weighted", "dimensions": tuple(
            {"id": key, "name": label, "weight": weight} for key, label, weight in (
                ("humor_score", "笑点", .30), ("interaction_reaction_score", "互动反应", .20),
                ("completeness_score", "完整度", .20), ("hook_score", "开头吸引力", .10),
                ("novelty_score", "新鲜度", .10), ("title_score", "标题", .10),
            )), "hard_gates": ({"dimension": "humor_score", "minimum": 75},
                                {"dimension": "completeness_score", "minimum": 70}),
            "a_threshold": 78, "b_threshold": 65, "audio_weight": .25, "signal_strategy": "bonus_only"},
        "dedupe": {"strategy": "comedy_topic", "recall_distance_seconds": 30,
                   "topic_distance_seconds": 120, "topic_gap_seconds": 90,
                   "expansion_overlap": .4, "final_overlap": .3},
        "selection": {"strategy": "a_only", "candidate_pool_default": 12, "candidate_pool_max": 12,
                      "final_target_default": 5, "final_target_max": 12},
        "title_strategy": "具体互动与反转，不用刺激话题替代笑点",
        "prompt_template_key": "legacy_comedy_three_stage",
    })
    general = ContentProfile.model_validate({
        **common,
        "id": "general", "name": "通用内容价值", "description": "保留通用分段分析与任务时长上限。",
        "recommended_scenes": ("尚未归类的语言内容",), "analyzer_key": "general",
        "duration": {"strategy": "task_limit", "min_seconds": 1, "max_seconds": 3600},
        "recall": {"strategy": "general_chunks", "windows": (
            {"provider": "all", "seconds": 180, "char_budget": 4500},)},
        "expansion": {"strategy": "none"},
        "scoring": {"strategy": "confidence", "dimensions": (
            {"id": "confidence_score", "name": "模型置信分（旧接口 0–1）", "weight": 1},)},
        "dedupe": {"strategy": "overlap", "final_overlap": .5},
        "selection": {"strategy": "default_selected", "candidate_pool_default": 12, "candidate_pool_max": 50,
                      "final_target_default": 12, "final_target_max": 50},
        "title_strategy": "沿用任务 Prompt", "prompt_template_key": "legacy_general_chunks",
    })
    long_live = ContentProfile.model_validate({
        **common,
        "id": "long_live_talk", "name": "长直播高光（语言类）",
        "description": "保留独立窗口 checkpoint、跨小时均衡与完整覆盖门禁。",
        "recommended_scenes": ("长直播", "长时间聊天"), "analyzer_key": "long_live_talk",
        "duration": {"strategy": "window_bounds", "min_seconds": 15, "max_seconds": 300,
                     "recommended_min_seconds": 45, "recommended_max_seconds": 180},
        "recall": {"strategy": "long_live_windows", "windows": (
            {"provider": "all", "seconds": 300, "overlap_seconds": 60, "char_budget": 12000, "recall_limit": 5},)},
        "expansion": {"strategy": "none"},
        "scoring": {"strategy": "long_live_score", "dimensions": (
            {"id": "score", "name": "内容价值", "weight": 1},), "a_threshold": 80, "b_threshold": 65},
        "dedupe": {"strategy": "long_live_semantic", "recall_distance_seconds": 30,
                   "topic_distance_seconds": 120, "expansion_overlap": .35, "final_overlap": .35, "semantic_threshold": .34},
        "selection": {"strategy": "hourly_balanced", "candidate_pool_default": 30, "candidate_pool_max": 50,
                      "final_target_default": 30, "final_target_max": 50, "density_per_hour": 4},
        "title_strategy": "观点或故事闭环", "prompt_template_key": "legacy_long_live_windows",
    })
    return comedy, general, long_live
