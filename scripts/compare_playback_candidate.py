"""Local files only: prepare frozen evidence and compare an inactive scoring draft.

No application imports, database connections, model calls or activation path.
"""
import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import re


DIMENSIONS = ("hook", "novelty", "interaction_reaction", "completeness", "humor", "title")
BASE_WEIGHTS = dict(zip(DIMENSIONS, (.1, .1, .2, .2, .3, .1), strict=True))
DEFAULT_BUNDLE = Path(__file__).resolve().parents[1] / "prompts/candidates/playback_v1"


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score_values(value):
    if set(value) != set(DIMENSIONS):
        raise ValueError("六个评分维度必须完整且唯一")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100 for v in value.values()):
        raise ValueError("评分必须为0—100有限数值，不能以缺失或布尔值替代")
    return value


def load_bundle(bundle):
    rules = read_json(bundle / "rules.json")
    prompt = (bundle / "prompt.txt").read_bytes()
    weights = rules["weights"]
    if (set(weights) != set(DIMENSIONS)
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in weights.values())
            or not math.isclose(sum(weights.values()), 1, abs_tol=1e-9)):
        raise ValueError("权重必须覆盖六维且合计为1")
    if rules.get("status") != "offline_only" or rules.get("activation_allowed") is not False:
        raise ValueError("只支持未启用的离线候选")
    if rules.get("recommendation_threshold") is not None or rules.get("comparison", {}).get("rank_only") is not True:
        raise ValueError("此工具只比较排名，不执行发布推荐门槛")
    return rules, hashlib.sha256(prompt).hexdigest()


def seconds(value):
    if not re.fullmatch(r"\d{2,}:\d{2}:\d{2}(?:\.\d+)?", value):
        raise ValueError("时码格式错误")
    h, m, s = map(float, value.split(":"))
    if m >= 60 or s >= 60:
        raise ValueError("时码范围错误")
    return h * 3600 + m * 60 + s


def prepare(evidence_path, evidence_sha, transcript_root, rules, prompt_sha):
    raw = evidence_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence_sha.lower():
        raise ValueError("原分析证据SHA-256不匹配")
    data = json.loads(raw)
    root = transcript_root.resolve()
    items = []
    paths = {r["task_id"]: r["task_dir_name"] for r in data["rows"]}
    for run_id, run in data["runs"].items():
        version = run["prompt_version"]
        actual_sha = hashlib.sha256(version["prompt_text"].encode()).hexdigest()
        if not actual_sha == run["prompt_text_sha256"] == version["prompt_sha256"]:
            raise ValueError("历史Prompt冻结证据损坏")
        path = (root / paths[run["task_id"]] / "transcripts/transcript.md").resolve()
        if not path.is_relative_to(root):
            raise ValueError("转写路径越出指定素材根目录")
        transcript = path.read_bytes()
        sentences = []
        for line in transcript.decode("utf-8-sig").splitlines():
            match = re.fullmatch(r"\| (\d\d:\d\d:\d\d) \| (\d\d:\d\d:\d\d) \| (.*) \|", line)
            if match:
                sentences.append({"start": match[1], "end": match[2], "text": match[3]})
        payload = json.loads(run["analysis_payload_json"])
        if payload.get("analysis_meta", {}).get("analysis_incomplete") is True:
            raise ValueError("不能将不完整分析作为完整候选池")
        for clip in payload["clips"]:
            start, end = seconds(clip["start_time"]), seconds(clip["end_time"])
            if end <= start:
                raise ValueError("候选边界无效")
            excerpt = [s for s in sentences if seconds(s["start"]) < end and seconds(s["end"]) > start]
            if not excerpt:
                raise ValueError("候选缺少区间转写")
            scores = clip["quality_evidence"]["score_breakdown"]
            old_scores = score_values({k: scores[k] for k in DIMENSIONS})
            review_input = {"title": clip["title"], "start_time": clip["start_time"],
                            "end_time": clip["end_time"], "transcript": excerpt}
            publications = [r for r in data["rows"] if r["source_analysis_run_id"] == run_id and r["clip_key"] == clip["clip_id"]]
            items.append({"key": run_id + "/" + clip["clip_id"], "task_id": run["task_id"],
                          "run_id": run_id, "clip_key": clip["clip_id"], "input": review_input,
                          "input_sha256": digest(review_input), "transcript_sha256": hashlib.sha256(transcript).hexdigest(),
                          "original_scores": old_scores, "original_total": clip["quality_score"],
                          "original_recommended": clip["selected_by_default"],
                          "profile_version_id": run.get("content_profile_version_id"),
                          "original_prompt_sha256": actual_sha,
                          "publications": [{k: r[k] for k in ("id", "publish_job_id", "output_clip_id", "published_at", "captured_at", "play_count", "like_count", "comment_count")} for r in publications]})
    if len({x["key"] for x in items}) != len(items):
        raise ValueError("候选key重复")
    packet = {"schema_version": 1, "candidate_id": rules["candidate_id"],
              "source_evidence_sha256": evidence_sha.lower(), "rules_sha256": digest(rules),
              "prompt_sha256": prompt_sha, "items": items}
    return {"packet": packet, "sha256": digest(packet)}


