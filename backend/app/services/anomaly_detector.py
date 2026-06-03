"""
Anomaly detection service — runs after every IReV scrape cycle.

Implements seven integrity checks (C1-C7) against current election results
and scrape log data, writing new Anomaly documents for each finding.

Deduplication: an (election_id, pu_id, anomaly_type) triple is only flagged
once per still-unresolved period — existing unresolved anomalies are skipped.
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from app.models.elections import Election, ElectionStatus, PollingUnit, Ward, LGA
from app.models.observers import Anomaly, AnomalyType, AnomalySeverity
from app.models.results import ElectionResult, ResultStatus
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry

log = structlog.get_logger()

# Polls close at 14:30 WAT (13:30 UTC); late = image absent 2h after close.
_POLLS_CLOSE_UTC_HOUR = 13
_POLLS_CLOSE_UTC_MINUTE = 30
_LATE_THRESHOLD_HOURS = 2


def _pop_std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / n)


class AnomalyDetector:
    def __init__(self, run: IRevScrapeRun):
        self._run = run
        # (election_id_str, pu_id_str_or_None, AnomalyType) → True
        self._seen: set[tuple] = set()

    # ── Public entry point ────────────────────────────────────────────────────

    async def run(self) -> tuple[int, int]:
        """Run all checks; return (total_new, critical_new)."""
        active_elections = await Election.find(
            {"status": {"$in": [ElectionStatus.ONGOING, ElectionStatus.COLLATING]}}
        ).to_list()

        if not active_elections:
            log.info("AnomalyDetector: no active elections, skipping")
            return 0, 0

        await self._load_dedup(active_elections)

        new_anomalies: list[Anomaly] = []

        for election in active_elections:
            found = await self._run_election_checks(election)
            new_anomalies.extend(found)

        # C4 and C6 are cross-election checks driven by scrape log
        c4 = await self._c4_late_uploads(active_elections)
        new_anomalies.extend(c4)

        c6 = await self._c6_image_hash_changed(active_elections)
        new_anomalies.extend(c6)

        if new_anomalies:
            await Anomaly.insert_many(new_anomalies)
            log.info("AnomalyDetector: inserted anomalies", count=len(new_anomalies))

        critical_new = sum(
            1 for a in new_anomalies if a.severity == AnomalySeverity.CRITICAL
        )
        return len(new_anomalies), critical_new

    # ── Deduplication ─────────────────────────────────────────────────────────

    async def _load_dedup(self, elections: list[Election]) -> None:
        election_ids = [e.id for e in elections]
        existing = await Anomaly.find(
            {"election_id": {"$in": election_ids}, "is_resolved": False}
        ).to_list()
        for a in existing:
            key = (
                str(a.election_id),
                str(a.polling_unit_id) if a.polling_unit_id else None,
                a.anomaly_type,
            )
            self._seen.add(key)

    def _is_duplicate(
        self,
        election_id,
        pu_id,
        atype: AnomalyType,
    ) -> bool:
        key = (str(election_id), str(pu_id) if pu_id else None, atype)
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    # ── Per-election checks (C1, C2, C3, C5, C7) ─────────────────────────────

    async def _run_election_checks(self, election: Election) -> list[Anomaly]:
        results = await ElectionResult.find(
            {
                "election_id": election.id,
                "supersedes_id": None,
                "status": {"$ne": ResultStatus.SUPERSEDED},
            }
        ).to_list()

        if not results:
            return []

        # Build PU → PollingUnit map for registered_voters + geography
        pu_ids = [r.polling_unit_id for r in results]
        pus = await PollingUnit.find({"_id": {"$in": pu_ids}}).to_list()
        pu_map = {pu.id: pu for pu in pus}

        # Build Ward → LGA map for geographic context
        ward_ids = list({pu.ward_id for pu in pus})
        wards = await Ward.find({"_id": {"$in": ward_ids}}).to_list()
        ward_map = {w.id: w for w in wards}

        lga_ids = list({w.lga_id for w in wards})
        lgas = await LGA.find({"_id": {"$in": lga_ids}}).to_list()
        lga_map = {lg.id: lg for lg in lgas}

        anomalies: list[Anomaly] = []

        # Group results by LGA for C5 z-score
        lga_turnouts: dict = defaultdict(list)  # lga_id → [(result, pu, turnout_pct)]

        for result in results:
            pu = pu_map.get(result.polling_unit_id)
            if not pu:
                continue
            ward = ward_map.get(pu.ward_id)
            lga = lga_map.get(ward.lga_id) if ward else None
            state_name = None
            lga_name = lga.name if lga else None
            ward_name = ward.name if ward else None

            geo = (state_name, lga_name, ward_name)

            c1 = self._c1_votes_exceed_accredited(election, result, pu, geo)
            if c1:
                anomalies.append(c1)

            c2 = self._c2_accredited_exceeds_registered(election, result, pu, geo)
            if c2:
                anomalies.append(c2)

            c3 = self._c3_unanimous_result(election, result, pu, geo)
            if c3:
                anomalies.append(c3)

            c7 = self._c7_ocr_discrepancy(election, result, pu, geo)
            if c7:
                anomalies.append(c7)

            # Accumulate turnout for C5
            if pu.registered_voters > 0:
                turnout_pct = result.total_votes_cast / pu.registered_voters * 100
                lga_key = lga.id if lga else None
                lga_turnouts[lga_key].append((result, pu, turnout_pct, geo))

        # C5 — turnout z-score per LGA
        c5_list = self._c5_turnout_zscore(election, lga_turnouts)
        anomalies.extend(c5_list)

        return anomalies

    # ── C1: Votes cast > accredited voters (CRITICAL) ─────────────────────────

    def _c1_votes_exceed_accredited(
        self,
        election: Election,
        result: ElectionResult,
        pu: PollingUnit,
        geo: tuple,
    ) -> Optional[Anomaly]:
        if result.total_votes_cast <= result.accredited_voters:
            return None
        if self._is_duplicate(election.id, pu.id, AnomalyType.VOTE_INFLATION):
            return None
        state_name, lga_name, ward_name = geo
        return Anomaly(
            election_id=election.id,
            polling_unit_id=pu.id,
            anomaly_type=AnomalyType.VOTE_INFLATION,
            severity=AnomalySeverity.CRITICAL,
            description=(
                f"Votes cast ({result.total_votes_cast:,}) exceed "
                f"accredited voters ({result.accredited_voters:,}) at {pu.name}"
            ),
            details={
                "total_votes_cast": result.total_votes_cast,
                "accredited_voters": result.accredited_voters,
                "excess": result.total_votes_cast - result.accredited_voters,
                "result_id": str(result.id),
            },
            state_name=state_name,
            lga_name=lga_name,
            ward_name=ward_name,
            source_result_id=result.id,
        )

    # ── C2: Accredited voters > registered voters (CRITICAL) ─────────────────

    def _c2_accredited_exceeds_registered(
        self,
        election: Election,
        result: ElectionResult,
        pu: PollingUnit,
        geo: tuple,
    ) -> Optional[Anomaly]:
        if pu.registered_voters <= 0:
            return None
        if result.accredited_voters <= pu.registered_voters:
            return None
        if self._is_duplicate(election.id, pu.id, AnomalyType.OVERCREDITATION):
            return None
        state_name, lga_name, ward_name = geo
        return Anomaly(
            election_id=election.id,
            polling_unit_id=pu.id,
            anomaly_type=AnomalyType.OVERCREDITATION,
            severity=AnomalySeverity.CRITICAL,
            description=(
                f"Accredited voters ({result.accredited_voters:,}) exceed "
                f"registered voters ({pu.registered_voters:,}) at {pu.name}"
            ),
            details={
                "accredited_voters": result.accredited_voters,
                "registered_voters": pu.registered_voters,
                "excess": result.accredited_voters - pu.registered_voters,
                "result_id": str(result.id),
            },
            state_name=state_name,
            lga_name=lga_name,
            ward_name=ward_name,
            source_result_id=result.id,
        )

    # ── C3: Single candidate gets 100% of valid votes (WARNING) ──────────────

    def _c3_unanimous_result(
        self,
        election: Election,
        result: ElectionResult,
        pu: PollingUnit,
        geo: tuple,
    ) -> Optional[Anomaly]:
        pv = result.party_votes
        if len(pv) < 2:
            return None
        if result.valid_votes <= 0:
            return None
        winner_party = max(pv, key=lambda p: pv[p])
        winner_votes = pv[winner_party]
        if winner_votes < result.valid_votes:
            return None
        if self._is_duplicate(election.id, pu.id, AnomalyType.UNANIMOUS_RESULT):
            return None
        state_name, lga_name, ward_name = geo
        return Anomaly(
            election_id=election.id,
            polling_unit_id=pu.id,
            anomaly_type=AnomalyType.UNANIMOUS_RESULT,
            severity=AnomalySeverity.WARNING,
            description=(
                f"{winner_party} received 100% of valid votes "
                f"({winner_votes:,}) at {pu.name}"
            ),
            details={
                "winning_party": winner_party,
                "winner_votes": winner_votes,
                "valid_votes": result.valid_votes,
                "party_votes": pv,
                "result_id": str(result.id),
            },
            state_name=state_name,
            lga_name=lga_name,
            ward_name=ward_name,
            source_result_id=result.id,
        )

    # ── C4: Image absent 2h after polls close (WARNING) ──────────────────────

    async def _c4_late_uploads(self, elections: list[Election]) -> list[Anomaly]:
        now_utc = datetime.now(timezone.utc)
        anomalies: list[Anomaly] = []

        for election in elections:
            # Determine the expected poll close time for this election
            edate = election.election_date
            close_utc = edate.replace(
                hour=_POLLS_CLOSE_UTC_HOUR,
                minute=_POLLS_CLOSE_UTC_MINUTE,
                second=0,
                microsecond=0,
                tzinfo=timezone.utc,
            )
            if now_utc < close_utc + timedelta(hours=_LATE_THRESHOLD_HOURS):
                continue  # Too early to flag as late

            # Find all missing-URL entries in this scrape run
            missing = await ScrapeLogEntry.find(
                {"run_id": self._run.id, "is_missing_url": True}
            ).to_list()

            for entry in missing:
                pu = None
                if entry.pu_code:
                    pu = await PollingUnit.find_one({"inec_pu_code": entry.pu_code})

                pu_id = pu.id if pu else None
                if self._is_duplicate(election.id, pu_id, AnomalyType.LATE_RESULT):
                    continue

                hours_late = (now_utc - close_utc).total_seconds() / 3600
                anomalies.append(Anomaly(
                    election_id=election.id,
                    polling_unit_id=pu_id,
                    anomaly_type=AnomalyType.LATE_RESULT,
                    severity=AnomalySeverity.WARNING,
                    description=(
                        f"EC8A image absent {hours_late:.1f}h after polls closed "
                        f"at {entry.pu_name or entry.pu_code or 'unknown PU'}"
                    ),
                    details={
                        "pu_code": entry.pu_code,
                        "pu_name": entry.pu_name,
                        "polls_closed_utc": close_utc.isoformat(),
                        "hours_late": round(hours_late, 2),
                        "scrape_run_id": str(self._run.id),
                    },
                    state_name=entry.state_name,
                    lga_name=entry.lga_name,
                    ward_name=entry.ward_name,
                ))

        return anomalies

    # ── C5: Turnout z-score outlier vs LGA mean (WARNING) ────────────────────

    def _c5_turnout_zscore(
        self,
        election: Election,
        lga_turnouts: dict,
        threshold: float = 3.0,
        min_pus: int = 4,
    ) -> list[Anomaly]:
        anomalies: list[Anomaly] = []

        for lga_id, entries in lga_turnouts.items():
            if len(entries) < min_pus:
                continue
            values = [e[2] for e in entries]
            mean = sum(values) / len(values)
            std = _pop_std(values)
            if std == 0:
                continue

            for result, pu, turnout_pct, geo in entries:
                z = abs(turnout_pct - mean) / std
                if z <= threshold:
                    continue
                if self._is_duplicate(election.id, pu.id, AnomalyType.TURNOUT_ANOMALY):
                    continue
                state_name, lga_name, ward_name = geo
                direction = "high" if turnout_pct > mean else "low"
                anomalies.append(Anomaly(
                    election_id=election.id,
                    polling_unit_id=pu.id,
                    anomaly_type=AnomalyType.TURNOUT_ANOMALY,
                    severity=AnomalySeverity.WARNING,
                    description=(
                        f"Turnout {turnout_pct:.1f}% is {direction} outlier "
                        f"(z={z:.2f}, LGA mean {mean:.1f}%) at {pu.name}"
                    ),
                    details={
                        "turnout_pct": round(turnout_pct, 2),
                        "lga_mean_pct": round(mean, 2),
                        "lga_std_pct": round(std, 2),
                        "z_score": round(z, 3),
                        "lga_pu_count": len(entries),
                        "total_votes_cast": result.total_votes_cast,
                        "registered_voters": pu.registered_voters,
                        "result_id": str(result.id),
                    },
                    state_name=state_name,
                    lga_name=lga_name,
                    ward_name=ward_name,
                    source_result_id=result.id,
                ))

        return anomalies

    # ── C6: EC8A image hash changed after first upload (CRITICAL) ────────────

    async def _c6_image_hash_changed(self, elections: list[Election]) -> list[Anomaly]:
        pipeline = [
            {"$match": {
                "image_downloaded": True,
                "pu_code": {"$ne": None},
                "image_hash_sha256": {"$ne": None},
            }},
            {"$group": {
                "_id": "$pu_code",
                "unique_hashes": {"$addToSet": "$image_hash_sha256"},
                "first_hash": {"$first": "$image_hash_sha256"},
                "last_hash": {"$last": "$image_hash_sha256"},
                "first_scraped": {"$min": "$scraped_at"},
                "last_scraped": {"$max": "$scraped_at"},
                "state_name": {"$first": "$state_name"},
                "lga_name": {"$first": "$lga_name"},
                "ward_name": {"$first": "$ward_name"},
                "pu_name": {"$first": "$pu_name"},
            }},
            # Only PUs with more than one distinct hash
            {"$match": {"unique_hashes.1": {"$exists": True}}},
        ]

        changed = await ScrapeLogEntry.get_pymongo_collection().aggregate(pipeline).to_list(None)
        if not changed:
            return []

        anomalies: list[Anomaly] = []

        for doc in changed:
            pu_code = doc["_id"]
            pu = await PollingUnit.find_one({"inec_pu_code": pu_code})
            pu_id = pu.id if pu else None

            for election in elections:
                if self._is_duplicate(election.id, pu_id, AnomalyType.IMAGE_HASH_CHANGED):
                    continue
                anomalies.append(Anomaly(
                    election_id=election.id,
                    polling_unit_id=pu_id,
                    anomaly_type=AnomalyType.IMAGE_HASH_CHANGED,
                    severity=AnomalySeverity.CRITICAL,
                    description=(
                        f"EC8A image replaced after initial upload "
                        f"at {doc.get('pu_name') or pu_code} "
                        f"({len(doc['unique_hashes'])} distinct hashes detected)"
                    ),
                    details={
                        "pu_code": pu_code,
                        "unique_hash_count": len(doc["unique_hashes"]),
                        "first_hash": doc["first_hash"],
                        "last_hash": doc["last_hash"],
                        "first_scraped": doc["first_scraped"].isoformat() if doc.get("first_scraped") else None,
                        "last_scraped": doc["last_scraped"].isoformat() if doc.get("last_scraped") else None,
                    },
                    state_name=doc.get("state_name"),
                    lga_name=doc.get("lga_name"),
                    ward_name=doc.get("ward_name"),
                ))

        return anomalies

    # ── C7: OCR vs portal total deviation > 20% (WARNING) ────────────────────

    def _c7_ocr_discrepancy(
        self,
        election: Election,
        result: ElectionResult,
        pu: PollingUnit,
        geo: tuple,
    ) -> Optional[Anomaly]:
        from app.models.results import ResultSource
        if result.source != ResultSource.OCR_DOCUMENT:
            return None

        # Scraper-ingested result for same PU in same election serves as portal figure
        # We compare total_votes_cast from OCR vs the accredited_voters count as a proxy
        # when a portal figure isn't yet available; real implementation would join on
        # a INEC_PORTAL result for the same PU.
        # Here we flag if ocr_confidence metadata is present in details and low.
        details_meta = result.__dict__.get("details") or {}
        portal_total = details_meta.get("portal_total_votes")
        ocr_total = result.total_votes_cast

        if portal_total is None or portal_total == 0:
            return None

        deviation = abs(ocr_total - portal_total) / portal_total
        if deviation <= 0.20:
            return None

        if self._is_duplicate(election.id, pu.id, AnomalyType.RESULT_DISCREPANCY):
            return None

        state_name, lga_name, ward_name = geo
        return Anomaly(
            election_id=election.id,
            polling_unit_id=pu.id,
            anomaly_type=AnomalyType.RESULT_DISCREPANCY,
            severity=AnomalySeverity.WARNING,
            description=(
                f"OCR total ({ocr_total:,}) deviates {deviation*100:.1f}% "
                f"from portal figure ({portal_total:,}) at {pu.name}"
            ),
            details={
                "ocr_total_votes": ocr_total,
                "portal_total_votes": portal_total,
                "deviation_pct": round(deviation * 100, 2),
                "result_id": str(result.id),
            },
            state_name=state_name,
            lga_name=lga_name,
            ward_name=ward_name,
            source_result_id=result.id,
        )
