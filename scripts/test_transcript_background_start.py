from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.task_service as task_service


class FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks = []

    def add_task(self, fn, *args, **kwargs) -> None:
        self.tasks.append((fn, args, kwargs))


def main() -> None:
    original_get_task = task_service.get_task
    original_get_artifact_paths = task_service.get_artifact_paths
    original_update_task_status = task_service.update_task_status
    original_append_task_log = task_service._append_task_log
    task_id = "background-test"

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        audio_path = temp_path / "source.wav"
        transcript_path = temp_path / "transcript.md"
        audio_path.write_bytes(b"fake audio")

        def fake_get_task(_task_id):
            return {"id": task_id, "task_name": "Background Test", "status": "transcribing"}

        def fake_get_artifact_paths(_task_id):
            return {
                "audio_path": audio_path,
                "transcript_path": transcript_path,
                "log_path": temp_path / "process.log",
            }

        try:
            task_service.get_task = fake_get_task
            task_service.get_artifact_paths = fake_get_artifact_paths
            task_service.update_task_status = lambda *_args, **_kwargs: fake_get_task(task_id)
            task_service._append_task_log = lambda *_args, **_kwargs: None
            task_service._RUNNING_TRANSCRIPT_TASKS.discard(task_id)

            background_tasks = FakeBackgroundTasks()
            result = task_service.process_task_transcript(task_id, background_tasks=background_tasks)
            duplicate = task_service.process_task_transcript(task_id, background_tasks=FakeBackgroundTasks())
            cancel = task_service.cancel_task_transcript(task_id)

            assert result["status"] == "started"
            assert len(background_tasks.tasks) == 1
            assert background_tasks.tasks[0][0] == task_service._run_task_transcript_background
            assert duplicate["status"] == "running"
            assert cancel["status"] == "cancelling"
            assert task_id in task_service._CANCEL_TRANSCRIPT_TASKS
        finally:
            task_service.get_task = original_get_task
            task_service.get_artifact_paths = original_get_artifact_paths
            task_service.update_task_status = original_update_task_status
            task_service._append_task_log = original_append_task_log
            task_service._RUNNING_TRANSCRIPT_TASKS.discard(task_id)
            task_service._CANCEL_TRANSCRIPT_TASKS.discard(task_id)

    print("transcript background start test passed")


def test_orphan_cancelling_progress_is_finalized() -> None:
    original_get_task = task_service.get_task
    original_get_artifact_paths = task_service.get_artifact_paths
    original_update_task_status = task_service.update_task_status
    original_append_task_log = task_service._append_task_log
    task_id = "orphan-cancel-test"
    updated_statuses = []

    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        transcript_path = temp_path / "transcript.md"

        def fake_get_task(_task_id):
            return {"id": task_id, "task_name": "Orphan Cancel Test", "status": "transcribing"}

        def fake_get_artifact_paths(_task_id):
            return {
                "transcript_path": transcript_path,
                "log_path": temp_path / "process.log",
            }

        def fake_update_task_status(_task_id, status, *_args, **_kwargs):
            updated_statuses.append(status)
            return fake_get_task(task_id)

        try:
            task_service.get_task = fake_get_task
            task_service.get_artifact_paths = fake_get_artifact_paths
            task_service.update_task_status = fake_update_task_status
            task_service._append_task_log = lambda *_args, **_kwargs: None
            task_service._RUNNING_TRANSCRIPT_TASKS.discard(task_id)
            task_service._CANCEL_TRANSCRIPT_TASKS.discard(task_id)

            task_service.write_transcript_progress(
                transcript_path,
                status="cancelling",
                current_chunk=1,
                total_chunks=23,
                percent=2,
                message="Stopping",
            )

            status = task_service.get_task_transcript_status(task_id)
            progress = task_service.read_transcript_progress(transcript_path)
            cancel = task_service.cancel_task_transcript(task_id)

            assert status["progress"]["status"] == "cancelled"
            assert progress["status"] == "cancelled"
            assert cancel["status"] == "not_running"
            assert task_service.TaskStatus.pending_processing in updated_statuses
        finally:
            task_service.get_task = original_get_task
            task_service.get_artifact_paths = original_get_artifact_paths
            task_service.update_task_status = original_update_task_status
            task_service._append_task_log = original_append_task_log
            task_service._RUNNING_TRANSCRIPT_TASKS.discard(task_id)
            task_service._CANCEL_TRANSCRIPT_TASKS.discard(task_id)


def run_tests() -> None:
    main()
    test_orphan_cancelling_progress_is_finalized()
    print("orphan cancelling progress test passed")


if __name__ == "__main__":
    run_tests()
