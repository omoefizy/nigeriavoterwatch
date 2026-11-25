"""
IReV scraper shutdown and crash visibility:
  - SIGTERM / SIGINT: stop taking units, flush, save INTERRUPTED with the real chain tip
  - grace period: hung downloads are cancelled without losing queued rows
  - progress (counters + chain tip) written to the run record after every flush
  - startup: RUNNING runs with a heartbeat older than 10 minutes are marked FAILED

Runs against in-memory MongoDB (mongomock-motor); discovery and downloads are faked.
"""
import asyncio
import hashlib
import os
import signal
from datetime import datetime, timedelta, timezone

import pytest
from beanie import PydanticObjectId

from app.models.audit import AuditAction, AuditLog
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus
from app.scrapers import irev_scraper as irev
from app.scrapers.irev_scraper import (
    IRevScraper,
    _DownloadResult,
    _PendingUnit,
    install_stop_signal_handlers,
)

ELECTION = "irev-shutdown-test-election"


def _unit(i: int, with_document: bool = True) -> _PendingUnit:
    return _PendingUnit(
        election_id=ELECTION, election_name="test",
        state_code="24", state_name="LAGOS",
        lga_code="18", lga_name="OSHODI/ISOLO",
        ward_code="02", ward_name="ORILE-OSHODI",
        pu_code=f"24/18/02/{i:03d}", pu_name=f"PU {i}",
        portal_url="https://example.invalid",
        document={"document_url": f"https://example.invalid/{i}.pdf"} if with_document else {},
    )


def _ok(url: str) -> _DownloadResult:
    return _DownloadResult(True, url, 200, 1, hashlib.sha256(url.encode()).hexdigest(),
                           "application/pdf")


def _index(url: str) -> int:
    return int(url.rsplit("/", 1)[1].split(".")[0])


def _scraper(monkeypatch, units, download) -> IRevScraper:
    s = IRevScraper(election_ids=[ELECTION], write_snapshot=False)
    s._throttle = irev._Throttle(0)

    async def discover():
        return list(units)

    monkeypatch.setattr(s, "_discover_units", discover)
    monkeypatch.setattr(s, "_download", download)
    return s


async def _stored_rows(run_id) -> list[ScrapeLogEntry]:
    return await ScrapeLogEntry.find({"run_id": run_id}).sort("+chain_sequence").to_list()


def _assert_chain_contiguous(rows: list[ScrapeLogEntry]):
    chained = sorted((r for r in rows if r.chain_sequence is not None),
                     key=lambda r: r.chain_sequence)
    assert [r.chain_sequence for r in chained] == list(range(1, len(chained) + 1))
    for prev, cur in zip(chained, chained[1:]):
        assert cur.prev_image_block_hash == prev.image_block_hash
    return chained


# ---------------------------------------------------------------------------
# SIGTERM / SIGINT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_signal_stops_new_units_flushes_and_marks_interrupted(mock_db, monkeypatch, sig):
    n_units, stop_at = 3 * irev._INSERT_BATCH, irev._INSERT_BATCH + 20
    started: list[int] = []

    async def download(url, save_to):
        started.append(_index(url))
        if len(started) == stop_at:
            os.kill(os.getpid(), sig)  # a real signal, delivered through the loop
        await asyncio.sleep(0)
        return _ok(url)

    scraper = _scraper(monkeypatch, [_unit(i) for i in range(n_units)], download)
    received: list[int] = []
    restore = install_stop_signal_handlers(scraper, received)
    try:
        run = await scraper.run()
    finally:
        restore()

    assert received == [sig]
    assert scraper.stop_reason == sig.name
    stored = await IRevScrapeRun.get(run.id)
    assert stored.status == ScrapeRunStatus.INTERRUPTED
    assert sig.name in stored.error_message
    assert stored.completed_at is not None

    # Stopped taking units: only the ones already started were processed,
    # and every one of them was flushed (nothing lost, nothing extra).
    rows = await _stored_rows(run.id)
    assert stop_at <= len(rows) < n_units
    assert len(rows) == len(started)
    chained = _assert_chain_contiguous(rows)

    # Final counters and chain tip match what is really in MongoDB
    assert stored.total_polling_units_visited == len(rows)
    assert stored.images_downloaded == len(rows)
    assert stored.chain_tip_sequence == chained[-1].chain_sequence
    assert stored.chain_tip_hash == chained[-1].image_block_hash


@pytest.mark.asyncio
async def test_signal_handlers_are_removed_after_the_run(mock_db, monkeypatch):
    async def download(url, save_to):
        return _ok(url)

    scraper = _scraper(monkeypatch, [_unit(i) for i in range(3)], download)
    restore = install_stop_signal_handlers(scraper)
    assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None)
    run = await scraper.run()
    restore()

    assert run.status == ScrapeRunStatus.COMPLETED  # no signal: normal completion
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


