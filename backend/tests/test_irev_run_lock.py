"""
IReV scraper run safety: run lock + heartbeat, crash handling, chain head
re-check before each batch insert, and ordered batch inserts.

Full runs go through IRevScraper.run() against the in-memory MongoDB
(mongomock-motor) with discovery and downloads faked — no network, no Atlas.
"""
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from beanie import PydanticObjectId

from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus
from app.scrapers import irev_scraper as irev
from app.scrapers.irev_scraper import (
    IRevScraper,
    _compute_image_block_hash,
    _DownloadResult,
    _image_genesis_hash,
    _PendingUnit,
)

ELECTION = "irev-lock-test-election"


def _unit(i: int) -> _PendingUnit:
    return _PendingUnit(
        election_id=ELECTION, election_name="test",
        state_code="24", state_name="LAGOS",
        lga_code="20", lga_name="SURULERE",
        ward_code="05", ward_name="YABA",
        pu_code=f"24/20/05/{i:03d}", pu_name=f"PU {i}",
        portal_url="https://example.invalid",
        document={"document_url": f"https://example.invalid/{i}.pdf"},
    )


def _ok(url: str) -> _DownloadResult:
    return _DownloadResult(True, url, 200, 1, hashlib.sha256(url.encode()).hexdigest(),
                           "application/pdf")


def _scraper(monkeypatch, n_units: int, download=None) -> IRevScraper:
    s = IRevScraper(election_ids=[ELECTION], write_snapshot=False)
    s._throttle = irev._Throttle(0)

    async def discover():
        return [_unit(i) for i in range(n_units)]

    async def default_download(url, save_to):
        await asyncio.sleep(0)
        return _ok(url)

    monkeypatch.setattr(s, "_discover_units", discover)
    monkeypatch.setattr(s, "_download", download or default_download)
    return s


async def _running_run(started_ago: timedelta, heartbeat_ago) -> IRevScrapeRun:
    now = datetime.now(timezone.utc)
    run = IRevScrapeRun(
        started_at=now - started_ago,
        heartbeat_at=None if heartbeat_ago is None else now - heartbeat_ago,
        status=ScrapeRunStatus.RUNNING,
    )
    await run.insert()
    return run


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Run lock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refuses_to_start_while_a_live_run_holds_the_lock(mock_db, monkeypatch):
    holder = await _running_run(timedelta(minutes=20), timedelta(minutes=9))
    downloads = []

    async def download(url, save_to):
        downloads.append(url)
        return _ok(url)

    scraper = _scraper(monkeypatch, 5, download)
    result = await scraper.run()

    assert result is None and scraper._run is None
    assert scraper.lock_holder.id == holder.id
    assert await IRevScrapeRun.count() == 1  # no run record created
    assert downloads == [] and await ScrapeLogEntry.count() == 0


@pytest.mark.asyncio
async def test_stale_and_heartbeatless_running_records_do_not_block(mock_db, monkeypatch):
    await _running_run(timedelta(hours=1), timedelta(minutes=11))  # process died
    await _running_run(timedelta(hours=3), None)  # record from before heartbeats

    run = await _scraper(monkeypatch, 5).run()

    assert run.status == ScrapeRunStatus.COMPLETED
    assert await ScrapeLogEntry.count() == 5


@pytest.mark.asyncio
async def test_simultaneous_start_earlier_run_wins(mock_db, monkeypatch):
    # Both runs passed the pre-check at the same moment; the competitor
    # started first, so after both records exist this run must back off.
    competitor = await _running_run(timedelta(seconds=1), timedelta(seconds=0))
    scraper = _scraper(monkeypatch, 5)
    real_check = scraper._earliest_live_run
    calls = 0

    async def check_missing_competitor_once():
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_check()

    monkeypatch.setattr(scraper, "_earliest_live_run", check_missing_competitor_once)
    result = await scraper.run()

    assert result is None and scraper.lock_holder.id == competitor.id
    own = await IRevScrapeRun.find({"_id": {"$ne": competitor.id}}).to_list()
    assert len(own) == 1 and own[0].status == ScrapeRunStatus.FAILED
    assert str(competitor.id) in own[0].error_message
    assert (await IRevScrapeRun.get(competitor.id)).status == ScrapeRunStatus.RUNNING
    assert await ScrapeLogEntry.count() == 0
    assert scraper._heartbeat_task is None


@pytest.mark.asyncio
async def test_simultaneous_start_later_competitor_does_not_stop_this_run(mock_db, monkeypatch):
    scraper = _scraper(monkeypatch, 5)
    real_check = scraper._earliest_live_run
    calls = 0

    async def competitor_appears_after_precheck():
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        # Competitor inserted just after this run's record: it started later
        await _running_run(timedelta(seconds=-1), timedelta(seconds=0))
        return await real_check()

    monkeypatch.setattr(scraper, "_earliest_live_run", competitor_appears_after_precheck)
    run = await scraper.run()

    assert run.status == ScrapeRunStatus.COMPLETED
    assert await ScrapeLogEntry.count() == 5


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_heartbeat_is_refreshed_during_the_run_and_stops_after(mock_db, monkeypatch):
    seen = []

    async def slow_download(url, save_to):
        for _ in range(4):
            await asyncio.sleep(0.03)
            r = await IRevScrapeRun.find_one({"status": ScrapeRunStatus.RUNNING.value})
            seen.append(_utc(r.heartbeat_at))
        return _ok(url)

    scraper = _scraper(monkeypatch, 1, slow_download)
    scraper._heartbeat_interval = 0.01
    run = await scraper.run()

    assert run.status == ScrapeRunStatus.COMPLETED
    assert len(set(seen)) >= 3 and seen == sorted(seen)  # advanced repeatedly
    assert seen[-1] > _utc(run.started_at)

    assert scraper._heartbeat_task is None
    stored = await IRevScrapeRun.get(run.id)
    await asyncio.sleep(0.05)
    assert (await IRevScrapeRun.get(run.id)).heartbeat_at == stored.heartbeat_at


