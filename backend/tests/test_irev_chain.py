"""
IReV image hash chain — concurrency tests.

Many polling units are downloaded concurrently (the download semaphore allows
2 at a time) and finish in random order. Each finished download extends the
image chain. These tests prove the chain is still a single, unforked,
gap-free sequence that re-verifies from genesis.
"""
import asyncio
import hashlib
import inspect
import random

import pytest
from beanie import PydanticObjectId

from app.models.scrape_log import IRevScrapeRun
from app.scrapers import irev_scraper as irev
from app.scrapers.irev_scraper import (
    IRevScraper,
    _compute_image_block_hash,
    _DownloadResult,
    _image_genesis_hash,
    _PendingUnit,
)

N_UNITS = 60  # < _INSERT_BATCH, so every entry stays in scraper._batch for inspection


def _unit(i: int) -> _PendingUnit:
    return _PendingUnit(
        election_id="irev-test-election", election_name="test",
        state_code="24", state_name="LAGOS",
        lga_code="20", lga_name="SURULERE",
        ward_code="05", ward_name="YABA",
        pu_code=f"24/20/05/{i:03d}", pu_name=f"PU {i}",
        portal_url="https://example.invalid",
        document={"document_url": f"https://example.invalid/{i}.pdf"},
    )


def _scraper() -> IRevScraper:
    s = IRevScraper(election_ids=["irev-test-election"])
    s._run = IRevScrapeRun()
    s._run.id = PydanticObjectId()
    s._throttle = irev._Throttle(0)  # no politeness delay in tests
    return s


@pytest.mark.asyncio
async def test_concurrent_downloads_extend_chain_serially_without_fork(mock_db, monkeypatch):
    scraper = _scraper()
    rng = random.Random(1234)
    in_flight = 0
    max_in_flight = 0
    completion_order: list[str] = []

    async def fake_download(url: str, save_to):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        # Random completion order, with many downloads finishing on the same
        # event-loop tick so their chain appends are scheduled back-to-back —
        # the situation in which a racy append would fork the chain.
        await asyncio.sleep(rng.choice([0, 0, 0, 0.001, 0.002]))
        in_flight -= 1
        completion_order.append(url)
        digest = hashlib.sha256(url.encode()).hexdigest()
        return _DownloadResult(True, url, 200, 123, digest, "application/pdf")

    monkeypatch.setattr(scraper, "_download", fake_download)

    await asyncio.gather(*(scraper._process_unit(_unit(i)) for i in range(N_UNITS)))

    # Downloads really did overlap, capped by the semaphore, and finished out of order
    assert max_in_flight == 2
    expected_order = [f"https://example.invalid/{i}.pdf" for i in range(N_UNITS)]
    assert completion_order != expected_order

    entries = sorted(scraper._batch, key=lambda e: e.chain_sequence)
    assert len(entries) == N_UNITS

    # Unique, gap-free sequence numbers
    assert [e.chain_sequence for e in entries] == list(range(1, N_UNITS + 1))
    # No fork: every block hash is used as `prev` by at most one successor
    prevs = [e.prev_image_block_hash for e in entries]
    assert len(set(prevs)) == len(prevs)

    # Linked and re-verifiable from genesis
    prev = _image_genesis_hash()
    for e in entries:
        assert e.prev_image_block_hash == prev
        assert e.image_block_hash == _compute_image_block_hash(e.image_hash_sha256, prev)
        prev = e.image_block_hash
    assert scraper._prev_block_hash == prev
    assert scraper._chain_sequence == N_UNITS


def test_extend_chain_has_no_await_point():
    """The tip read/compute/write must stay atomic on the event loop."""
    assert not inspect.iscoroutinefunction(IRevScraper._extend_chain)
    assert "await" not in inspect.getsource(IRevScraper._extend_chain).split('"""')[-1]


@pytest.mark.asyncio
async def test_failed_downloads_do_not_consume_chain_sequence(mock_db, monkeypatch):
    scraper = _scraper()

    async def fake_download(url: str, save_to):
        i = int(url.rsplit("/", 1)[1].split(".")[0])
        await asyncio.sleep(0)
        if i % 3 == 0:
            return _DownloadResult(False, url, None, reason="ConnectError: dns")
        return _DownloadResult(True, url, 200, 1, hashlib.sha256(url.encode()).hexdigest(),
                               "application/pdf")

    monkeypatch.setattr(scraper, "_download", fake_download)
    await asyncio.gather(*(scraper._process_unit(_unit(i)) for i in range(30)))

    chained = sorted((e for e in scraper._batch if e.chain_sequence), key=lambda e: e.chain_sequence)
    failed = [e for e in scraper._batch if e.document_status == "download_failed"]
    assert len(failed) == 10 and all(e.chain_sequence is None for e in failed)
    assert [e.chain_sequence for e in chained] == list(range(1, 21))


# ---------------------------------------------------------------------------
# Batched inserts under concurrency (regression for the 2026-11-25 seq 251 gap)
# ---------------------------------------------------------------------------