@pytest.mark.asyncio
async def test_stop_during_discovery_processes_no_units(mock_db, monkeypatch):
    downloads = []

    async def download(url, save_to):
        downloads.append(url)
        return _ok(url)

    scraper = _scraper(monkeypatch, [], download)

    async def discover():
        scraper.request_stop("SIGTERM")
        return [_unit(i) for i in range(10)]

    monkeypatch.setattr(scraper, "_discover_units", discover)
    run = await scraper.run()

    assert run.status == ScrapeRunStatus.INTERRUPTED
    assert downloads == []
    assert await ScrapeLogEntry.find({"run_id": run.id}).count() == 0
    assert run.total_polling_units_visited == 0


@pytest.mark.asyncio
async def test_hung_downloads_are_cancelled_after_grace_and_queued_rows_flushed(mock_db, monkeypatch):
    finished_before_stop = 10
    hang = asyncio.Event()  # never set

    scraper_ref: list[IRevScraper] = []

    async def download(url, save_to):
        i = _index(url)
        if i < finished_before_stop:
            return _ok(url)
        if i == finished_before_stop:
            scraper_ref[0].request_stop("SIGTERM")
        await hang.wait()  # stuck download, e.g. a slow host mid-backoff
        return _ok(url)

    scraper = _scraper(monkeypatch, [_unit(i) for i in range(50)], download)
    scraper_ref.append(scraper)
    scraper._stop_grace_seconds = 0.2

    run = await asyncio.wait_for(scraper.run(), timeout=10)

    assert run.status == ScrapeRunStatus.INTERRUPTED
    rows = await _stored_rows(run.id)
    # The 10 completed units were still in the queued batch (< _INSERT_BATCH)
    # and were written by the final flush; the hung ones left no row.
    assert sorted(_index(r.image_url) for r in rows) == list(range(finished_before_stop))
    chained = _assert_chain_contiguous(rows)
    assert run.chain_tip_sequence == chained[-1].chain_sequence == finished_before_stop
    assert (await IRevScrapeRun.get(run.id)).total_polling_units_visited == finished_before_stop


# ---------------------------------------------------------------------------
# Progress after every flush
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_record_shows_progress_after_every_flush(mock_db, monkeypatch):
    batch = irev._INSERT_BATCH
    n_units = 2 * batch + 5
    # One unit has no document, so the missing counter is exercised too. Only
    # one: mongomock ignores the unique index's partial filter, so it would
    # reject a second row with chain_sequence null (real MongoDB accepts it).
    units = [_unit(i, with_document=(i != 0)) for i in range(n_units)]

    async def download(url, save_to):
        await asyncio.sleep(0)
        return _ok(url)

    scraper = _scraper(monkeypatch, units, download)
    snapshots: list[dict] = []
    original = scraper._write_progress

    async def write_and_snapshot():
        await original()
        doc = await IRevScrapeRun.get_motor_collection().find_one({"_id": scraper._run.id})
        rows = await ScrapeLogEntry.find({"run_id": scraper._run.id}).to_list()
        head = max((r for r in rows if r.chain_sequence is not None),
                   key=lambda r: r.chain_sequence)
        snapshots.append({"doc": doc, "rows": rows, "head": head})

    monkeypatch.setattr(scraper, "_write_progress", write_and_snapshot)
    run = await scraper.run()
    assert run.status == ScrapeRunStatus.COMPLETED

    assert [len(s["rows"]) for s in snapshots] == [batch, 2 * batch, n_units]
    for s in snapshots:
        doc, rows = s["doc"], s["rows"]
        # Written while the run is still going, and equal to what is stored
        assert doc["status"] == ScrapeRunStatus.RUNNING.value
        assert doc["total_polling_units_visited"] == len(rows)
        assert doc["missing_url_count"] == sum(r.is_missing_url for r in rows)
        assert doc["images_downloaded"] == sum(r.image_downloaded for r in rows)
        assert doc["broken_url_count"] == 0
        assert doc["chain_tip_sequence"] == s["head"].chain_sequence
        assert doc["chain_tip_hash"] == s["head"].image_block_hash


@pytest.mark.asyncio
async def test_progress_write_never_overwrites_a_final_status(mock_db, monkeypatch):
    async def download(url, save_to):
        return _ok(url)

    scraper = _scraper(monkeypatch, [_unit(i) for i in range(3)], download)
    run = await scraper.run()
    assert run.status == ScrapeRunStatus.COMPLETED

    scraper._stored_counts["total_polling_units_visited"] = 999
    await scraper._write_progress()
    stored = await IRevScrapeRun.get(run.id)
    assert stored.status == ScrapeRunStatus.COMPLETED
    assert stored.total_polling_units_visited == 3


