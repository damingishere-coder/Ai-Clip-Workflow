"""Versioned step checkpoints for the persistent auto pipeline."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from app.services import job_service


AUTO_PIPELINE_CHECKPOINT_KIND = "auto_pipeline_step_v1"
_STEP_STATES = {"running", "succeeded", "failed"}


class PipelineCheckpointError(RuntimeError):
    """The persisted auto-pipeline checkpoint cannot be trusted."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AutoPipelineCheckpoint:
    """Small, fenced state machine stored in workflow_jobs.checkpoint_json."""

    def __init__(
        self,
        *,
        job_id: str,
        task_id: str,
        start_step: str,
        run_key: str,
        ordered_steps: Iterable[str],
        state: dict[str, Any],
    ) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self.start_step = start_step
        self.run_key = run_key
        self.ordered_steps = tuple(ordered_steps)
        self.scoped_steps = self.ordered_steps[self.ordered_steps.index(start_step) :]
        self.state = state
        self._validate()

    @classmethod
    def load(
        cls,
        *,
        job_id: str,
        task_id: str,
        start_step: str,
        run_key: str,
        ordered_steps: Iterable[str],
    ) -> AutoPipelineCheckpoint:
        active = job_service.require_active_job_lease()
        if not active or active[0] != job_id:
            raise job_service.JobLeaseLostError(
                f"自动流水线没有当前 Workflow Job 租约：{job_id}"
            )
        job = job_service.get_job(job_id)
        if not job or job.get("job_type") != job_service.JOB_TYPE_AUTO_PIPELINE:
            raise PipelineCheckpointError("自动流水线 checkpoint 对应的 Job 不存在或类型不正确")
        if str(job.get("task_id") or "") != task_id:
            raise PipelineCheckpointError("自动流水线 checkpoint 的 Task 与 Job 不一致")

        raw = job.get("checkpoint_json")
        if raw in (None, "", {}):
            state = {
                "kind": AUTO_PIPELINE_CHECKPOINT_KIND,
                "task_id": task_id,
                "run_key": run_key,
                "start_step": start_step,
                "current_step": "",
                "completed_steps": [],
                "steps": {},
                "last_error": "",
                "updated_at": _now_iso(),
            }
        elif not isinstance(raw, dict):
            raise PipelineCheckpointError("自动流水线 checkpoint JSON 已损坏，拒绝从头重复执行")
        else:
            state = deepcopy(raw)

        return cls(
            job_id=job_id,
            task_id=task_id,
            start_step=start_step,
            run_key=run_key,
            ordered_steps=ordered_steps,
            state=state,
        )

    @property
    def has_history(self) -> bool:
        return bool(self.state.get("steps") or self.state.get("completed_steps"))

    @property
    def current_step(self) -> str:
        return str(self.state.get("current_step") or "")

    @property
    def completed_steps(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.state.get("completed_steps") or [])

    def is_completed(self, step: str) -> bool:
        return step in self.completed_steps

    def step_record(self, step: str) -> dict[str, Any]:
        record = (self.state.get("steps") or {}).get(step) or {}
        return deepcopy(record) if isinstance(record, dict) else {}

    def begin_step(self, step: str, *, baseline: dict[str, Any] | None = None) -> None:
        self._require_next_step(step)
        now = _now_iso()
        previous = self.step_record(step)
        attempts = int(previous.get("attempts") or 0) + 1
        self.state["current_step"] = step
        self.state.setdefault("steps", {})[step] = {
            "state": "running",
            "attempts": attempts,
            "started_at": now,
            "baseline": deepcopy(baseline or {}),
            "outputs": {},
            "error": "",
        }
        self.state["last_error"] = ""
        self._persist()

    def complete_step(
        self,
        step: str,
        *,
        outputs: dict[str, Any],
        recovered: bool = False,
    ) -> None:
        self._require_next_step(step)
        previous = self.step_record(step)
        now = _now_iso()
        self.state.setdefault("steps", {})[step] = {
            "state": "succeeded",
            "attempts": max(1, int(previous.get("attempts") or 0)),
            "started_at": str(previous.get("started_at") or now),
            "completed_at": now,
            "recovered": bool(recovered),
            "baseline": deepcopy(previous.get("baseline") or {}),
            "outputs": deepcopy(outputs),
            "error": "",
        }
        completed = list(self.completed_steps)
        completed.append(step)
        self.state["completed_steps"] = completed
        self.state["current_step"] = ""
        self.state["last_error"] = ""
        self._persist()

    def fail_step(self, step: str, error: str) -> None:
        self._require_next_step(step)
        previous = self.step_record(step)
        now = _now_iso()
        self.state.setdefault("steps", {})[step] = {
            "state": "failed",
            "attempts": max(1, int(previous.get("attempts") or 0)),
            "started_at": str(previous.get("started_at") or now),
            "failed_at": now,
            "baseline": deepcopy(previous.get("baseline") or {}),
            "outputs": deepcopy(previous.get("outputs") or {}),
            "error": str(error or "自动流水线步骤失败"),
        }
        self.state["current_step"] = step
        self.state["last_error"] = str(error or "自动流水线步骤失败")
        self._persist()

    def invalidate_from(self, step: str, error: str) -> None:
        if step not in self.scoped_steps:
            raise PipelineCheckpointError(f"未知的自动流水线步骤：{step}")
        index = self.scoped_steps.index(step)
        retained = list(self.scoped_steps[:index])
        if tuple(retained) != self.completed_steps[:index]:
            raise PipelineCheckpointError("自动流水线 checkpoint 的完成顺序已损坏")
        previous = self.step_record(step)
        self.state["completed_steps"] = retained
        steps = self.state.setdefault("steps", {})
        for later in self.scoped_steps[index + 1 :]:
            steps.pop(later, None)
        steps[step] = {
            "state": "failed",
            "attempts": max(1, int(previous.get("attempts") or 0)),
            "started_at": str(previous.get("started_at") or _now_iso()),
            "failed_at": _now_iso(),
            "baseline": deepcopy(previous.get("baseline") or {}),
            "outputs": deepcopy(previous.get("outputs") or {}),
            "error": str(error),
        }
        self.state["current_step"] = step
        self.state["last_error"] = str(error)
        self._persist()

    def _require_next_step(self, step: str) -> None:
        if step not in self.scoped_steps:
            raise PipelineCheckpointError(f"未知的自动流水线步骤：{step}")
        completed = self.completed_steps
        expected = self.scoped_steps[len(completed)] if len(completed) < len(self.scoped_steps) else ""
        if expected != step:
            raise PipelineCheckpointError(
                f"自动流水线 checkpoint 顺序冲突：期望 {expected or '全部完成'}，收到 {step}"
            )

    def _persist(self) -> None:
        self.state["updated_at"] = _now_iso()
        self._validate()
        updated = job_service.update_job_checkpoint(self.job_id, self.state)
        if not updated:
            raise job_service.JobLeaseLostError(
                f"自动流水线 checkpoint 写入失败，Workflow Job 租约已失效：{self.job_id}"
            )

    def _validate(self) -> None:
        if self.start_step not in self.ordered_steps:
            raise PipelineCheckpointError(f"自动流水线起始步骤无效：{self.start_step}")
        if self.state.get("kind") != AUTO_PIPELINE_CHECKPOINT_KIND:
            raise PipelineCheckpointError("自动流水线 checkpoint 版本未知，拒绝静默重跑")
        if str(self.state.get("task_id") or "") != self.task_id:
            raise PipelineCheckpointError("自动流水线 checkpoint 属于其他 Task")
        if str(self.state.get("run_key") or "") != self.run_key:
            raise PipelineCheckpointError("任务输入或自动配置已变化，请创建新的流水线 Job")
        if str(self.state.get("start_step") or "") != self.start_step:
            raise PipelineCheckpointError("自动流水线 checkpoint 的起始步骤与 Job payload 不一致")

        completed = list(self.completed_steps)
        if completed != list(self.scoped_steps[: len(completed)]):
            raise PipelineCheckpointError("自动流水线 checkpoint 的完成步骤不是连续前缀")
        steps = self.state.get("steps")
        if not isinstance(steps, dict):
            raise PipelineCheckpointError("自动流水线 checkpoint.steps 不是对象")
        unknown = set(steps) - set(self.scoped_steps)
        if unknown:
            raise PipelineCheckpointError(f"自动流水线 checkpoint 包含未知步骤：{sorted(unknown)}")
        for step, record in steps.items():
            if not isinstance(record, dict) or record.get("state") not in _STEP_STATES:
                raise PipelineCheckpointError(f"自动流水线 checkpoint 步骤状态无效：{step}")
            if step in completed and record.get("state") != "succeeded":
                raise PipelineCheckpointError(f"自动流水线已完成步骤缺少 succeeded 证据：{step}")
            if not isinstance(record.get("outputs") or {}, dict):
                raise PipelineCheckpointError(f"自动流水线步骤输出证据无效：{step}")
        current = self.current_step
        if current and current not in self.scoped_steps:
            raise PipelineCheckpointError(f"自动流水线 current_step 无效：{current}")
        if current and current in completed:
            raise PipelineCheckpointError("自动流水线 current_step 与 completed_steps 冲突")
        if current:
            expected = self.scoped_steps[len(completed)] if len(completed) < len(self.scoped_steps) else ""
            if current != expected:
                raise PipelineCheckpointError("自动流水线 current_step 不是首个未完成步骤")
        expected_records = set(completed)
        if current:
            expected_records.add(current)
        if set(steps) != expected_records:
            raise PipelineCheckpointError("自动流水线 checkpoint 包含缺失或越序的步骤记录")