class _FakeStore:
    """Stand-in for ScrapeLogEntry.insert_many with the unique chain_sequence
    index. The insert takes time, so other tasks keep queueing rows meanwhile."""

    def __init__(self, fail_on_call: int | None = None):
        self.rows: list = []
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.fail_on_call = fail_on_call

    async def insert_many(self, docs, *args, **kwargs):
        from pymongo.errors import BulkWriteError
        docs = list(docs)  # what was sent at call time
        self.calls += 1
        call = self.calls
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            if call == self.fail_on_call:
                raise BulkWriteError({"writeErrors": [{"index": 0, "code": 11000}]})
            stored = {r.chain_sequence for r in self.rows if r.chain_sequence is not None}
            for d in docs:
                if d.chain_sequence is not None and d.chain_sequence in stored:
                    raise BulkWriteError({"writeErrors": [{"index": 0, "code": 11000,
                                          "keyValue": {"chain_sequence": d.chain_sequence}}]})
            self.rows.extend(docs)
        finally:
            self.in_flight -= 1


async def _run_units(scraper, n, monkeypatch, store, downloads: list | None = None):
    """Runs units through _process_all, as the real run does. Returns the
    exception it raised, or None."""
    rng = random.Random(99)

    async def fake_download(url: str, save_to):
        if downloads is not None:
            downloads.append(url)
        await asyncio.sleep(rng.choice([0, 0, 0.001, 0.002]))
        return _DownloadResult(True, url, 200, 1, hashlib.sha256(url.encode()).hexdigest(),
                               "application/pdf")

    async def head_from_store():
        # The chain head check reads MongoDB; here the fake store is the database
        chained = [r for r in store.rows if r.chain_sequence is not None]
        return max(chained, key=lambda r: r.chain_sequence) if chained else None

    monkeypatch.setattr(scraper, "_download", fake_download)
    monkeypatch.setattr(scraper, "_read_chain_head", head_from_store)
    monkeypatch.setattr(irev.ScrapeLogEntry, "insert_many", store.insert_many)
    try:
        await scraper._process_all([_unit(i) for i in range(n)])
    except Exception as exc:
        return exc
    return None


@pytest.mark.asyncio
async def test_concurrent_flushes_store_every_row_once_in_chain_order(mock_db, monkeypatch):
    scraper = _scraper()
    store = _FakeStore()
    n = 3 * irev._INSERT_BATCH + 17

    error = await _run_units(scraper, n, monkeypatch, store)
    await scraper._flush_batch()

    assert error is None
    assert store.max_in_flight == 1  # flushes never overlap
    # Every sequence stored exactly once, in order, nothing lost
    assert [r.chain_sequence for r in store.rows] == list(range(1, n + 1))
    prev = _image_genesis_hash()
    for r in store.rows:
        assert r.prev_image_block_hash == prev
        prev = r.image_block_hash
    assert scraper._chain_tip() == (n, prev)


@pytest.mark.asyncio
async def test_failed_flush_stops_later_writes_and_reports_stored_tip(mock_db, monkeypatch):
    scraper = _scraper()
    store = _FakeStore(fail_on_call=2)
    n = 3 * irev._INSERT_BATCH

    error = await _run_units(scraper, n, monkeypatch, store)
    await scraper._flush_batch()  # the run's final flush must not write either

    assert error is not None
    # Only the first batch is stored; nothing chains past the failed batch
    seqs = [r.chain_sequence for r in store.rows]
    assert seqs == list(range(1, irev._INSERT_BATCH + 1))
    assert store.calls == 2
    assert scraper._chain_tip() == (irev._INSERT_BATCH, store.rows[-1].image_block_hash)
    assert scraper._chain_sequence > irev._INSERT_BATCH  # in-memory tip ran ahead


@pytest.mark.asyncio
async def test_failure_cancels_in_flight_units(mock_db, monkeypatch):
    scraper = _scraper()
    store = _FakeStore(fail_on_call=1)
    downloads: list[str] = []
    n = 5 * irev._INSERT_BATCH

    error = await _run_units(scraper, n, monkeypatch, store, downloads)
    started_at_failure = len(downloads)
    await asyncio.sleep(0.05)  # anything left running would keep downloading

    assert error is not None
    assert len(downloads) == started_at_failure < n  # the rest were cancelled
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert not others  # no unit task outlives _process_all
    assert store.rows == [] and store.calls == 1


@pytest.mark.asyncio
async def test_cancel_during_insert_counts_as_failed_flush(mock_db, monkeypatch):
    scraper = _scraper()
    started = asyncio.Event()

    async def hanging_insert(docs, *a, **k):
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(irev.ScrapeLogEntry, "insert_many", hanging_insert)
    scraper._batch = [object()]  # contents don't matter to the fake
    flush = asyncio.create_task(scraper._flush_batch())
    await started.wait()
    flush.cancel()
    with pytest.raises(asyncio.CancelledError):
        await flush

    # Batch may or may not be stored, so later rows must not chain onto it
    assert scraper._flush_failed
    scraper._batch = [object()]
    await scraper._flush_batch()  # dropped, not sent to the hanging insert
    assert scraper._batch == []
