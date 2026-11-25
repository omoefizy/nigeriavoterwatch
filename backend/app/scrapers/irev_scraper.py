"""
IReV (INEC Results Viewing) EC8A scraper — REST API edition.

The IReV portal (inecelectionresults.ng) is a React SPA backed by a JSON API
(settings.irev_api_base_url). This scraper talks to that API directly with
httpx — no browser — so it can run on a small server.

Traversal:
    /elections (or /elections/{id})  → filter by state / election id
    /elections/{id}/lga              → LGAs, each with its wards
    /elections/{id}/pus?ward={id}    → polling units, each with a `document`

For each polling unit it:
  1. Tries the document URLs in order: backup_url, url, document_url
  2. Streams the file and SHA-256 hashes it chunk by chunk (never held in memory)
  3. Accepts the file only if its magic bytes are PDF / JPEG / PNG — redirects are
     never followed, so a lapsed/parked domain cannot be hashed as an EC8A
  4. Extends the image hash chain:
         block_hash = SHA-256(image_hash + prev_block_hash)
  5. Writes a ScrapeLogEntry document to MongoDB (batched)

Units with no document, or whose every URL fails, are recorded as missing
(document_status = "no_document" / "download_failed"), not as run errors.

Resumable: units already downloaded for an election are skipped on the next run.
Only hashes are kept; the first N files of a run (--save-first) are saved to a
temp folder for OCR testing.

CLI:
    python -m app.scrapers.irev_scraper --state LAGOS --election-id <id> --limit 20
"""
import argparse
import asyncio
import email.utils
import hashlib
import json
import random
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import structlog

from app.config import settings
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus

logger = structlog.get_logger()

# Document URL fields, in the order they are tried
_URL_FIELDS = ("backup_url", "url", "document_url")

# Accepted file signatures → detected content type / extension
_MAGIC = (
    (b"%PDF", "application/pdf", ".pdf"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG", "image/png", ".png"),
)

_CHUNK_SIZE = 64 * 1024
_INSERT_BATCH = 100
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 120.0

DEFAULT_SAMPLE_DIR = Path(tempfile.gettempdir()) / "nigeriavoterwatch_ec8a_samples"


class DocStatus:
    DOWNLOADED = "downloaded"
    NO_DOCUMENT = "no_document"
    DOWNLOAD_FAILED = "download_failed"


# ---------------------------------------------------------------------------
# Image chain helpers (independent namespace from the results chain)
# ---------------------------------------------------------------------------

def _image_genesis_hash() -> str:
    seed = f"{settings.genesis_block_seed}:irev-images"
    return hashlib.sha256(seed.encode()).hexdigest()


def _compute_image_block_hash(image_hash: str, prev_block_hash: str) -> str:
    return hashlib.sha256(f"{image_hash}{prev_block_hash}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# HTTP politeness helpers
# ---------------------------------------------------------------------------

class _Throttle:
    """Enforces a minimum gap between the start of any two requests."""

    def __init__(self, interval: float):
        self._interval = interval
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self):
        async with self._lock:
            delay = self._next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at = time.monotonic() + self._interval


def _is_retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


def _is_retryable_exc(exc: httpx.TransportError) -> bool:
    # DNS failures are permanent for our purposes — move on to the next URL.
    if isinstance(exc, httpx.ConnectError):
        msg = str(exc).lower()
        if any(s in msg for s in ("nodename nor servname", "name or service not known",
                                  "getaddrinfo", "no address associated")):
            return False
    return True


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())


def _detect_type(head: bytes) -> Optional[tuple[str, str]]:
    for magic, ctype, ext in _MAGIC:
        if head.startswith(magic):
            return ctype, ext
    return None


