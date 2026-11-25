"""
IReV image chain: unique chain_sequence index + read-only verifier.
"""
import asyncio
import hashlib

import pytest
from beanie import PydanticObjectId
from pymongo.errors import BulkWriteError, DuplicateKeyError

from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry
from app.scrapers import irev_scraper as irev
from app.scrapers.irev_scraper import IRevScraper, _DownloadResult, _PendingUnit
from app.utils.image_chain import (
    compute_image_block_hash,
    image_genesis_hash,
    verify_image_chain,
)


def _entry(seq, image_hash=None, prev=None, block=None, pu="24/20/05/001"):
    return ScrapeLogEntry(
        run_id=PydanticObjectId(), election_id="e", pu_code=pu,
        state_code="24", state_name="LAGOS",
        chain_sequence=seq, image_hash_sha256=image_hash,
        prev_image_block_hash=prev, image_block_hash=block,
    )


def _chain(n):
    """n correctly linked entries."""
    out, prev = [], image_genesis_hash()
    for seq in range(1, n + 1):
        h = hashlib.sha256(f"img{seq}".encode()).hexdigest()
        block = compute_image_block_hash(h, prev)
        out.append(_entry(seq, h, prev, block, pu=f"24/20/05/{seq:03d}"))
        prev = block
    return out


def _coll():
    return ScrapeLogEntry.get_motor_collection()


# ---------------------------------------------------------------------------
# Unique index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_chain_sequence_insert_raises(mock_db):
    await _entry(7, "a" * 64).insert()
    with pytest.raises(DuplicateKeyError):
        await _entry(7, "b" * 64).insert()


@pytest.mark.asyncio
async def test_duplicate_chain_sequence_in_insert_many_raises(mock_db):
    await ScrapeLogEntry.insert_many([_entry(1, "a" * 64)])
    with pytest.raises(BulkWriteError):
        await ScrapeLogEntry.insert_many([_entry(2, "b" * 64), _entry(1, "c" * 64)])


def test_chain_sequence_index_is_unique_and_partial():
    """Unique only where chain_sequence is a number, so the many unchained
    entries (null) never collide. mongomock ignores partialFilterExpression,
    so the null case is checked against a real server, not here."""
    idx = next(i.document for i in ScrapeLogEntry.Settings.indexes
               if i.document["name"] == "chain_sequence_unique")
    assert list(idx["key"].items()) == [("chain_sequence", 1)]
    assert idx["unique"] is True
    assert idx["partialFilterExpression"] == {"chain_sequence": {"$type": "number"}}


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_chain_is_valid_at_genesis(mock_db):
    r = await verify_image_chain(_coll())
    assert r.valid and r.entries_checked == 0
    assert r.head_sequence == 0 and r.head_hash == image_genesis_hash()


@pytest.mark.asyncio
async def test_intact_chain(mock_db):
    entries = _chain(5)
    await ScrapeLogEntry.insert_many(entries)
    r = await verify_image_chain(_coll())
    assert r.valid and r.entries_checked == 5
    assert (r.head_sequence, r.head_hash) == (5, entries[-1].image_block_hash)


@pytest.mark.asyncio
async def test_tampered_image_hash_detected(mock_db):
    entries = _chain(5)
    entries[2].image_hash_sha256 = "f" * 64  # seq 3
    await ScrapeLogEntry.insert_many(entries)
    r = await verify_image_chain(_coll())
    assert not r.valid
    assert (r.problem, r.problem_sequence) == ("bad_block_hash", 3)
    assert r.head_sequence == 2  # last verified entry


@pytest.mark.asyncio
async def test_broken_link_detected(mock_db):
    entries = _chain(4)
    # seq 3 re-pointed at a forged prev, with a block hash consistent with it
    entries[2].prev_image_block_hash = "0" * 64
    entries[2].image_block_hash = compute_image_block_hash(entries[2].image_hash_sha256, "0" * 64)
    await ScrapeLogEntry.insert_many(entries)
    r = await verify_image_chain(_coll())
    assert (r.valid, r.problem, r.problem_sequence) == (False, "broken_link", 3)


@pytest.mark.asyncio
async def test_gap_detected(mock_db):
    entries = _chain(5)
    del entries[1]  # seq 2 missing
    await ScrapeLogEntry.insert_many(entries)
    r = await verify_image_chain(_coll())
    assert (r.valid, r.problem, r.problem_sequence) == (False, "gap", 2)
    assert r.detail["missing_to"] == 2


@pytest.mark.asyncio
async def test_scraper_output_verifies(mock_db, monkeypatch):
    """End to end: concurrent scraper downloads → flush → verifier says intact."""
    s = IRevScraper(election_ids=["e"])
    s._run = IRevScrapeRun()
    s._run.id = PydanticObjectId()
    s._throttle = irev._Throttle(0)

    async def fake_download(url, save_to):
        i = int(url.rsplit("/", 1)[1].split(".")[0])
        await asyncio.sleep((i * 7 % 5) / 1000)
        if i % 4 == 0:
            return _DownloadResult(False, url, None, reason="ConnectError")
        return _DownloadResult(True, url, 200, 1, hashlib.sha256(url.encode()).hexdigest(),
                               "application/pdf")

    monkeypatch.setattr(s, "_download", fake_download)
    units = [
        _PendingUnit(election_id="e", election_name="t", state_code="24", state_name="LAGOS",
                     lga_code="20", lga_name="L", ward_code="05", ward_name="W",
                     pu_code=f"24/20/05/{i:03d}", pu_name=str(i), portal_url="x",
                     document={"document_url": f"https://example.invalid/{i}.pdf"})
        for i in range(40)
    ]
    await asyncio.gather(*(s._process_unit(u) for u in units))
    # only chained entries: mongomock can't apply the partial index to nulls
    await ScrapeLogEntry.insert_many([e for e in s._batch if e.chain_sequence])
    assert sum(e.chain_sequence is None for e in s._batch) == 10

    r = await verify_image_chain(_coll())
    assert r.valid and r.entries_checked == 30
    assert (r.head_sequence, r.head_hash) == (s._chain_sequence, s._prev_block_hash)