def test_heartbeat_interval_and_lock_window():
    assert irev._HEARTBEAT_INTERVAL_SECONDS == 30
    assert irev._LOCK_STALE_AFTER == timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Crash handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancelled_run_is_marked_failed(mock_db, monkeypatch):
    started = asyncio.Event()

    async def hanging_download(url, save_to):
        started.set()
        await asyncio.sleep(60)

    scraper = _scraper(monkeypatch, 3, hanging_download)
    task = asyncio.create_task(scraper.run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    run = await IRevScrapeRun.get(scraper._run.id)
    assert run.status == ScrapeRunStatus.FAILED
    assert "CancelledError" in run.error_message and run.completed_at is not None
    assert scraper._heartbeat_task is None
    assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []


@pytest.mark.asyncio
async def test_crash_while_loading_chain_tip_is_marked_failed(mock_db, monkeypatch):
    scraper = _scraper(monkeypatch, 3)

    async def broken_tip():
        raise RuntimeError("tip read failed")

    monkeypatch.setattr(scraper, "_load_chain_tip", broken_tip)
    run = await scraper.run()

    stored = await IRevScrapeRun.get(run.id)
    assert stored.status == ScrapeRunStatus.FAILED
    assert stored.error_message == "tip read failed"
    assert scraper._heartbeat_task is None


# ---------------------------------------------------------------------------
# Chain head re-check before each batch insert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_aborts_when_chain_head_moves_between_batches(mock_db, monkeypatch):
    foreign_run = PydanticObjectId()
    injected = False

    async def download(url, save_to):
        nonlocal injected
        await asyncio.sleep(0)
        # Once this run's first batch is stored, another writer extends the chain
        if scraper._stored_tip_sequence == irev._INSERT_BATCH and not injected:
            injected = True
            await ScrapeLogEntry(
                run_id=foreign_run, state_code="24", state_name="LAGOS",
                chain_sequence=900, image_hash_sha256="ab" * 32,
                prev_image_block_hash="cd" * 32, image_block_hash="ef" * 32,
            ).insert()
        return _ok(url)

    scraper = _scraper(monkeypatch, 3 * irev._INSERT_BATCH, download)
    run = await scraper.run()

    assert injected
    assert run.status == ScrapeRunStatus.FAILED
    msg = run.error_message
    assert msg.startswith("Image chain head moved during this run")
    assert f"expected seq {irev._INSERT_BATCH}" in msg and "database has seq 900" in msg
    assert str(foreign_run) in msg
    # Only the first batch and the foreign row are in the chain; nothing after
    rows = await ScrapeLogEntry.find({"run_id": run.id}).sort("chain_sequence").to_list()
    assert [r.chain_sequence for r in rows] == list(range(1, irev._INSERT_BATCH + 1))
    assert run.chain_tip_sequence == irev._INSERT_BATCH


@pytest.mark.asyncio
async def test_unchanged_chain_head_lets_every_batch_through(mock_db, monkeypatch):
    n = 2 * irev._INSERT_BATCH + 7
    run = await _scraper(monkeypatch, n).run()

    assert run.status == ScrapeRunStatus.COMPLETED
    rows = await ScrapeLogEntry.find_all().sort("chain_sequence").to_list()
    assert [r.chain_sequence for r in rows] == list(range(1, n + 1))
    assert run.chain_tip_sequence == n


# ---------------------------------------------------------------------------
# Ordered batch insert
# ---------------------------------------------------------------------------

def _entry(seq: int, prev: str) -> ScrapeLogEntry:
    image = hashlib.sha256(str(seq).encode()).hexdigest()
    return ScrapeLogEntry(
        run_id=PydanticObjectId(), state_code="24", state_name="LAGOS",
        chain_sequence=seq, image_hash_sha256=image,
        prev_image_block_hash=prev, image_block_hash=_compute_image_block_hash(image, prev),
    )


@pytest.mark.asyncio
async def test_ordered_insert_stops_at_duplicate(mock_db, monkeypatch):
    # Another writer stores seq 3 after this run's head check passed
    await _entry(3, "00" * 32).insert()

    scraper = IRevScraper(election_ids=[ELECTION], write_snapshot=False)
    prev, batch = _image_genesis_hash(), []
    for seq in range(1, 6):
        batch.append(_entry(seq, prev))
        prev = batch[-1].image_block_hash
    scraper._batch = list(batch)

    async def head_before_conflict():
        return None  # what the head check saw, before the other writer landed

    real_insert = ScrapeLogEntry.insert_many
    kwargs_seen = {}

    async def recording_insert(docs, *a, **kw):
        kwargs_seen.update(kw)
        return await real_insert(docs, *a, **kw)

    monkeypatch.setattr(scraper, "_read_chain_head", head_before_conflict)
    monkeypatch.setattr(irev.ScrapeLogEntry, "insert_many", recording_insert)
    with pytest.raises(irev.BulkWriteError):
        await scraper._flush_batch()

    assert kwargs_seen.get("ordered") is True
    stored = sorted(r.chain_sequence for r in await ScrapeLogEntry.find_all().to_list())
    assert stored == [1, 2, 3]  # 1-2 from this batch, 3 foreign; 4-5 never sent through
    assert scraper._chain_tip() == (2, batch[1].image_block_hash)
    assert scraper._flush_failed

    scraper._batch = [_entry(6, prev)]
    await scraper._flush_batch()  # dropped: would chain past the conflict
    assert await ScrapeLogEntry.count() == 3
