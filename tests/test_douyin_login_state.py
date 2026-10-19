from contextlib import contextmanager

import pytest

from app.services.publishers import douyin
from app.services.publishers.douyin import DouyinPublisher


class Clock:
    elapsed = 0.0

    def now(self):
        return self.elapsed


class Page:
    url = "https://creator.douyin.com/creator-micro/content/upload"

    def __init__(self, clock, *, ready_at=0, text=None):
        self.clock = clock
        self.ready_at = ready_at
        self.text = text

    def locator(self, _selector):
        return self

    def count(self):
        return int(self.clock.elapsed >= self.ready_at and self.text is None)

    def wait_for_timeout(self, ms):
        self.clock.elapsed += ms / 1000


class Runtime:
    def __init__(self, page):
        self.current = page
        self.phases = []
        self.screenshots = []

    @contextmanager
    def page(self, _url):
        yield self.current

    def body_text(self, page):
        if page.text is not None:
            return page.text
        return "发布视频 上传视频" if page.count() else "作品发布 内容管理 加载中，请稍候..."

    def phase(self, phase, details):
        self.phases.append((phase, details))

    def screenshot(self, _page, name):
        self.screenshots.append(name)
        return "diagnostic.png"


def publisher(monkeypatch, **kwargs):
    clock = Clock()
    monkeypatch.setattr(douyin.time, "monotonic", clock.now)
    page = Page(clock, **kwargs)
    runtime = Runtime(page)
    return DouyinPublisher(runtime=runtime), runtime, page


def test_slow_authenticated_spa_is_not_mislabeled_expired(monkeypatch):
    service, runtime, _ = publisher(monkeypatch, ready_at=30)
    result = service.check_login("account")
    assert result["login_status"] == "normal"
    assert 30 <= result["evidence"]["elapsed_seconds"] <= 31
    assert result["evidence"]["checks"] > 2
    assert not runtime.screenshots


def test_loading_timeout_is_inconclusive_with_diagnostic(monkeypatch):
    service, runtime, _ = publisher(monkeypatch, ready_at=1000)
    result = service.check_login("account")
    assert result["login_status"] == "check_failed"
    assert result["evidence"]["reason"] == "page_not_ready"
    assert result["evidence"]["screenshot"] == "diagnostic.png"
    assert "重新登录" not in result["message"]
    assert runtime.screenshots


@pytest.mark.parametrize("url,text", [
    ("https://creator.douyin.com/login", "登录"),
    ("https://passport.douyin.com/", ""),
    (Page.url, "扫码登录 手机号登录"),
])
def test_explicit_login_page_blocks_even_with_upload_input(monkeypatch, url, text):
    service, _, page = publisher(monkeypatch, text=text)
    page.url = url
    page.count = lambda: 1
    result = service.check_login("account")
    assert result["login_status"] == "login_required"
    assert result["evidence"]["reason"] == "login_page"


def test_login_word_in_query_is_not_an_expired_session(monkeypatch):
    service, _, page = publisher(monkeypatch)
    page.url += "?enter_from=login&redirect=/login"
    result = service.check_login("account")
    assert result["login_status"] == "normal"
    assert "?" not in result["evidence"]["path"]


def test_unrecognized_page_cannot_authorize_upload(monkeypatch):
    service, _, _ = publisher(monkeypatch, text="内容管理 创作服务")
    result = service.check_login("account")
    assert result["login_status"] == "check_failed"


def test_other_origin_cannot_authorize_upload(monkeypatch):
    service, _, page = publisher(monkeypatch)
    page.url = "https://example.test/upload"
    assert service.check_login("account")["login_status"] == "check_failed"


def test_security_challenge_does_not_become_normal(monkeypatch):
    service, _, _ = publisher(monkeypatch, text="上传视频 安全验证 验证码")
    result = service.check_login("account")
    assert result["login_status"] == "check_failed"
    assert result["evidence"]["reason"] == "verification_required"


def test_manual_login_waits_for_user_instead_of_closing_login_page(monkeypatch):
    service, runtime, page = publisher(monkeypatch, ready_at=5)
    original = runtime.body_text
    runtime.body_text = lambda current: "扫码登录" if current.clock.elapsed < 5 else original(current)
    result = service.open_login("account")
    assert result["login_status"] == "normal"
    assert page.clock.elapsed >= 5
