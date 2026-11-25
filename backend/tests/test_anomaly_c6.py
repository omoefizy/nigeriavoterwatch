"""
C6 (IMAGE_HASH_CHANGED) regression tests.

C6 used to group scrape-log entries by pu_code alone, so one polling unit that
appears in two different elections (each with its own, legitimately different
EC8A) was reported as a CRITICAL "image replaced" anomaly — against every
active election. It now groups by (election_id, pu_code).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.elections import Election, ElectionStatus, ElectionType
from app.models.observers import Anomaly, AnomalySeverity, AnomalyType
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry
from app.services.anomaly_detector import AnomalyDetector

PU_CODE = "24/20/05/010"  # Surulere — in both the 2023 governorship and 2024 reps elections
HASH_A = "a" * 64
HASH_B = "b" * 64
T0 = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


async def _election(irev_id: str, etype: ElectionType) -> Election:
    e = Election(
        name=f"test {irev_id}",
        election_type=etype,
        status=ElectionStatus.ONGOING,
        election_date=datetime.now(timezone.utc),  # recent → C4 stays quiet
        irev_election_id=irev_id,
    )
    await e.insert()
    return e


async def _entry(run: IRevScrapeRun, irev_id: str, image_hash: str, seq: int) -> None:
    await ScrapeLogEntry(
        run_id=run.id,
        election_id=irev_id,
        state_code="24", state_name="LAGOS",
        lga_name="SURULERE", ward_name="YABA/OJUELEGBA",
        pu_code=PU_CODE, pu_name="JUNCTION OF OLAJUWON ST.",
        image_downloaded=True,
        image_hash_sha256=image_hash,
        document_status="downloaded",
        chain_sequence=seq,
        scraped_at=T0 + timedelta(minutes=seq),
    ).insert()


async def _run() -> IRevScrapeRun:
    run = IRevScrapeRun()
    await run.insert()
    return run


@pytest.mark.asyncio
async def test_same_pu_in_two_elections_with_different_hashes_is_not_flagged(mock_db):
    gov = await _election("irev-gov-2023", ElectionType.GUBERNATORIAL)
    reps = await _election("irev-reps-2024", ElectionType.HOUSE_OF_REPS)
    run = await _run()
    await _entry(run, gov.irev_election_id, HASH_A, seq=1)
    await _entry(run, reps.irev_election_id, HASH_B, seq=2)

    await AnomalyDetector(run).run()

    flagged = await Anomaly.find({"anomaly_type": AnomalyType.IMAGE_HASH_CHANGED.value}).to_list()
    assert flagged == []


@pytest.mark.asyncio
async def test_same_pu_same_election_with_changed_hash_is_critical(mock_db):
    gov = await _election("irev-gov-2023", ElectionType.GUBERNATORIAL)
    other = await _election("irev-reps-2024", ElectionType.HOUSE_OF_REPS)
    run = await _run()
    await _entry(run, gov.irev_election_id, HASH_A, seq=1)
    await _entry(run, gov.irev_election_id, HASH_B, seq=2)

    total, critical = await AnomalyDetector(run).run()

    flagged = await Anomaly.find({"anomaly_type": AnomalyType.IMAGE_HASH_CHANGED.value}).to_list()
    assert len(flagged) == 1
    a = flagged[0]
    assert a.severity == AnomalySeverity.CRITICAL
    assert a.election_id == gov.id          # attributed only to its own election,
    assert a.election_id != other.id        # not to every active election
    assert a.details["pu_code"] == PU_CODE
    assert a.details["unique_hash_count"] == 2
    assert a.details["first_hash"] == HASH_A and a.details["last_hash"] == HASH_B
    assert critical >= 1
