from pathlib import Path

from app.main import app
from scripts.backup_restore import APP_VERSION
from scripts.publish_host_worker import create_worker_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "2.1.0"


def test_runtime_versions_match_release_version() -> None:
    assert (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip() == EXPECTED_VERSION
    assert app.version == EXPECTED_VERSION
    assert APP_VERSION == EXPECTED_VERSION
    assert create_worker_app("test-token").version == EXPECTED_VERSION


def test_readme_badges_and_release_gate_match_release_version() -> None:
    badge = f"version-{EXPECTED_VERSION}"
    assert badge in (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert badge in (PROJECT_ROOT / "README.en.md").read_text(encoding="utf-8")

    release_gate = (PROJECT_ROOT / "scripts" / "release_gate.ps1").read_text(encoding="utf-8")
    escaped_version = EXPECTED_VERSION.replace(".", r"\.")
    assert f'version="{escaped_version}"' in release_gate
    assert f"## {EXPECTED_VERSION} - " in (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