@dataclass
class _DownloadResult:
    ok: bool
    url: str
    http_status: Optional[int] = None
    size: Optional[int] = None
    sha256: Optional[str] = None
    content_type: Optional[str] = None
    saved_path: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class _PendingUnit:
    election_id: str
    election_name: str
    state_code: str
    state_name: str
    lga_code: Optional[str]
    lga_name: Optional[str]
    ward_code: Optional[str]
    ward_name: Optional[str]
    pu_code: Optional[str]
    pu_name: Optional[str]
    portal_url: str
    document: dict


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class IRevScraper:
    def __init__(
        self,
        *,
        state: Optional[str] = None,
        election_ids: Optional[list[str]] = None,
        limit: Optional[int] = None,
        save_first: int = 0,
        save_dir: Optional[Path] = None,
        write_snapshot: bool = True,
        dry_run: bool = False,
    ):
        self.state = state
        self.election_ids = list(election_ids or [])
        self.limit = limit
        self.save_first = save_first
        self.save_dir = Path(save_dir) if save_dir else DEFAULT_SAMPLE_DIR
        self.write_snapshot = write_snapshot and not dry_run
        self.dry_run = dry_run

        self._run: Optional[IRevScrapeRun] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._throttle = _Throttle(settings.irev_request_delay_seconds)
        self._download_sem = asyncio.Semaphore(settings.irev_scraper_concurrency)
        self._api_base = settings.irev_api_base_url.rstrip("/")

        # Image chain state — carried across the entire run
        self._chain_sequence: int = 0
        self._prev_block_hash: str = _image_genesis_hash()

        self._batch: list[ScrapeLogEntry] = []
        self._samples_reserved = 0
        self.saved_samples: list[str] = []
        self.discovery_stopped_early = False
        self.elections_scraped: list[str] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> Optional[IRevScrapeRun]:
        if not self.state and not self.election_ids:
            logger.warning("IReV scrape skipped: no state or election ids configured")
            return None

        self._run = IRevScrapeRun(
            started_at=datetime.now(timezone.utc),
            filter_state=self.state,
            filter_election_ids=self.election_ids,
            filter_limit=self.limit,
        )
        if not self.dry_run:
            await self._run.insert()
            logger.info("MongoDB insert irev_scrape_runs", run_id=str(self._run.id))

        log = logger.bind(run_id=str(self._run.id), dry_run=self.dry_run)
        log.info("IReV scrape run started", state=self.state,
                 election_ids=self.election_ids, limit=self.limit)

        await self._load_chain_tip()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0),
                follow_redirects=False,
                headers={"User-Agent": settings.scraper_user_agent,
                         "Accept-Language": "en-NG,en;q=0.9"},
            ) as http_client:
                self._http = http_client
                pending = await self._discover_units()
                await asyncio.gather(*(self._process_unit(u) for u in pending))

            await self._flush_batch()
            self._run.status = ScrapeRunStatus.COMPLETED

        except Exception as exc:
            log.error("IReV scrape run failed", error=str(exc))
            await self._flush_batch()
            self._run.status = ScrapeRunStatus.FAILED
            self._run.error_message = str(exc)[:1000]

        finally:
            self._run.completed_at = datetime.now(timezone.utc)
            self._run.chain_tip_hash = self._prev_block_hash
            self._run.chain_tip_sequence = self._chain_sequence
            if not self.dry_run:
                await self._run.save()
                logger.info("MongoDB update irev_scrape_runs", run_id=str(self._run.id),
                            status=self._run.status.value)

        if self.write_snapshot:
            try:
                await self._export_snapshot()
            except Exception as exc:
                log.error("Snapshot export failed", error=str(exc))

        log.info(
            "IReV scrape run finished",
            status=self._run.status.value,
            downloaded=self._run.images_downloaded,
            missing=self._run.missing_url_count,
            failed=self._run.broken_url_count,
        )
        return self._run

    # ------------------------------------------------------------------
    # Resume chain from last committed tip
    # ------------------------------------------------------------------

    async def _load_chain_tip(self):
        last = (
            await ScrapeLogEntry.find(ScrapeLogEntry.chain_sequence != None)  # noqa: E711
            .sort("-chain_sequence")
            .limit(1)
            .first_or_none()
        )
        if last and last.image_block_hash:
            self._chain_sequence = last.chain_sequence
            self._prev_block_hash = last.image_block_hash
            logger.info("Resumed image chain", sequence=self._chain_sequence,
                        prev=self._prev_block_hash[:16] + "…")
        else:
            logger.info("Image chain starts at genesis")

    # ------------------------------------------------------------------
    # API access
    # ------------------------------------------------------------------

    async def _backoff(self, attempt: int, retry_after: Optional[str], url: str):
        wait = _retry_after_seconds(retry_after)
        if wait is None:
            wait = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt))
            wait += random.uniform(0, wait / 4)
        else:
            wait = min(wait, _BACKOFF_MAX_SECONDS * 5)
        logger.warning("Backing off", url=url, attempt=attempt + 1,
                       wait_s=round(wait, 1), retry_after=retry_after)
        await asyncio.sleep(wait)

    async def _api_get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self._api_base}{path}"
        retries = settings.irev_max_retries
        for attempt in range(retries + 1):
            await self._throttle.wait()
            t0 = time.monotonic()
            try:
                resp = await self._http.get(url, params=params)
            except httpx.TransportError as exc:
                logger.warning("HTTP GET", url=url, params=params, status=None,
                               error=f"{type(exc).__name__}: {exc}")
                if attempt >= retries or not _is_retryable_exc(exc):
                    raise
                await self._backoff(attempt, None, url)
                continue

            logger.info("HTTP GET", url=str(resp.request.url), status=resp.status_code,
                        bytes=len(resp.content), content_type=resp.headers.get("content-type"),
                        ms=int((time.monotonic() - t0) * 1000))
            if _is_retryable_status(resp.status_code) and attempt < retries:
                await self._backoff(attempt, resp.headers.get("Retry-After"), url)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"unreachable: retries exhausted for {url}")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _state_matches(self, state_obj: Any) -> bool:
        if not self.state:
            return True
        if not isinstance(state_obj, dict):
            return False
        want = self.state.strip().upper()
        return want in (str(state_obj.get("name", "")).upper(), str(state_obj.get("code", "")))

    async def _select_elections(self) -> list[dict]:
        if self.election_ids:
            elections = []
            for eid in self.election_ids:
                data = (await self._api_get(f"/elections/{eid}"))["data"]
                if not self._state_matches(data.get("state")):
                    raise ValueError(
                        f"Election {eid} is in state "
                        f"{(data.get('state') or {}).get('name')!r}, not {self.state!r}"
                    )
                elections.append(data)
            return elections
        data = (await self._api_get("/elections"))["data"]
        return [e for e in data if self._state_matches(e.get("state"))]

    async def _already_downloaded(self, election_id: str) -> set[str]:
        codes = await ScrapeLogEntry.get_motor_collection().distinct(
            "pu_code", {"election_id": election_id, "document_status": DocStatus.DOWNLOADED}
        )
        return {c for c in codes if c}

    async def _discover_units(self) -> list[_PendingUnit]:
        elections = await self._select_elections()
        logger.info("Elections selected", count=len(elections),
                    names=[e.get("full_name") for e in elections])
        if not elections:
            return []

        pending: list[_PendingUnit] = []
        for election in elections:
            eid = election["_id"]
            ename = election.get("full_name") or eid
            state = election.get("state") or {}
            self.elections_scraped.append(ename)
            done = await self._already_downloaded(eid)
            logger.info("Resume check", election=ename, already_downloaded=len(done))

            lgas = (await self._api_get(f"/elections/{eid}/lga"))["data"]
            for lga_entry in lgas:
                lga = lga_entry.get("lga") or {}
                for ward in lga_entry.get("wards") or []:
                    if self._limit_reached(pending):
                        self.discovery_stopped_early = True
                        return pending
                    pus = (await self._api_get(
                        f"/elections/{eid}/pus", params={"ward": ward["_id"]}
                    ))["data"]
                    for rec in pus:
                        pu = rec.get("polling_unit") or {}
                        pu_code = pu.get("pu_code") or rec.get("pu_code")
                        self._run.units_found += 1
                        if pu_code and pu_code in done:
                            self._run.units_skipped_already_done += 1
                            continue
                        if self._limit_reached(pending):
                            self.discovery_stopped_early = True
                            continue  # keep counting this ward's units
                        pending.append(_PendingUnit(
                            election_id=eid,
                            election_name=ename,
                            state_code=str(state.get("code") or ""),
                            state_name=str(state.get("name") or ""),
                            lga_code=lga.get("code"),
                            lga_name=lga.get("name"),
                            ward_code=ward.get("code"),
                            ward_name=ward.get("name"),
                            pu_code=pu_code,
                            pu_name=pu.get("name") or rec.get("name"),
                            portal_url=(f"{settings.irev_base_url.rstrip('/')}/elections/{eid}"
                                        f"/context/pus/lga/{lga.get('_id')}/ward/{ward['_id']}"),
                            document=rec.get("document") or {},
                        ))
        return pending

    def _limit_reached(self, pending: list) -> bool:
        return self.limit is not None and len(pending) >= self.limit

    # ------------------------------------------------------------------
    # Per polling unit
    # ------------------------------------------------------------------

    async def _process_unit(self, unit: _PendingUnit):
        t0 = time.monotonic()
        candidates = [(f, unit.document[f]) for f in _URL_FIELDS if unit.document.get(f)]
        result: Optional[_DownloadResult] = None
        used_field: Optional[str] = None
        attempts: list[str] = []

        if candidates:
            async with self._download_sem:
                save_to = self._reserve_sample_path(unit)
                for field, url in candidates:
                    result = await self._download(url, save_to)
                    attempts.append(f"{field}: {result.reason or result.http_status}")
                    if result.ok:
                        used_field = field
                        break
                if save_to is not None and not (result and result.ok):
                    self._samples_reserved -= 1  # give the sample slot back

        self._run.total_polling_units_visited += 1
        if not candidates:
            status = DocStatus.NO_DOCUMENT
            reason = "no document uploaded"
            self._run.missing_url_count += 1
        elif result and result.ok:
            status = DocStatus.DOWNLOADED
            reason = None
            self._run.images_downloaded += 1
            if result.saved_path:
                self.saved_samples.append(result.saved_path)
        else:
            status = DocStatus.DOWNLOAD_FAILED
            reason = "; ".join(attempts)[:1000]
            self._run.broken_url_count += 1

        ok = result if (result and result.ok) else None
        await self._queue_log_entry(
            unit,
            status=status,
            missing_reason=reason,
            image_url=(ok.url if ok else (candidates[0][1] if candidates else None)),
            source_url_field=used_field,
            image_hash=ok.sha256 if ok else None,
            image_size=ok.size if ok else None,
            content_type=ok.content_type if ok else None,
            image_path=ok.saved_path if ok else None,
            http_status=result.http_status if result else None,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    def _reserve_sample_path(self, unit: _PendingUnit) -> Optional[Path]:
        if self._samples_reserved >= self.save_first:
            return None
        self._samples_reserved += 1
        self.save_dir.mkdir(parents=True, exist_ok=True)
        name = (unit.pu_code or "unknown").replace("/", "_")
        return self.save_dir / f"{unit.election_id}_{name}"

    async def _download(self, url: str, save_to: Optional[Path]) -> _DownloadResult:
        retries = settings.irev_max_retries
        last_status: Optional[int] = None
        for attempt in range(retries + 1):
            await self._throttle.wait()
            t0 = time.monotonic()
            retry_after: Optional[str] = None
            try:
                async with self._http.stream("GET", url) as resp:
                    last_status = resp.status_code
                    server_ctype = resp.headers.get("content-type")
                    if _is_retryable_status(resp.status_code):
                        retry_after = resp.headers.get("Retry-After")
                        logger.warning("HTTP GET document", url=url, status=resp.status_code,
                                       retry_after=retry_after)
                    elif resp.is_redirect:
                        location = resp.headers.get("location", "")
                        logger.warning("HTTP GET document", url=url, status=resp.status_code,
                                       redirect_to=location, note="redirect not followed")
                        return _DownloadResult(False, url, resp.status_code,
                                               reason=f"HTTP {resp.status_code} redirect to {location[:120]}")
                    elif resp.status_code != 200:
                        logger.warning("HTTP GET document", url=url, status=resp.status_code)
                        return _DownloadResult(False, url, resp.status_code,
                                               reason=f"HTTP {resp.status_code}")
                    else:
                        return await self._consume(resp, url, server_ctype, save_to, t0)
            except httpx.TransportError as exc:
                err = f"{type(exc).__name__}: {exc}"
                logger.warning("HTTP GET document", url=url, status=None, error=err)
                if attempt >= retries or not _is_retryable_exc(exc):
                    return _DownloadResult(False, url, None, reason=err[:300])
                await self._backoff(attempt, None, url)
                continue

            if attempt < retries:
                await self._backoff(attempt, retry_after, url)
        return _DownloadResult(False, url, last_status,
                               reason=f"HTTP {last_status} after {retries} retries")

    async def _consume(self, resp: httpx.Response, url: str, server_ctype: Optional[str],
                       save_to: Optional[Path], t0: float) -> _DownloadResult:
        """Stream the body: hash every chunk, optionally write it, never buffer it."""
        hasher = hashlib.sha256()
        size = 0
        head = b""
        detected: Optional[tuple[str, str]] = None
        part = save_to.with_suffix(".part") if save_to else None
        fh = part.open("wb") if part else None
        kept = False
        try:
            async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                if detected is None:
                    head += chunk[: 8 - len(head)]
                    if len(head) >= 4:
                        detected = _detect_type(head)
                        if detected is None:
                            logger.warning("HTTP GET document", url=url, status=200,
                                           content_type=server_ctype, head=head[:8].hex(),
                                           note="rejected: not PDF/JPEG/PNG")
                            return _DownloadResult(
                                False, url, 200,
                                reason=f"unexpected content ({server_ctype}), not PDF/JPEG/PNG")
                hasher.update(chunk)
                size += len(chunk)
                if fh:
                    fh.write(chunk)

            if detected is None:
                return _DownloadResult(False, url, 200, reason=f"body too short ({size} bytes)")

            digest = hasher.hexdigest()
            saved: Optional[str] = None
            if part:
                fh.close()
                final = save_to.with_name(f"{save_to.name}_{digest[:16]}{detected[1]}")
                part.replace(final)
                saved = str(final)
            kept = True
        finally:
            if fh and not fh.closed:
                fh.close()
            if part and not kept:
                part.unlink(missing_ok=True)

        logger.info("HTTP GET document", url=url, status=200, bytes=size,
                    content_type=detected[0], sha256=digest,
                    ms=int((time.monotonic() - t0) * 1000), saved_to=saved)
        return _DownloadResult(True, url, 200, size, digest, detected[0], saved)

    # ------------------------------------------------------------------
    # Image chain
    # ------------------------------------------------------------------

    def _extend_chain(self, image_hash: str) -> tuple[int, str, str]:
        """Returns (new_sequence, prev_used, new_block_hash).

        Deliberately synchronous: read-tip → compute → write-tip contains no
        `await`, so on the single-threaded event loop no other download task can
        run in between. Concurrent downloads therefore extend the chain strictly
        one after another (in completion order) and can never fork it within a
        run. Do not add an `await` here — tests/test_irev_chain.py guards this.
        """
        self._chain_sequence += 1
        prev = self._prev_block_hash
        block = _compute_image_block_hash(image_hash, prev)
        self._prev_block_hash = block
        return self._chain_sequence, prev, block

    # ------------------------------------------------------------------
    # Batched MongoDB inserts
    # ------------------------------------------------------------------

    async def _queue_log_entry(
        self,
        unit: _PendingUnit,
        *,
        status: str,
        missing_reason: Optional[str],
        image_url: Optional[str],
        source_url_field: Optional[str],
        image_hash: Optional[str],
        image_size: Optional[int],
        content_type: Optional[str],
        image_path: Optional[str],
        http_status: Optional[int],
        duration_ms: int,
    ):
        chain_seq: Optional[int] = None
        prev_block: Optional[str] = None
        block_hash: Optional[str] = None
        if image_hash:
            chain_seq, prev_block, block_hash = self._extend_chain(image_hash)

        entry = ScrapeLogEntry(
            run_id=self._run.id,
            election_id=unit.election_id,
            election_name=unit.election_name,
            state_code=unit.state_code,
            state_name=unit.state_name,
            lga_code=unit.lga_code,
            lga_name=unit.lga_name,
            ward_code=unit.ward_code,
            ward_name=unit.ward_name,
            pu_code=unit.pu_code,
            pu_name=unit.pu_name,
            portal_url=unit.portal_url,
            image_url=image_url,
            image_downloaded=image_hash is not None,
            image_hash_sha256=image_hash,
            image_size_bytes=image_size,
            image_stored_path=image_path,
            source_url_field=source_url_field,
            content_type=content_type,
            document_status=status,
            missing_reason=missing_reason,
            chain_sequence=chain_seq,
            prev_image_block_hash=prev_block,
            image_block_hash=block_hash,
            is_missing_url=status == DocStatus.NO_DOCUMENT,
            is_broken_url=status == DocStatus.DOWNLOAD_FAILED,
            http_status=http_status,
            scraped_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
        )
        logger.info(
            "Queued scrape_log entry" + (" (dry run, not stored)" if self.dry_run else ""),
            pu_code=unit.pu_code, ward=unit.ward_name, status=status,
            source=source_url_field, http_status=http_status, size=image_size,
            sha256=image_hash, chain_seq=chain_seq, reason=missing_reason,
        )
        if self.dry_run:
            return
        self._batch.append(entry)
        if len(self._batch) >= _INSERT_BATCH:
            await self._flush_batch()

    async def _flush_batch(self):
        if not self._batch:
            return
        await ScrapeLogEntry.insert_many(self._batch)
        logger.info("MongoDB insert_many scrape_log", documents=len(self._batch))
        self._batch.clear()

    # ------------------------------------------------------------------
    # Public JSON snapshot export
    # ------------------------------------------------------------------

    async def _export_snapshot(self):
        pipeline = [
            {"$match": {"run_id": self._run.id}},
            {
                "$group": {
                    "_id": {"state_code": "$state_code", "state_name": "$state_name"},
                    "total": {"$sum": 1},
                    "downloaded": {"$sum": {"$cond": ["$image_downloaded", 1, 0]}},
                    "missing": {"$sum": {"$cond": ["$is_missing_url", 1, 0]}},
                    "broken": {"$sum": {"$cond": ["$is_broken_url", 1, 0]}},
                }
            },
            {"$sort": {"_id.state_name": 1}},
        ]
        rows = await ScrapeLogEntry.get_motor_collection().aggregate(pipeline).to_list(None)

        snapshot = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scrape_id": str(self._run.id),
            "chain_tip_hash": self._prev_block_hash,
            "chain_tip_sequence": self._chain_sequence,
            "summary": {
                "total_polling_units": self._run.total_polling_units_visited,
                "images_downloaded": self._run.images_downloaded,
                "missing_url": self._run.missing_url_count,
                "broken_url": self._run.broken_url_count,
            },
            "states": [
                {
                    "state_code": r["_id"]["state_code"],
                    "state_name": r["_id"]["state_name"],
                    "total_polling_units": r["total"],
                    "images_downloaded": r["downloaded"],
                    "missing_url": r["missing"],
                    "broken_url": r["broken"],
                }
                for r in rows
            ],
        }

        out = Path(settings.public_snapshot_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2))

        self._run.snapshot_written_at = datetime.now(timezone.utc)
        await self._run.save()
        logger.info("Snapshot written", path=str(out), states=len(rows))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-off IReV EC8A scrape via the REST API.")
    p.add_argument("--state", help="State name or INEC code, e.g. LAGOS or 24")
    p.add_argument("--election-id", action="append", dest="election_ids", default=[],
                   help="IReV election _id (repeatable)")
    p.add_argument("--limit", type=int, help="Max polling units to process this run")
    p.add_argument("--save-first", type=int, default=5,
                   help="Save the first N downloaded files for OCR testing (default 5)")
    p.add_argument("--save-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    p.add_argument("--no-snapshot", action="store_true",
                   help="Do not overwrite the public latest.json snapshot")
    p.add_argument("--dry-run", action="store_true",
                   help="Download and hash, but write nothing to MongoDB")
    args = p.parse_args(argv)
    if not args.state and not args.election_ids:
        p.error("give --state and/or --election-id")
    return args


