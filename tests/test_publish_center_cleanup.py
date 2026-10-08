from pathlib import Path

from app.main import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_selection_bar_has_no_legacy_batch_send_but_keeps_scheduling() -> None:
    template = (PROJECT_ROOT / "app/templates/publish.html").read_text(encoding="utf-8")
    selection_bar = template.split('<div class="publish-selection-bar"', 1)[1].split("</div>", 1)[0]

    assert "data-send-selected" not in selection_bar
    assert "data-open-schedule-drawer" in selection_bar
    assert "data-apply-batch-target" in selection_bar
    assert "data-batch-ai" in selection_bar
    assert "AI 重写已选文案" in selection_bar
    assert "data-publish-now" in template


def test_publish_ai_and_maintenance_actions_are_scoped_to_content_preparation() -> None:
    template = (PROJECT_ROOT / "app/templates/publish.html").read_text(encoding="utf-8")
    publish_script = (PROJECT_ROOT / "app/static/js/publish-center.js").read_text(encoding="utf-8")
    page_heading = template.split('<section class="page-heading send-heading">', 1)[1].split(
        "</section>", 1
    )[0]
    content_panel = template.split('data-center-panel="content"', 1)[1].split(
        'data-center-panel="schedule"', 1
    )[0]
    content_row_macro = template.split("{% macro content_row(job) %}", 1)[1].split(
        "{% endmacro %}", 1
    )[0]

    assert "data-supplement-publish-jobs" not in page_heading
    assert "data-supplement-publish-jobs" in content_panel
    assert "同步遗漏切片" in content_panel
    assert "不调用 AI，也不修改已有文案" in content_panel
    assert "AI 重写本条文案" in content_row_macro
    assert "use_ai=false" in publish_script
    assert "/api/publish/jobs/metadata/upgrade-pending-douyin" not in publish_script
    assert 'batchAiButton.hidden = tab !== "content"' in publish_script


def test_legacy_publish_frontend_handlers_are_removed() -> None:
    global_script = (PROJECT_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    publish_script = (PROJECT_ROOT / "app/static/js/publish-center.js").read_text(encoding="utf-8")

    assert "data-publish-tab" not in global_script
    assert "data-send-job-form" not in global_script
    assert "data-start-send-queue" not in global_script
    assert "data-send-selected" not in publish_script


def test_current_single_send_route_remains_and_legacy_routes_are_removed() -> None:
    route_paths = set(app.openapi()["paths"])

    assert "/api/publish/jobs/{job_id}/publish-now" in route_paths
    assert "/api/publish/jobs/{job_id}/send" not in route_paths
    assert "/api/publish/send/start" not in route_paths
