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
