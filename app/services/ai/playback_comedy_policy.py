"""Playback-oriented stage instructions and evidence gates, without activation."""

import json
import math
from pathlib import Path


GATES = ("opening_supported", "progression_supported", "meaning_preserved", "closure_supported", "core_event_confirmed")
SCORE_KEYS = ("humor_score", "interaction_reaction_score", "completeness_score", "hook_score", "novelty_score", "title_score")


def is_playback(policy):
    return policy.rules_version == "comedy-playback-v1"


def playback_rules():
    return (Path(__file__).resolve().parents[3] / "prompts/production/playback_v1.txt").read_text(encoding="utf-8").strip()


def recall_prompt(window, preference):
    return f"""你是综艺观看价值召回编辑。只宽召回0—3个有具体事件、处境、关系反差或信息推进的时刻，不生成完整切片。
有完整事件但不以大笑为目标的内容也应保留。明星、前任、身体、金钱标签本身不加分；不要为了最终条数提前只留少数候选。
{preference}
只输出JSON：{{"moments":[{{"key_time":"HH:MM:SS","title":"真实短标题","topic_key":"稳定故事标识","humor_reason":"具体观看理由；此兼容字段不限于笑点","recall_score":0}}]}}
时码来自原文，没有支持就返回空数组。窗口{window.index}/{window.total}：
{window.text}"""


def expansion_prompt(contexts, preference):
    return f"""围绕每个召回事件寻找一条自然的连续片段。必须45—150秒，通常偏好60—90秒；已有45—59秒完整闭环不凑60秒。
不要求3秒或15秒出现最终爆点；检查原始开头的观看理由、持续推进和必要结果。不能重排、拼接、跨缺失转写或加无关内容。
保留重要解释，无法支持时跳过该source_id。宽召回不等于必须推荐；不按笑点75分或旧总分78分淘汰。
{preference}
只输出JSON：{{"clips":[{{"source_id":"原值","title":"真实标题","start_time":"HH:MM:SS","end_time":"HH:MM:SS","key_moment_time":"HH:MM:SS","topic_key":"稳定故事标识","summary":"处境与内容","highlight_reason":"具体观看理由","arc_structure":"开头→推进→回应或结果","suggested_editing":"连续边界建议","humor_score":0,"interaction_reaction_score":0,"completeness_score":0,"hook_score":0,"novelty_score":0,"title_score":0}}]}}
分数0—100，按上述新定义判断。时码必须来自对应原文。待扩展：{json.dumps(contexts, ensure_ascii=False)}"""


def judge_prompt(candidates, preference, feedback):
    return f"""比较所有候选的观看价值，完整返回每条候选。必须依据transcript_evidence的原始开头、发展和结尾，不能拿标题或摘要代替。
crosses_clip_boundary=true表示原句跨切点，不能假设整句在片内。音量不等于笑声；声音、画面只是证据，不给数值奖励。
门槛和分数分开：opening_supported=原始开头清楚且有继续观看理由；progression_supported=存在可指出的信息/处境推进；
meaning_preserved=边界未删掉改变含义的解释；closure_supported=兑现问题且保留必要回应；core_event_confirmed=核心事件可由现有证据确认。
逐项只能返回true或false。缺乏证据就false，不为凑数放行；gate_reason写每项判定的原文依据。所有true仅代表推荐生成预览，绝非已通过成片验收或可发布。
av_uncertainty说明原音/画面待核部分；若其影响核心事件判断则core_event_confirmed=false。why_selected引用带时码的开头、发展和结尾，arc_structure写实际结构。
{preference}
近期审片反馈仅作参考，不得覆盖冻结定义：{json.dumps(feedback, ensure_ascii=False)}
只输出JSON：{{"ranked_clips":[{{"source_id":"原值","title":"真实标题","topic_key":"故事标识","humor_score":0,"interaction_reaction_score":0,"completeness_score":0,"hook_score":0,"novelty_score":0,"title_score":0,"arc_structure":"开头→推进→结果","why_selected":"原文时码与理由","rejection_reason":"不足原因，可为空","opening_supported":false,"progression_supported":false,"meaning_preserved":false,"closure_supported":false,"core_event_confirmed":false,"gate_reason":"逐项证据说明","av_uncertainty":"待核部分或仅文本可确认的范围"}}]}}
候选：{json.dumps(candidates, ensure_ascii=False)}"""


def score_playback(candidate, judge, policy):
    complete = all(type(judge.get(k)) in (float, int) and math.isfinite(judge[k]) and 0 <= judge[k] <= 100 for k in SCORE_KEYS)
    scores = {k: float((judge if complete else candidate).get(k, 0)) for k in SCORE_KEYS}
    if any(not math.isfinite(v) or not 0 <= v <= 100 for v in scores.values()):
        raise ValueError("播放规则收到无效分数")
    total = round(sum(scores[d.id] * d.weight for d in policy.scoring.dimensions), 1)
    gates = {k: judge.get(k) is True for k in GATES}
    reason_present = all(isinstance(judge.get(k), str) and judge[k].strip() for k in ("why_selected", "arc_structure", "gate_reason", "av_uncertainty"))
    duration_ok = policy.duration.min_seconds <= candidate.get("duration_seconds", 0) <= policy.duration.max_seconds
    eligible = complete and reason_present and duration_ok and all(gates.values())
    breakdown = {k.removesuffix("_score"): v for k, v in scores.items()}
    breakdown.update(text_quality=total, audio_reaction=0, final=total)
    return {**candidate, **scores, "title": str(judge.get("title") or candidate.get("title") or "综艺候选")[:160],
            "topic_key": str(judge.get("topic_key") or candidate.get("topic_key") or "")[:120],
            "text_quality_score": total, "audio_reaction_score": 0, "quality_score": total,
            "quality_tier": "A" if eligible else "B", "selected_by_default": False,
            "rejection_reason": "" if eligible else str(judge.get("rejection_reason") or "文本证据门槛尚未全部通过，待人工判断"),
            "quality_evidence": {"why_selected": str(judge.get("why_selected") or ""), "arc_structure": str(judge.get("arc_structure") or ""),
                "score_breakdown": breakdown, "audio": candidate.get("audio_evidence") or {},
                "score_dimensions": {d.id.removesuffix("_score"): d.name for d in policy.scoring.dimensions},
                "rules_version": policy.rules_version, "profile_sha256": policy.content_hash(),
                "selection_gates": {**gates, "duration_valid": duration_ok, "review_complete": complete and reason_present},
                "gate_reason": str(judge.get("gate_reason") or "缺少完整复评"),
                "av_uncertainty": str(judge.get("av_uncertainty") or "未核验"), "human_acceptance_required": True}}
