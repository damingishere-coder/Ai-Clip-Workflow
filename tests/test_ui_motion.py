from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_TEMPLATE = PROJECT_ROOT / "app" / "templates" / "base.html"
MOTION_SCRIPT = PROJECT_ROOT / "app" / "static" / "js" / "motion.js"
APP_SCRIPT = PROJECT_ROOT / "app" / "static" / "js" / "app.js"
PUBLISH_SCRIPT = PROJECT_ROOT / "app" / "static" / "js" / "publish-center.js"
STYLESHEET = PROJECT_ROOT / "app" / "static" / "css" / "styles.css"


def test_base_loads_local_motion_layer_after_page_scripts() -> None:
    template = BASE_TEMPLATE.read_text(encoding="utf-8")

    assert 'data-page="{{ active_page or \'\' }}"' in template
    assert "js/motion.js" in template
    assert template.index("{% block extra_scripts %}") < template.index("js/motion.js")
    motion_script_line = next(line for line in template.splitlines() if "js/motion.js" in line)
    assert "http://" not in motion_script_line
    assert "https://" not in motion_script_line


def test_motion_profiles_cover_every_rendered_page_family() -> None:
    script = MOTION_SCRIPT.read_text(encoding="utf-8")

    expected_profiles = {
        'name: "dashboard"',
        'name: "new-task"',
        'name: "tasks"',
        'name: "task-detail"',
        'name: "transcript"',
        'name: "clips"',
        'name: "clip-review"',
        'name: "subtitles"',
        'name: "subtitle-task"',
        'name: "publish"',
        'name: "system"',
    }

    for profile in expected_profiles:
        assert profile in script


def test_motion_layer_is_progressive_and_presentation_only() -> None:
    script = MOTION_SCRIPT.read_text(encoding="utf-8")

    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in script
    assert "IntersectionObserver" in script
    assert "MutationObserver" in script
    assert "body.dataset.motionPage" in script
    assert "if (nextValue === lastValue) return" in script
    assert "#runtime-log-lines" not in script
    assert "fetch(" not in script
    assert "apiFetch" not in script
    assert "innerHTML" not in script
    assert "localStorage" not in script


def test_styles_disable_nonessential_motion_when_user_requests_it() -> None:
    styles = STYLESHEET.read_text(encoding="utf-8")

    assert "@media print, (prefers-reduced-motion: reduce)" in styles
    assert "animation-duration: 1ms !important" in styles
    assert "transition-duration: 1ms !important" in styles
    assert ".send-publishing-spinner" in styles
    assert "@keyframes motion-drawer-in" in styles


def test_reduced_motion_also_disables_scripted_smooth_scrolling() -> None:
    app_script = APP_SCRIPT.read_text(encoding="utf-8")
    publish_script = PUBLISH_SCRIPT.read_text(encoding="utf-8")

    assert "function preferredScrollBehavior()" in app_script
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in app_script
    assert "window.preferredScrollBehavior = preferredScrollBehavior" in app_script
    assert 'behavior: "smooth"' not in app_script
    assert 'behavior: "smooth"' not in publish_script
    assert publish_script.count("window.preferredScrollBehavior()") == 7
