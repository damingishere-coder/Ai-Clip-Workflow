"""将同素材两组隔离结果生成可播放原片、逐条填写结论的离线审片页。"""

import argparse
import hashlib
import html
import json
from pathlib import Path


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def build_review(baseline_dir, trial_dir, output):
    def manifest(path):
        return json.loads((path if path.is_file() else path / "manifest.json").read_text(encoding="utf-8"))
    baseline = manifest(baseline_dir)
    trial = manifest(trial_dir)
    by_id = {task["task_id"]: task for task in trial["tasks"]}
    if set(by_id) != {task["task_id"] for task in baseline["tasks"]}:
        raise ValueError("两组素材不一致")
    materials, cards = [], []
    labels = {
        "opening": "开头",
        "topic": "主题",
        "highlight": "笑点",
        "response": "回应",
        "ending": "收尾",
        "comparison": "与另一组相比的具体改善或退步",
    }
    for task in baseline["tasks"]:
        other = by_id[task["task_id"]]
        if task["transcript_sha256"] != other["transcript_sha256"]:
            raise ValueError("两组转写不一致")
        material = {
            "source_task_id": task["task_id"],
            "transcript_sha256": task["transcript_sha256"],
            "samples": [],
        }
        cards.append(f"<h2>素材 {html.escape(task['task_id'])}</h2>")
        for side, entry in (("baseline", task), ("trial", other)):
            result = json.loads(Path(entry["result"]).read_text(encoding="utf-8"))
            if result["analysis_meta"].get("analysis_incomplete"):
                raise ValueError("有未完成分析，不能导出验收页")
            material[side + "_result"] = result
            material[side + "_result_sha256"] = canonical_hash(result)
            for clip in result["clips"]:
                decision = {
                    "publish": "可出片",
                    "review": "待审核",
                    "reject": "已淘汰",
                }[clip["decision"]]
                cards.append(
                    f"<article><h3>{'恢复基线' if side == 'baseline' else '单项试验'} · {decision} · {html.escape(clip['title'])}</h3><p>{html.escape(clip['start_time'])}–{html.escape(clip['end_time'])} · {html.escape(clip['decision_reason'])}</p>"
                )
                for issue in clip.get("review_issues", []):
                    cards.append(f"<p>{html.escape(issue)}</p>")

                def seconds(value):
                    h, m, s = map(int, value.split(":"))
                    return h * 3600 + m * 60 + s

                start, end = seconds(clip["start_time"]), seconds(clip["end_time"])
                url = Path(task["video"]).as_uri() + f"#t={start},{end}"
                cards.append(
                    f'<video controls preload="none" src="{html.escape(url, quote=True)}" data-start="{start}" data-end="{end}"></video>'
                )
                for point in clip.get("quality_evidence", {}).get(
                    "content_evidence", []
                ):
                    cards.append(
                        f"<p>{html.escape(labels.get(point['role'], point['role']))} {html.escape(point['start_time'])}：{html.escape(point['quote'])}</p>"
                    )
                if clip["decision"] == "publish":
                    sample_id = side + ":" + clip["clip_id"]
                    material["samples"].append({"sample_id": sample_id})
                    cards.append(
                        _form(len(materials), len(material["samples"]) - 1, labels)
                    )
                cards.append("</article>")
        if not material["samples"]:
            material["samples"].append({"sample_id": "zero-usable"})
            cards.append(
                "<article><h3>两组均零条可用：请结合原片说明依据</h3>"
                + _form(len(materials), 0, labels)
                + "</article>"
            )
        materials.append(material)
    data = json.dumps(materials, ensure_ascii=False).replace("<", "\\u003c")
    page = (
        """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>同素材对照审片</title>
<style>body{max-width:960px;margin:32px auto;padding:0 20px;font:16px/1.6 system-ui;background:#f5f5f7;color:#222}article{background:white;padding:24px;margin:24px 0;border-radius:16px}video{width:100%;max-height:480px}label{display:block;margin:16px 0}textarea{display:block;width:95%;min-height:70px;font:inherit}button,select{padding:12px;font:inherit}</style>
<h1>同素材对照审片</h1><p>逐条播放原片，核对开头、主题、笑点、回应和收尾，再记录相较另一组的变化。分数和数量不能代替内容审核。本页只读原片，导出记录不会应用正式规则。</p>"""
        + "".join(cards)
        + """
<label>本轮结论 <select id="verdict"><option value="rejected">未证明改善，拒绝应用</option><option value="accepted">逐条审核通过，建议应用</option></select></label>
<label><input type="checkbox" id="confirmed">我已亲自核对以上全部样本和两组对照，确认此结论</label><button id="export">导出内容审核记录</button><p id="message"></p><script>
const materials=DATA;
document.querySelectorAll('video').forEach(v=>{v.addEventListener('play',()=>{if(v.currentTime<Number(v.dataset.start)||v.currentTime>=Number(v.dataset.end))v.currentTime=Number(v.dataset.start)});v.addEventListener('timeupdate',()=>{if(v.currentTime>=Number(v.dataset.end))v.pause()})});
document.querySelector('#export').onclick=()=>{const message=document.querySelector('#message');if(!document.querySelector('#confirmed').checked){message.textContent='请先逐条审片并确认。';return}for(const field of document.querySelectorAll('textarea')){if(!field.value.trim()){message.textContent='请填写全部内容审核项。';field.focus();return}materials[Number(field.dataset.material)].samples[Number(field.dataset.sample)][field.dataset.key]=field.value.trim()}const body={human_confirmed:true,verdict:document.querySelector('#verdict').value,materials};const url=URL.createObjectURL(new Blob([JSON.stringify(body,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='content-review.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message.textContent='已导出。回到周复盘试验导入此记录，再核对是否应用。'};
</script></html>""".replace("DATA", data)
    )
    with output.open("x", encoding="utf-8") as handle:
        handle.write(page)


def _form(material, sample, labels):
    return "".join(
        f'<label>{html.escape(label)}<textarea data-material="{material}" data-sample="{sample}" data-key="{key}" required></textarea></label>'
        for key, label in labels.items()
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build_review(args.baseline, args.trial, args.output)