def weighted(scores, weights):
    return round(sum(scores[k] * weights[k] for k in DIMENSIONS), 1)


def review_material(envelope):
    """Separate review handoff from the result-bearing comparison packet."""
    packet = envelope["packet"]
    if digest(packet) != envelope["sha256"]:
        raise ValueError("离线候选包已改变")
    return {"packet_sha256": envelope["sha256"], "prompt_sha256": packet["prompt_sha256"],
            "items": [{k: item[k] for k in ("key", "input_sha256", "input")} for item in packet["items"]]}


def rank_items(items, field):
    ordered = sorted(items, key=lambda x: (-x[field], x["key"]))
    rank = 0
    previous = None
    for index, item in enumerate(ordered, 1):
        if previous != item[field]:
            rank = index
        item[field + "_rank"] = rank
        previous = item[field]


def compare(envelope, review, rules, prompt_sha):
    packet = envelope["packet"]
    if digest(packet) != envelope["sha256"]:
        raise ValueError("离线候选包已改变")
    if packet["rules_sha256"] != digest(rules) or packet["prompt_sha256"] != prompt_sha:
        raise ValueError("评分规则或Prompt已改变，需重新准备和复评")
    if review.get("packet_sha256") != envelope["sha256"] or review.get("prompt_sha256") != prompt_sha:
        raise ValueError("复评记录与候选包/Prompt不一致")
    if review.get("mode") != "astra_text_review_not_blind" or not review.get("reviewer"):
        raise ValueError("必须说明文本复评来源及非盲评限制")
    source_items = packet["items"]
    judgments = review["items"]
    if len({x["key"] for x in judgments}) != len(judgments):
        raise ValueError("复评key重复")
    by_key = {x["key"]: x for x in judgments}
    if len({x["key"] for x in source_items}) != len(source_items) or set(by_key) != {x["key"] for x in source_items}:
        raise ValueError("必须完整复评候选池，不允许静默遗漏或加入未知候选")
    result = []
    for item in source_items:
        judgment = by_key[item["key"]]
        if item["input_sha256"] != digest(item["input"]) or judgment.get("input_sha256") != item["input_sha256"]:
            raise ValueError("候选文本证据已改变")
        new_scores = score_values(judgment["scores"])
        old_scores = score_values(item["original_scores"])
        if any(not isinstance(judgment.get(k), str) or not judgment[k].strip() for k in ("rationale", "opening_evidence", "uncertainty")):
            raise ValueError("缺少评分理由、原始开头证据或不确定性说明")
        contributions = {k: round(old_scores[k] * (rules["weights"][k] - BASE_WEIGHTS[k]), 2) for k in DIMENSIONS}
        result.append({**item, "baseline_text": weighted(old_scores, BASE_WEIGHTS),
                       "reweighted_only": weighted(old_scores, rules["weights"]),
                       "text_review": weighted(new_scores, rules["weights"]),
                       "weight_contributions": contributions, "review": judgment})
    groups = []
    for run_id in sorted({x["run_id"] for x in result}):
        group = [x for x in result if x["run_id"] == run_id]
        for field in ("baseline_text", "reweighted_only", "text_review"):
            rank_items(group, field)
        top_n = rules["comparison"]["top_n"]
        old = {x["key"] for x in group if x["baseline_text_rank"] <= top_n}
        new = {x["key"] for x in group if x["text_review_rank"] <= top_n}
        groups.append({"run_id": run_id, "count": len(group), "entered_top_group": sorted(new - old),
                       "left_top_group": sorted(old - new), "top_n": top_n,
                       "tie_notice": "并列排名可能使前N名多于N条；不按key决定推荐"})
    return {"candidate_id": rules["candidate_id"], "packet_sha256": envelope["sha256"],
            "rules_sha256": digest(rules), "prompt_sha256": prompt_sha,
            "review_sha256": digest(review), "reviewer": review["reviewer"],
            "status": "offline_only_not_activated", "groups": groups, "items": result,
            "limitations": ["同一固定区间的文本复评，未重新召回、切片或核验视听效果",
                            "原分析已看过播放结果，复评材料不展示结果但不是盲评",
                            "新分是编辑排序分，不是播放预测或提升证据",
                            "前五仅用于排名对照，不是推荐/发布门槛",
                            "未出现在最新50条中的候选只标未纳入，不推断其未发布或零播放"]}