def _print_summary(scraper: IRevScraper, run: IRevScrapeRun, seconds: float):
    found = f"{run.units_found}" + (" (discovery stopped at --limit)"
                                    if scraper.discovery_stopped_early else "")
    print("\n" + "=" * 60)
    print("IReV scrape summary" + ("  [DRY RUN — nothing stored]" if scraper.dry_run else ""))
    print("=" * 60)
    print(f"Run id              : {run.id}")
    print(f"Status              : {run.status.value}" +
          (f"  ({run.error_message})" if run.error_message else ""))
    print(f"Elections           : {', '.join(scraper.elections_scraped) or '-'}")
    print(f"Units found         : {found}")
    print(f"Skipped (done before): {run.units_skipped_already_done}")
    print(f"Processed this run  : {run.total_polling_units_visited}")
    print(f"  Downloaded        : {run.images_downloaded}")
    print(f"  Missing (no doc)  : {run.missing_url_count}")
    print(f"  Failed download   : {run.broken_url_count}")
    print(f"Chain tip           : seq {run.chain_tip_sequence}  {run.chain_tip_hash}")
    print(f"Samples saved       : {len(scraper.saved_samples)}" +
          (f"  in {scraper.save_dir}" if scraper.saved_samples else ""))
    print(f"Time taken          : {seconds:.1f}s")
    print("=" * 60)


async def _main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    from app.database import close_db, init_db

    await init_db()
    try:
        scraper = IRevScraper(
            state=args.state,
            election_ids=args.election_ids,
            limit=args.limit,
            save_first=args.save_first,
            save_dir=args.save_dir,
            write_snapshot=not args.no_snapshot,
            dry_run=args.dry_run,
        )
        t0 = time.monotonic()
        run = await scraper.run()
        _print_summary(scraper, run, time.monotonic() - t0)
        return 0 if run.status == ScrapeRunStatus.COMPLETED else 1
    finally:
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
