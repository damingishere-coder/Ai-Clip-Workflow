"""Human-authored Prompt-only drafts. Creation cannot activate a policy or job."""

from datetime import datetime, timezone
import difflib
import hashlib
import json
from uuid import uuid4

from app.db.database import get_connection
from app.models.content_challenger import ChallengerDraftCreate
from app.services.ai_prompt_preset_service import ensure_ai_prompt_version_with_connection
from app.services.content_intelligence_service import _read_report
from app.services.content_profile_service import active_profile, _version
from app.services.content_review_service import ContentReviewError
from app.services.review_observation_service import evidence_hash


def _fail(message, status=409):
    raise ContentReviewError(message, status_code=status)


def _report(connection, report_id):
    row = connection.execute("SELECT * FROM content_intelligence_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        _fail("来源报告不存在", 404)
    return _read_report(row)


def _baseline(connection, profile_id, preset_id=None):
    try:
        version, profile = active_profile(connection, profile_id)
    except ValueError as exc:
        _fail(str(exc))
    preset_id = preset_id or profile.prompt_preset_id
    preset = connection.execute("SELECT * FROM ai_prompt_presets WHERE id=? AND is_archived=0", (preset_id,)).fetchone()
    if not preset:
        _fail("基线 Prompt 不存在或已归档", 404)
    text = preset["prompt_text"].strip()
    value = {"profile_version_id": version["id"], "profile_sha256": version["config_sha256"],
             "profile": profile.model_dump(mode="json"), "rules_version": version["rules_version"],
             "preset_id": preset["id"], "preset_name": preset["name"], "prompt_text": text,
             "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()}
    return {**value, "sha256": evidence_hash(value)}


def draft_context(report_id, profile_id, preset_id=None):
    with get_connection() as connection:
        connection.execute("BEGIN")
        report = _report(connection, report_id)
        baseline = _baseline(connection, profile_id, preset_id)
    return {"report_id": report_id, "report_sha256": report["payload_sha256"], "baseline": baseline,
            "notice": "基线是你当前选择的正式方案，不追认未知历史。草稿只改变 Prompt；Profile、评分权重和硬门槛完整冻结并保持不变。"}


def _prompt_version(connection, version_id):
    row = connection.execute("SELECT * FROM ai_prompt_versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        _fail("Challenger 引用的 Prompt 版本缺失")
    value = dict(row)
    if hashlib.sha256(value["prompt_text"].strip().encode()).hexdigest() != value["prompt_sha256"]:
        _fail("Prompt 版本证据损坏")
    return value


def read_challenger_with_connection(connection, challenger_id):
    row = connection.execute("SELECT * FROM content_strategy_challengers WHERE id=?", (challenger_id,)).fetchone()
    if not row:
        _fail("Challenger 草稿不存在", 404)
    row = dict(row)
    report = _report(connection, row["report_id"])
    if report["payload_sha256"] != row["report_sha256"]:
        _fail("Challenger 来源报告校验失败")
    try:
        evidence = json.loads(row["evidence_json"])
        if evidence_hash(evidence) != row["evidence_sha256"]:
            _fail("Challenger 草稿证据损坏")
        profile_version, profile = _version(connection, row["profile_version_id"])
        champion = _prompt_version(connection, row["champion_prompt_version_id"])
        challenger = _prompt_version(connection, row["challenger_prompt_version_id"])
        baseline = evidence["baseline"]
        consistent = (baseline["profile"] == profile.model_dump(mode="json")
            and baseline["profile_version_id"] == profile_version["id"]
            and baseline["profile_sha256"] == profile_version["config_sha256"]
            and baseline["rules_version"] == profile_version["rules_version"]
            and baseline["prompt_text"] == champion["prompt_text"]
            and baseline["prompt_sha256"] == champion["prompt_sha256"]
            and baseline["preset_id"] == champion["preset_id"]
            and evidence["challenger_prompt_sha256"] == challenger["prompt_sha256"]
            and evidence["challenger_prompt_text"] == challenger["prompt_text"]
            and challenger["preset_id"] == row["challenger_preset_id"]
            and evidence["report_id"] == row["report_id"] and evidence["report_sha256"] == row["report_sha256"])
        if not consistent:
            _fail("Challenger 草稿与实际版本证据不一致")
    except (ValueError, TypeError, KeyError) as exc:
        _fail(f"Challenger 草稿证据无法读取：{exc}")
    return {key: value for key, value in row.items() if key not in {"evidence_json", "request_key", "request_sha256"}} | {"evidence": evidence}


def create_draft(payload: ChallengerDraftCreate):
    request_sha = evidence_hash(payload.model_dump(mode="json"))
    name, hypothesis, text = payload.name.strip(), payload.hypothesis.strip(), payload.prompt_text.strip()
    if not name or not hypothesis or not text:
        _fail("请填写草稿名称、待验证假设和 Prompt 正文", 422)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT id,request_sha256 FROM content_strategy_challengers WHERE request_key=?", (str(payload.request_key),)).fetchone()
        if existing:
            if existing["request_sha256"] != request_sha:
                _fail("重复请求的草稿内容不一致")
            return read_challenger_with_connection(connection, existing["id"])
        report = _report(connection, payload.report_id)
        if report["payload_sha256"] != payload.report_sha256:
            _fail("来源报告已不匹配，请重新打开报告")
        baseline = _baseline(connection, payload.profile_id, payload.champion_preset_id)
        if baseline["sha256"] != payload.baseline_sha256:
            _fail("正式策略在预览后已改变，请重新核对差异")
        if text == baseline["prompt_text"]:
            _fail("草稿与当前 Prompt 相同，没有可验证的改动", 422)
        now = datetime.now(timezone.utc).isoformat()
        champion = ensure_ai_prompt_version_with_connection(connection, preset_id=baseline["preset_id"],
            preset_name=baseline["preset_name"], prompt_text=baseline["prompt_text"], now=now)
        challenger_id = uuid4().hex
        preset_id = f"challenger_{challenger_id}"
        slot = connection.execute("SELECT COALESCE(MAX(slot),0)+1 FROM ai_prompt_presets").fetchone()[0]
        # Archived means the draft cannot silently appear in normal production
        # selectors. Future trial activation is a separate explicit action.
        connection.execute("""INSERT INTO ai_prompt_presets
            (id,slot,name,prompt_text,is_default,is_archived,created_at,updated_at) VALUES(?,?,?,?,0,1,?,?)""",
            (preset_id, slot, f"试验草稿 · {name}", text, now, now))
        challenger = ensure_ai_prompt_version_with_connection(connection, preset_id=preset_id,
            preset_name=f"试验草稿 · {name}", prompt_text=text, now=now)
        diff = "\n".join(difflib.unified_diff(baseline["prompt_text"].splitlines(), text.splitlines(),
            fromfile="当前正式 Prompt", tofile="Challenger 草稿", lineterm=""))
        evidence = {"schema_version": "prompt-challenger-v1", "name": name, "hypothesis": hypothesis,
            "author_source": "explicit_human_input", "report_id": payload.report_id,
            "report_sha256": payload.report_sha256, "baseline": baseline,
            "baseline_basis": "explicitly_selected_current_strategy", "profile_scoring_changed": False,
            "challenger_prompt_text": text, "challenger_prompt_sha256": challenger["prompt_sha256"],
            "diff": diff, "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
            "performance_status": report["report"]["performance"]["status"],
            "notice": "假设未被验证。草稿不代表报告证明了改动有效，不会自动修改正式策略或建立作品实验。"}
        connection.execute("""INSERT INTO content_strategy_challengers
            (id,report_id,report_sha256,profile_version_id,champion_prompt_version_id,challenger_prompt_version_id,
             challenger_preset_id,request_key,request_sha256,evidence_json,evidence_sha256,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (challenger_id, payload.report_id, payload.report_sha256,
            baseline["profile_version_id"], champion["id"], challenger["id"], preset_id, str(payload.request_key), request_sha,
            json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")), evidence_hash(evidence), now, now))
        result = read_challenger_with_connection(connection, challenger_id)
        connection.commit()
        return result


def get_challenger(challenger_id):
    with get_connection() as connection:
        connection.execute("BEGIN")
        return read_challenger_with_connection(connection, challenger_id)


def list_challengers(report_id, limit=30):
    with get_connection() as connection:
        return [dict(r) for r in connection.execute("""SELECT id,report_id,status,created_at,evidence_sha256
            FROM content_strategy_challengers WHERE report_id=? ORDER BY created_at DESC,id DESC LIMIT ?""", (report_id, limit))]