def render(report):
    esc = html.escape
    chunks = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>候选选片规则：离线对比</title><style>body{font:16px/1.7 system-ui;color:#202b38;background:#fff;max-width:1100px;margin:auto;padding:24px;overflow-wrap:anywhere}h1{font-size:26px}h2{font-size:21px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #ddd;padding:9px}.scroll{overflow-x:auto}details{padding:12px 0;border-bottom:1px solid #ddd}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#536271}a{color:#245aa6}</style>',
              '<h1>候选选片规则：离线对比</h1><p><b>未启用 · 未改变正式Prompt、评分配置、任务或排期</b></p>',
              '<p>三列分别为：旧文本分；保持原维度分、仅改变权重；Astra按候选Prompt重新阅读固定区间转写后的分数。</p><ul>']
    chunks += ['<li>' + esc(x) + '</li>' for x in report['limitations']]
    chunks.append('</ul>')
    for group in report['groups']:
        items = sorted((x for x in report['items'] if x['run_id'] == group['run_id']), key=lambda x: (x['text_review_rank'], x['key']))
        chunks.append(f'<h2>任务 {esc(items[0]["task_id"])} · {len(items)}条完整候选</h2><p>进入前五组 {len(group["entered_top_group"])} 条，离开 {len(group["left_top_group"])} 条；含并列，不等于正式推荐。</p>')
        chunks.append('<div class="scroll"><table><thead><tr><th>片段</th><th>旧分/名次</th><th>仅调权/名次</th><th>文本复评/名次</th><th>理由</th></tr></thead><tbody>')
        for x in items:
            chunks.append(f'<tr><td>{esc(x["input"]["title"])}<br><small>{esc(x["clip_key"])} · {esc(x["input"]["start_time"])}—{esc(x["input"]["end_time"])}</small></td>')
            chunks += [f'<td>{x[f]:.1f} / {x[f + "_rank"]}</td>' for f in ('baseline_text', 'reweighted_only', 'text_review')]
            chunks.append('<td>' + esc(x['review']['rationale']) + '</td></tr>')
        chunks.append('</tbody></table></div>')
        for x in items:
            chunks.append('<details><summary>' + esc(x['input']['title']) + '：原文、评分与结果关联</summary>')
            chunks.append('<p>开头证据：' + esc(x['review']['opening_evidence']) + '</p><p>限制：' + esc(x['review']['uncertainty']) + '</p>')
            chunks.append('<p>新六维分：' + esc(json.dumps(x['review']['scores'], ensure_ascii=False)) + '</p>')
            chunks.append('<p>旧默认推荐：' + ('是' if x['original_recommended'] else '否') + '；历史Profile版本：' + esc(x['profile_version_id'] or '缺失，不能补推') + '</p>')
            if x['publications']:
                for p in x['publications']:
                    chunks.append(f'<p>本次50条中的发布 {esc(str(p["published_at"]))}：{esc(str(p["play_count"]))}播放 / {esc(str(p["like_count"]))}赞 / {esc(str(p["comment_count"]))}评。采集 {esc(str(p["captured_at"]))}</p>')
            else:
                chunks.append('<p>未纳入最新50条的发布样本；不代表未发布或播放为零。</p>')
            chunks.append('<pre>' + esc('\n'.join(s['start'] + '—' + s['end'] + ' ' + s['text'] for s in x['input']['transcript'])) + '</pre></details>')
    chunks.append('<p>证据哈希：' + esc(report['packet_sha256']) + '</p></html>')
    return '\n'.join(chunks)


def write_new(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        handle.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_BUNDLE)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--evidence', type=Path, required=True)
    prep.add_argument('--evidence-sha256', required=True)
    prep.add_argument('--transcripts-root', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--review-input', type=Path, required=True, help='单独导出不含旧分及平台指标的复评材料')
    comp = sub.add_parser('compare')
    comp.add_argument('--packet', type=Path, required=True)
    comp.add_argument('--reviews', type=Path, required=True)
    comp.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    rules, prompt_sha = load_bundle(args.bundle)
    if args.command == 'prepare':
        if args.output.resolve() == args.review_input.resolve() or args.output.exists() or args.review_input.exists():
            raise FileExistsError('两个输出路径必须不同且尚不存在')
        result = prepare(args.evidence, args.evidence_sha256, args.transcripts_root, rules, prompt_sha)
        write_new(args.output, json.dumps(result, ensure_ascii=False, indent=2))
        write_new(args.review_input, json.dumps(review_material(result), ensure_ascii=False, indent=2))
    else:
        result = compare(read_json(args.packet), read_json(args.reviews), rules, prompt_sha)
        if any((args.output_dir / name).exists() for name in ('comparison.json', 'comparison.html')):
            raise FileExistsError('结果已存在，请指定新的输出目录，保留旧证据')
        write_new(args.output_dir / 'comparison.json', json.dumps(result, ensure_ascii=False, indent=2))
        write_new(args.output_dir / 'comparison.html', render(result))
    print('完成：离线文件已保存，未连接数据库、模型或发布服务。')


if __name__ == '__main__':
    main()
