"""
In-app scheduler shutdown: the app lifespan stops a running scheduled IReV
scrape through request_stop() (flush + INTERRUPTED) before the scheduler
shutdown cancels its task.
"""
import asyncio
import hashlib

import pytest

from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus
from app.scrapers import irev_scraper as irev
from app.scrapers.irev_scraper import IRevScraper, _DownloadResult, _PendingUnit
from app.services import anomaly_detector
from app.tasks import irev_tasks, scheduler as sched_mod

ELECTION = "irev-scheduler-shutdown-election"


def _unit(i: int) -> _PendingUnit:
    return _PendingUnit(
        election_id=ELECTION, election_name="test", state_code="24", state_name="LAGOS",
        lga_code="18", lga_name="OSHODI/ISOLO", ward_code="02", ward_name="ORILE-OSHODI",
        pu_code=f"24/18/02/{i:03d}", pu_name=f"PU {i}", portal_url="https://example.invalid",
        document={"document_url": f"https://example.invalid/{i}.pdf"},
    )


def _ok(url: str) -> _DownloadResult:
    return _DownloadResult(True, url, 200, 1, hashlib.sha256(url.encode()).hexdigest(),
                           "application/pdf")


@pytest.fixture
def scheduled_scraper(monkeypatch):
    """Make scrape_irev_results build a scraper with faked discovery/downloads.

    Units 0-9 download at once; from unit 10 on, downloads wait for `release`
    (or a stop request), so the job is mid-run when the test shuts it down.
    """
    release = asyncio.Event()
    started = asyncio.Event()
    built: list[IRevScraper] = []
    detector_calls: list = []

    def factory(*args, **kwargs):
        s = IRevScraper(election_ids=[ELECTION], write_snapshot=False)
        s._throttle = irev._Throttle(0)

        async def discover():
            return [_unit(i) for i in range(40)]

        async def download(url, save_to):
            i = int(url.rsplit("/", 1)[1].split(".")[0])
            if i >= 10:
                started.set()
                await release.wait()
            return _ok(url)

        s._discover_units = discover
        s._download = download
        built.append(s)
        return s

    class FakeDetector:
        def __init__(self, run):
            detector_calls.append(run)

        async def run(self):
            return 0, 0

    monkeypatch.setattr(irev, "IRevScraper", factory)
    monkeypatch.setattr(anomaly_detector, "AnomalyDetector", FakeDetector)
    return {"release": release, "started": started, "built": built,
            "detector_calls": detector_calls}


@pytest.mark.asyncio
async def test_shutdown_stops_running_scheduled_scrape_cleanly(mock_db, scheduled_scraper):
    job = asyncio.create_task(irev_tasks.scrape_irev_results())
    await asyncio.wait_for(scheduled_scraper["started"].wait(), 5)
    scraper = scheduled_scraper["built"][0]
    scraper._stop_grace_seconds = 0.1  # the held downloads never finish on their own

    assert await irev_tasks.stop_active_irev_scrape("app shutdown", timeout=5) is True
    assert job.done() and job.exception() is None

    run = await IRevScrapeRun.get(scraper._run.id)
    assert run.status == ScrapeRunStatus.INTERRUPTED
    assert "app shutdown" in run.error_message
    rows = await ScrapeLogEntry.find({"run_id": run.id}).to_list()
    assert len(rows) == 10 == run.total_polling_units_visited
    assert run.chain_tip_sequence == max(r.chain_sequence for r in rows) == 10
    # Shutdown path skips anomaly detection; the next cycle covers it
    assert scheduled_scraper["detector_calls"] == []
    # Registration cleared, so a later shutdown finds nothing to stop
    assert irev_tasks._active_scraper is None
    assert await irev_tasks.stop_active_irev_scrape() is None


@pytest.mark.asyncio
async def test_stop_returns_none_when_no_scrape_is_running(mock_db):
    assert await irev_tasks.stop_active_irev_scrape(timeout=0.1) is None


@pytest.mark.asyncio
async def test_stop_gives_up_after_timeout(mock_db, scheduled_scraper):
    job = asyncio.create_task(irev_tasks.scrape_irev_results())
    await asyncio.wait_for(scheduled_scraper["started"].wait(), 5)
    scheduled_scraper["built"][0]._stop_grace_seconds = 60  # longer than our wait

    assert await irev_tasks.stop_active_irev_scrape("app shutdown", timeout=0.1) is False

    # What the scheduler shutdown does next: cancel the job. Existing handling
    # saves the run as FAILED rather than leaving it RUNNING.
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    run = await IRevScrapeRun.get(scheduled_scraper["built"][0]._run.id)
    assert run.status == ScrapeRunStatus.FAILED


@pytest.mark.asyncio
async def test_scrape_without_shutdown_still_runs_anomaly_detection(mock_db, scheduled_scraper):
    scheduled_scraper["release"].set()
    await irev_tasks.scrape_irev_results()
    run = scheduled_scraper["built"][0]._run
    assert run.status == ScrapeRunStatus.COMPLETED
    assert scheduled_scraper["detector_calls"] == [run]


@pytest.mark.asyncio
async def test_shutdown_scheduler_pauses_then_stops_scrape_then_shuts_down(monkeypatch):
    calls: list[str] = []

    class FakeScheduler:
        running = True

        def pause(self):
            calls.append("pause")

        def shutdown(self, wait=True):
            calls.append(f"shutdown(wait={wait})")
            self.running = False

    async def fake_stop(reason="app shutdown", timeout=0):
        calls.append(f"stop({reason})")
        return True

    monkeypatch.setattr(sched_mod, "scheduler", FakeScheduler())
    monkeypatch.setattr(irev_tasks, "stop_active_irev_scrape", fake_stop)

    await sched_mod.shutdown_scheduler()
    assert calls == ["pause", "stop(app shutdown)", "shutdown(wait=False)"]
