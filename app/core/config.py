from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    app_name: str = "Live Streaming Slicing Workflow"
    app_name_cn: str = "直播切片工作流"
    app_description: str = "Windows 本地直播长视频自动切片工作流系统"
    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    tasks_dir: Path = PROJECT_ROOT / "tasks"
    database_path: Path = PROJECT_ROOT / "data" / "workflow.sqlite3"
    ui_reference_image: Path = (
        PROJECT_ROOT
        / "docs"
        / "design"
        / "live_streaming_slicing_workflow_ui_16x9.png"
    )
    default_max_clip_minutes: int = 2
    default_candidate_count: int = 8


settings = Settings()
