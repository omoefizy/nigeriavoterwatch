"""
IReV scraper — per-host failure memory.

A host that fails DNS/connection three times in a row within a run is skipped
for the rest of that run; any HTTP response from the host resets its count.
"""
import httpx
import pytest

from app.scrapers import irev_scraper as irev
from app.scrapers.irev_scraper import IRevScraper

PDF = b"%PDF-1.4 test body"


def _scraper(handler, calls: list[str]) -> IRevScraper:
    def recording(request: httpx.Request):
        calls.append(request.url.host)
        return handler(request)

    s = IRevScraper(election_ids=["irev-test-election"])
    s._throttle = irev._Throttle(0)
    s._http = httpx.AsyncClient(transport=httpx.MockTransport(recording),
                                follow_redirects=False)
    return s


def _dns_fail(request: httpx.Request):
    raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known",
                             request=request)


@pytest.mark.asyncio
async def test_host_skipped_after_three_consecutive_connection_failures():
    calls: list[str] = []

    def handler(request: httpx.Request):
        if request.url.host == "dead.example":
            _dns_fail(request)
        return httpx.Response(200, content=PDF)

    s = _scraper(handler, calls)
    try:
        for i in range(3):
            r = await s._download(f"https://dead.example/{i}.pdf", None)
            assert not r.ok and "ConnectError" in r.reason
        assert "dead.example" in s.skipped_hosts

        # 4th and later URLs on the dead host are not requested at all
        r = await s._download("https://dead.example/3.pdf", None)
        assert not r.ok and "skipped" in r.reason
        assert calls.count("dead.example") == 3

        # Other hosts are unaffected
        r = await s._download("https://good.example/0.pdf", None)
        assert r.ok and r.content_type == "application/pdf"
    finally:
        await s._http.aclose()


@pytest.mark.asyncio
async def test_http_response_resets_host_failure_count():
    calls: list[str] = []
    fail = {"on": True}

    def handler(request: httpx.Request):
        if fail["on"]:
            _dns_fail(request)
        return httpx.Response(404)

    s = _scraper(handler, calls)
    try:
        for i in range(2):
            await s._download(f"https://flaky.example/{i}.pdf", None)
        fail["on"] = False
        r = await s._download("https://flaky.example/ok.pdf", None)  # 404 — host answered
        assert r.http_status == 404
        fail["on"] = True
        for i in range(2):
            await s._download(f"https://flaky.example/again{i}.pdf", None)

        # 2 failures, reset, 2 failures — never 3 in a row
        assert "flaky.example" not in s.skipped_hosts
        assert calls.count("flaky.example") == 5
    finally:
        await s._http.aclose()