# ---------------------------------------------------------------------------
# Stale run cleanup at startup
# ---------------------------------------------------------------------------

async def _run_record(status, started_ago, heartbeat_ago) -> IRevScrapeRun:
    now = datetime.now(timezone.utc)
    run = IRevScrapeRun(
        status=status,
        started_at=now - started_ago,
        heartbeat_at=None if heartbeat_ago is None else now - heartbeat_ago,
    )
    await run.insert()
    return run


@pytest.mark.asyncio
async def test_startup_marks_only_stale_running_runs_failed(mock_db):
    stale = await _run_record(ScrapeRunStatus.RUNNING, timedelta(hours=2), timedelta(minutes=11))
    no_beat_old = await _run_record(ScrapeRunStatus.RUNNING, timedelta(minutes=30), None)
    fresh = await _run_record(ScrapeRunStatus.RUNNING, timedelta(hours=1), timedelta(minutes=2))
    no_beat_new = await _run_record(ScrapeRunStatus.RUNNING, timedelta(minutes=2), None)
    done = await _run_record(ScrapeRunStatus.COMPLETED, timedelta(hours=3), timedelta(hours=2))
    # The stale run's scrape_log rows must be left alone
    for i in range(3):
        await ScrapeLogEntry(run_id=stale.id, state_code="24", state_name="LAGOS",
                             pu_code=f"24/18/02/{i:03d}", chain_sequence=i + 1).insert()

    marked = await IRevScraper.fail_stale_runs()

    assert {r.id for r in marked} == {stale.id, no_beat_old.id}
    for r in (stale, no_beat_old):
        got = await IRevScrapeRun.get(r.id)
        assert got.status == ScrapeRunStatus.FAILED
        assert got.completed_at is not None
        assert "heartbeat" in got.error_message and "10 minutes" in got.error_message
    assert (await IRevScrapeRun.get(fresh.id)).status == ScrapeRunStatus.RUNNING
    assert (await IRevScrapeRun.get(no_beat_new.id)).status == ScrapeRunStatus.RUNNING
    got_done = await IRevScrapeRun.get(done.id)
    assert got_done.status == ScrapeRunStatus.COMPLETED and got_done.error_message is None

    assert await ScrapeLogEntry.find({"run_id": stale.id}).count() == 3

    audits = await AuditLog.find({"entity_type": "irev_scrape_run"}).to_list()
    assert sorted(a.entity_id for a in audits) == sorted([str(stale.id), str(no_beat_old.id)])
    assert all(a.action == AuditAction.SCRAPE_FAILED for a in audits)

    # Idempotent: a second pass finds nothing more to do
    assert await IRevScraper.fail_stale_runs() == []
    assert await AuditLog.find({"entity_type": "irev_scrape_run"}).count() == 2


@pytest.mark.asyncio
async def test_scraper_startup_clears_stale_run_then_runs(mock_db, monkeypatch):
    stale = await _run_record(ScrapeRunStatus.RUNNING, timedelta(hours=1), timedelta(minutes=15))

    async def download(url, save_to):
        return _ok(url)

    run = await _scraper(monkeypatch, [_unit(i) for i in range(3)], download).run()

    assert run is not None and run.status == ScrapeRunStatus.COMPLETED
    assert (await IRevScrapeRun.get(stale.id)).status == ScrapeRunStatus.FAILED


@pytest.mark.asyncio
async def test_scraper_startup_leaves_live_run_alone_and_refuses(mock_db, monkeypatch):
    live = await _run_record(ScrapeRunStatus.RUNNING, timedelta(minutes=30), timedelta(minutes=1))

    async def download(url, save_to):
        raise AssertionError("must not download while another run holds the lock")

    scraper = _scraper(monkeypatch, [_unit(0)], download)
    assert await scraper.run() is None
    assert scraper.lock_holder.id == live.id
    assert (await IRevScrapeRun.get(live.id)).status == ScrapeRunStatus.RUNNING
    assert await AuditLog.find().count() == 0


@pytest.mark.asyncio
async def test_dry_run_does_not_touch_stale_runs(mock_db, monkeypatch):
    stale = await _run_record(ScrapeRunStatus.RUNNING, timedelta(hours=1), timedelta(minutes=15))

    async def download(url, save_to):
        return _ok(url)

    scraper = _scraper(monkeypatch, [_unit(0)], download)
    scraper.dry_run = True
    scraper.write_snapshot = False
    await scraper.run()
    assert (await IRevScrapeRun.get(stale.id)).status == ScrapeRunStatus.RUNNING
