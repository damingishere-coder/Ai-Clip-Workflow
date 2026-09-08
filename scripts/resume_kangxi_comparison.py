"""恢复已暂停的隔离对照；不确定请求须显式确认，每个单元最多确认补跑一次。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--previous-process-id", required=True, type=int)
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--receipt-task-id", help="诊断捕获文件对应的明确原任务 ID")
    parser.add_argument("--config-env", type=Path)
    parser.add_argument("--material-manifest", type=Path)
    parser.add_argument("--confirm-uncertain", action="store_true")
    args = parser.parse_args()
    root = args.directory.resolve()
    snapshot = root / "data" / "workflow.sqlite3"
    if not snapshot.is_file() or not (root / "prompt.txt").is_file():
        parser.error("不是已建立的隔离对照目录")
    if os.name == "nt":
        alive = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"if (Get-Process -Id {args.previous_process_id} -ErrorAction SilentlyContinue) {{ exit 1 }}",
            ],
            capture_output=True,
        )
        if alive.returncode:
            parser.error("旧隔离进程仍存在，拒绝接管")
    if args.config_env:
        for line in args.config_env.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in {
                "AI_CODEX_PATH",
                "AI_CODEX_HOME",
                "AI_CODEX_MODEL",
                "AI_CODEX_TIMEOUT_SECONDS",
            }:
                os.environ[key.strip()] = value.strip().strip('"').strip("'")
    os.environ.update(
        {
            "DATA_DIR": str(root / "data"),
            "DATABASE_PATH": str(snapshot),
            "STORAGE_ROOT": str(root / "storage"),
            "TASKS_DIR": str(root / "storage"),
            "UPLOAD_TEMP_DIR": str(root / "uploads"),
            "PUBLISH_SCHEDULER_EXPORT_DIR": str(root / "exports"),
            "NIUMA_WEEKLY_REVIEW_RUNNER_ENABLED": "false",
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.db import database
    from app.core.config import settings
    from app.services import job_service
    from app.services.ai import content_decision_analyzer as analyzer
    from app.services.ai.variety_comedy_analyzer import ComedyAnalysisRequest
    from scripts.kangxi_materials import resolve_materials

    if (
        settings.database_path.resolve() != snapshot
        or database.settings.database_path.resolve() != snapshot
    ):
        raise RuntimeError("隔离路径不符")
    lock = root / "resume.lock"
    with lock.open("x", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        database.create_schema_migration_backup(
            snapshot, root / "data" / "backups", "before-isolated-resume"
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prompt = (root / "prompt.txt").read_text(encoding="utf-8")
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        manifest = {
            "isolation": True,
            "prompt_sha256": prompt_hash,
            "contract": analyzer.CONTRACT_VERSION,
            "tasks": [],
        }
        with database.get_connection() as connection:
            tasks = [
                dict(
                    connection.execute(
                        "SELECT * FROM tasks WHERE id=?", (task,)
                    ).fetchone()
                )
                for task in args.task
            ]
        for task in tasks:
            task_id = task["id"]
            folder = root / task_id
            folder.mkdir(exist_ok=True)
            video, source_transcript = resolve_materials(
                task, manifest_path=args.material_manifest
            )
            transcript = folder / "transcript.md"
            if transcript.is_file():
                if transcript.read_bytes() != source_transcript.read_bytes():
                    raise RuntimeError("转写已变化，旧结果不能复用")
            else:
                transcript.write_bytes(source_transcript.read_bytes())
            with database.get_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                old = connection.execute(
                    "SELECT * FROM workflow_jobs WHERE task_id=? AND payload_json LIKE '%isolated_content_comparison%' ORDER BY created_at DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                if old:
                    job_id = old["id"]
                    cp = json.loads(old["checkpoint_json"] or "{}")
                    namespaces = cp.get("_ai_analysis_units_v1", {}).get(
                        "namespaces", {}
                    )
                    receipts = {}
                    if args.receipt_task_id == task_id:
                        for path in [
                            *root.glob("captured-*.json"),
                            *root.glob("paused-*-response.json"),
                            *(root / "captured-responses").glob("*.json"),
                        ]:
                            raw = json.loads(path.read_text(encoding="utf-8"))
                            if len(raw.get("clips", [])) == 1:
                                receipts[raw["clips"][0]["source_id"]] = (path, raw)
                    for namespace, state in namespaces.items():
                        for unit_id, unit in state.get("units", {}).items():
                            if unit.get("status") not in {"uncertain", "running"}:
                                continue
                            if (
                                namespace == "content_global_judge"
                                and unit_id in receipts
                                and not unit.get("rejected_response_json")
                            ):
                                path, raw = receipts[unit_id]
                                serialized = json.dumps(
                                    raw,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                state.setdefault("receipt_imports", []).append(
                                    {
                                        "unit_id": unit_id,
                                        "previous_state": dict(unit),
                                        "path": str(path),
                                        "sha256": hashlib.sha256(
                                            path.read_bytes()
                                        ).hexdigest(),
                                        "at": stamp,
                                    }
                                )
                                unit.update(
                                    status="uncertain",
                                    rejected_response_json=serialized,
                                    rejected_response_checksum=hashlib.sha256(
                                        serialized.encode()
                                    ).hexdigest(),
                                )
                    connection.execute(
                        "UPDATE workflow_jobs SET status='queued',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,cancel_requested=0,attempt_count=0,checkpoint_json=? WHERE id=?",
                        (json.dumps(cp, ensure_ascii=False), job_id),
                    )
                    connection.commit()
                else:
                    connection.commit()
                    job_id = job_service.create_job(
                        task_id,
                        job_service.JOB_TYPE_AI_ANALYSIS,
                        payload={"isolated_content_comparison": True},
                    )["id"]
            claim = job_service.claim_job(
                job_id, "isolated-resume", lease_seconds=14400
            )
            if not claim:
                raise RuntimeError("无法取得隔离 Job 租约")
            original_execute = analyzer.execute_checkpointed_ai_unit

            def execute(**kwargs):
                print(
                    json.dumps(
                        {
                            "task": task_id,
                            "stage": kwargs["namespace"],
                            "unit": kwargs["unit_id"],
                            "status": "checking",
                        }
                    ),
                    flush=True,
                )
                if args.confirm_uncertain:
                    with database.get_connection() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        current = job_service.require_job_lease_with_connection(
                            connection,
                            task_id=task_id,
                            allowed_job_types={job_service.JOB_TYPE_AI_ANALYSIS},
                        )
                        cp = current["checkpoint_json"]
                        state = (
                            cp.get("_ai_analysis_units_v1", {})
                            .get("namespaces", {})
                            .get(kwargs["namespace"], {})
                        )
                        unit = state.get("units", {}).get(kwargs["unit_id"], {})
                        valid_receipt = False
                        if (
                            unit.get("rejected_response_json")
                            and unit.get("request_fingerprint")
                            == kwargs["request_fingerprint"]
                        ):
                            try:
                                raw = unit["rejected_response_json"]
                                if hashlib.sha256(raw.encode()).hexdigest() == unit.get(
                                    "rejected_response_checksum"
                                ):
                                    kwargs["validate_payload"](json.loads(raw))
                                    valid_receipt = True
                            except Exception:
                                pass
                        previous_retries = state.setdefault("confirmed_retries", [])
                        if (
                            unit.get("status") in {"running", "uncertain"}
                            and not valid_receipt
                            and not any(
                                r["unit_id"] == kwargs["unit_id"]
                                for r in previous_retries
                            )
                        ):
                            previous_retries.append(
                                {
                                    "unit_id": kwargs["unit_id"],
                                    "at": stamp,
                                    "previous_state": dict(unit),
                                    "request_fingerprint": kwargs[
                                        "request_fingerprint"
                                    ],
                                }
                            )
                            unit["status"] = "retryable_failed"
                            connection.execute(
                                "UPDATE workflow_jobs SET checkpoint_json=? WHERE id=?",
                                (json.dumps(cp, ensure_ascii=False), job_id),
                            )
                        connection.commit()
                result = original_execute(**kwargs)
                print(
                    json.dumps(
                        {
                            "task": task_id,
                            "stage": kwargs["namespace"],
                            "unit": kwargs["unit_id"],
                            "status": result.status,
                            "reused": result.reused,
                            "error": result.error,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return result

            analyzer.execute_checkpointed_ai_unit = execute
            try:
                with job_service.job_lease_context(
                    job_id, "isolated-resume", claim["lease_token"]
                ):
                    result = analyzer.analyze_content_decisions(
                        ComedyAnalysisRequest(
                            task_id,
                            transcript,
                            video.parent.parent / "audio" / "source.wav",
                            12,
                            5,
                            task.get("ai_preference") or "",
                            "codex",
                            prompt,
                            "content",
                        )
                    )
                    if result.analysis_meta.get("analysis_incomplete"):
                        job_service.mark_job_failed(job_id, "隔离分析仍有待确认单元")
                    else:
                        job_service.mark_job_completed(
                            job_id,
                            {"isolated": True, "analysis_meta": result.analysis_meta},
                        )
            finally:
                analyzer.execute_checkpointed_ai_unit = original_execute
            payload = result.model_dump()
            payload["analysis_meta"].update(
                prompt_sha256=prompt_hash,
                transcript_sha256=hashlib.sha256(transcript.read_bytes()).hexdigest(),
                model=settings.ai_codex_model,
            )
            result_path = folder / f"result-{stamp}.json"
            result_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            manifest["tasks"].append(
                {
                    "task_id": task_id,
                    "video": str(video),
                    "transcript_sha256": payload["analysis_meta"]["transcript_sha256"],
                    "result": str(result_path),
                    "result_sha256": hashlib.sha256(
                        result_path.read_bytes()
                    ).hexdigest(),
                    "job_id": job_id,
                    "analysis_meta": payload["analysis_meta"],
                }
            )
            (root / f"manifest-{stamp}.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(manifest["tasks"][-1], ensure_ascii=False), flush=True)
        return int(
            any(t["analysis_meta"]["analysis_incomplete"] for t in manifest["tasks"])
        )
    finally:
        lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
