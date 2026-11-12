import httpx
import pytest
from common.page_cache import get_cache
from agent.registry import execute



class FakeResp:
    def __init__(self, status_code: int, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"fake {self.status_code}", request=None, response=None
            )
def test_fecth_page_429(monkeypatch):
    def fack_get(url, **kwargs):
        return FakeResp(429, headers={"Retry-After": "5"})
    monkeypatch.setattr("agent.tools.httpx.get", fack_get)
    r = execute("fetch_page", {"url": "http://example.test/"})
    assert r["ok"] is False
    assert r["retryable"] is True
    assert r["retry_after"] == 5

def test_fetch_page_404(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeResp(404)

    monkeypatch.setattr("agent.tools.httpx.get", fake_get)

    r = execute("fetch_page", {"url": "http://example.test/"})

    assert r["ok"] is False
    assert r["retryable"] is False
HTML = "<html>hi</html>"


def test_fetch_page_ok(monkeypatch):
    def fake_get(url, **kwargs):
        return FakeResp(200, text=HTML)

    monkeypatch.setattr("agent.tools.httpx.get", fake_get)

    r = execute("fetch_page", {"url": "http://example.test/"})

    assert r["ok"] is True
    assert set(r["result"]) == {"ref", "url", "chars"}
    assert r["result"]["chars"] == len(HTML)


@pytest.fixture(autouse=True)
def clean_cache():
    get_cache()._store.clear()
    get_cache()._url_index.clear()
    yield
