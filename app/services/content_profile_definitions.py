"""New content policies; published legacy baselines remain unchanged."""

from app.models.content_profile import (
    ContentProfile, DurationPolicy, RecallPolicy, WindowPolicy, ExpansionPolicy,
    ScoringPolicy, ScoreDimension, HardGate, DedupePolicy, SelectionPolicy,
)
from app.services.content_profile_baselines import legacy_profile_baselines


def interview_profile() -> ContentProfile:
    return ContentProfile(
        id="interview_story", name="人物访谈与故事", description="识别真实经历、故事、冲突与情绪转折，保留表达上下文。",
        recommended_scenes=("人物访谈", "名人采访", "深度聊天"), rules_version="interview-v1",
        analyzer_key="content", prompt_preset_id="profile_interview_v1", prompt_template_key="content-stages-v1",
        duration=DurationPolicy(strategy="sentence_bounds", min_seconds=45, max_seconds=240,
                                recommended_min_seconds=60, recommended_max_seconds=180),
        recall=RecallPolicy(strategy="content_windows", preliminary_limit=18, windows=(
            WindowPolicy(provider="non_local", seconds=300, overlap_seconds=60, char_budget=10000, recall_limit=3),
            WindowPolicy(provider="local", seconds=180, overlap_seconds=45, char_budget=5000, recall_limit=3),
        )),
        expansion=ExpansionPolicy(strategy="content_context", before_seconds=120, after_seconds=180),
        scoring=ScoringPolicy(strategy="content_weighted", dimensions=tuple(
            ScoreDimension(id=key, name=name, weight=weight) for key, name, weight in (
                ("story_value", "故事价值", .25), ("completeness", "完整度", .25),
                ("conflict", "冲突与转折", .20), ("hook", "开场吸引力", .15),
                ("novelty", "新鲜度", .10), ("title_fit", "标题适配", .05),
            )), hard_gates=(HardGate(dimension="story_value", minimum=70), HardGate(dimension="completeness", minimum=70)),
            a_threshold=78, b_threshold=65),
        dedupe=DedupePolicy(strategy="content_topic", recall_distance_seconds=30,
                            topic_distance_seconds=120, final_overlap=.4),
        selection=SelectionPolicy(strategy="a_only", candidate_pool_default=12, candidate_pool_max=12,
                                  final_target_default=5, final_target_max=12),
        title_strategy="围绕真实人物经历与冲突，避免虚构爆料或夸大情绪",
        selection_rules=("保留起因、经历与结果，不截断关键解释", "故事价值不以笑声或综艺互动衡量", "允许候选不足，不为凑数降低门槛"),
    )


def builtin_profiles() -> tuple[ContentProfile, ...]:
    return (*legacy_profile_baselines(), interview_profile(), knowledge_profile())


def knowledge_profile() -> ContentProfile:
    data = interview_profile().model_dump(mode="json")
    data.update(id="knowledge_opinion", name="知识与观点", description="识别知识价值、论证完整的观点、金句与认知反差。",
                recommended_scenes=["播客", "知识节目", "演讲", "观点类长视频"], rules_version="knowledge-v1",
                prompt_preset_id="profile_knowledge_v1", title_strategy="准确表达观点与适用条件，不夸大结论或制造争议",
                selection_rules=["保留论据、限定条件和结论", "区分事实、个人观点与推测", "知识价值不以笑声或争议强度代替", "允许不足，不为凑数降低门槛"])
    data["duration"].update(min_seconds=30, max_seconds=180, recommended_min_seconds=45, recommended_max_seconds=120)
    data["scoring"]["dimensions"] = [{"id": key, "name": name, "weight": weight} for key, name, weight in (
        ("knowledge_value", "知识与观点价值", .30), ("completeness", "完整度", .25), ("evidence", "论据与上下文", .20),
        ("hook", "开场吸引力", .15), ("contrast", "认知反差", .05), ("title_fit", "标题适配", .05),
    )]
    data["scoring"]["hard_gates"] = [{"dimension": key, "minimum": 70} for key in ("knowledge_value", "completeness")]
    return ContentProfile.model_validate(data)
