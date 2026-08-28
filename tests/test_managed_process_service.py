from __future__ import annotations

from types import SimpleNamespace
import subprocess
from unittest.mock import Mock

import pytest

from app.services import managed_process_service
from app.services.managed_process_service import ProcessTerminationError, terminate_process_tree


def _windows(monkeypatch) -> None:
    monkeypatch.setattr(managed_process_service, "os", SimpleNamespace(name="nt"))


def test_terminate_returns_immediately_for_finished_process(monkeypatch):
    process = Mock(pid=123)
    process.poll.return_value = 0
    run = Mock()
    monkeypatch.setattr(managed_process_service.subprocess, "run", run)

    terminate_process_tree(process)

    run.assert_not_called()


def test_windows_terminate_waits_for_confirmed_exit(monkeypatch):
    _windows(monkeypatch)
    process = Mock(pid=123)
    process.poll.return_value = None
    process.wait.return_value = 1
    monkeypatch.setattr(
        managed_process_service.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, stderr=b"")),
    )

    terminate_process_tree(process)

    process.wait.assert_called_once_with(timeout=5)


def test_windows_terminate_accepts_race_when_process_already_exited(monkeypatch):
    _windows(monkeypatch)
    process = Mock(pid=123)
    process.poll.side_effect = [None, 0]
    process.wait.return_value = 0
    monkeypatch.setattr(
        managed_process_service.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 128, stderr=b"not found")),
    )

    terminate_process_tree(process)

    process.wait.assert_called_once_with(timeout=5)


def test_windows_terminate_rejects_nonzero_taskkill_for_live_process(monkeypatch):
    _windows(monkeypatch)
    process = Mock(pid=123)
    process.poll.side_effect = [None, None]
    monkeypatch.setattr(
        managed_process_service.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 5, stderr=b"access denied")),
    )

    with pytest.raises(ProcessTerminationError, match="退出码 5"):
        terminate_process_tree(process)

    process.wait.assert_not_called()


def test_windows_terminate_reports_taskkill_timeout(monkeypatch):
    _windows(monkeypatch)
    process = Mock(pid=123)
    process.poll.side_effect = [None, None]
    monkeypatch.setattr(
        managed_process_service.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("taskkill", 15)),
    )

    with pytest.raises(ProcessTerminationError, match="无法执行 taskkill"):
        terminate_process_tree(process)


def test_windows_terminate_reports_process_that_does_not_exit(monkeypatch):
    _windows(monkeypatch)
    process = Mock(pid=123)
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("process", 5)
    monkeypatch.setattr(
        managed_process_service.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, stderr=b"")),
    )

    with pytest.raises(ProcessTerminationError, match="仍未退出"):
        terminate_process_tree(process)
