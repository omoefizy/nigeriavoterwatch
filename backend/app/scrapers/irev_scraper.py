"""
IReV (INEC Results Viewing) portal scraper.

Navigates inecelectionresults.ng through the four-level hierarchy:
    State → LGA → Ward → Polling Unit

For each polling unit it:
  1. Locates the EC8A result-sheet image URL
  2. Downloads the image via httpx
  3. Computes SHA-256 of raw bytes immediately on download
  4. Extends the image hash chain:
         block_hash = SHA-256(image_hash + prev_block_hash)
  5. Writes a ScrapeLogEntry document to MongoDB
  6. Flags polling units whose image URL is absent or returns a non-200 status

After the full hierarchy traversal writes a public JSON snapshot to
settings.public_snapshot_path (default: ../frontend/public/latest.json).

Beanie documents are used directly — no SQLAlchemy session required.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
import structlog
from beanie import PydanticObjectId
from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
    TimeoutError as PlaywrightTimeout,
)

from app.config import settings
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Selectors — update if the IReV portal DOM changes
# ---------------------------------------------------------------------------
_SEL = {
    "state_select": "select[data-testid='state-select'], select#state, select[name='state']",
    "lga_select": "select[data-testid='lga-select'], select#lga, select[name='lga']",
    "ward_select": "select[data-testid='ward-select'], select#ward, select[name='ward']",
    "pu_select": (
        "select[data-testid='pu-select'], select#pu, "
        "select[name='pu'], select[name='polling_unit']"
    ),
    "result_image": (
        "img[data-testid='result-sheet'], img.result-sheet, "
        "img[alt*='EC8A'], img[src*='ec8a'], img[src*='result']"
    ),
    "loading": ".loading-overlay, [data-testid='loading'], .spinner",
}

_DROPDOWN_TIMEOUT = 15_000
_IMAGE_TIMEOUT = 20_000
_PAGE_TIMEOUT = 30_000

# Number of ScrapeLogEntry documents to batch-insert at once
_INSERT_BATCH = 100


# ---------------------------------------------------------------------------
# Image chain helpers (independent namespace from the results chain)
# ---------------------------------------------------------------------------

def _image_genesis_hash() -> str:
    seed = f"{settings.genesis_block_seed}:irev-images"
    return hashlib.sha256(seed.encode()).hexdigest()


def _compute_image_block_hash(image_hash: str, prev_block_hash: str) -> str:
    return hashlib.sha256(f"{image_hash}{prev_block_hash}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class IRevScraper:
    def __init__(self):
        self._run: Optional[IRevScrapeRun] = None
        self._semaphore = asyncio.Semaphore(settings.irev_scraper_concurrency)
        self._http: Optional[httpx.AsyncClient] = None

        # Image chain state — carried across the entire run
        self._chain_sequence: int = 0
        self._prev_block_hash: str = _image_genesis_hash()

        # In-memory batch for bulk-insert efficiency
        self._batch: list[ScrapeLogEntry] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self):
        self._run = IRevScrapeRun(started_at=datetime.now(timezone.utc))
        await self._run.insert()

        log = logger.bind(run_id=str(self._run.id))
        log.info("IReV scrape run started")

        await self._load_chain_tip()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={"User-Agent": settings.scraper_user_agent},
            ) as http_client:
                self._http = http_client
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    try:
                        context = await browser.new_context(
                            user_agent=settings.scraper_user_agent,
                            extra_http_headers={"Accept-Language": "en-NG,en;q=0.9"},
                            viewport={"width": 1280, "height": 900},
                        )
                        await self._scrape_all_states(context)
                    finally:
                        await browser.close()

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
            await self._run.save()

        try:
            await self._export_snapshot()
        except Exception as exc:
            log.error("Snapshot export failed", error=str(exc))

        log.info(
            "IReV scrape run finished",
            status=self._run.status,
            images=self._run.images_downloaded,
            missing=self._run.missing_url_count,
            broken=self._run.broken_url_count,
        )

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
            logger.info(
                "Resumed image chain",
                sequence=self._chain_sequence,
                prev=self._prev_block_hash[:16] + "…",
            )

    # ------------------------------------------------------------------
    # Hierarchy traversal
    # ------------------------------------------------------------------

    async def _scrape_all_states(self, context: BrowserContext):
        page = await context.new_page()
        try:
            await page.goto(settings.irev_base_url, wait_until="networkidle", timeout=_PAGE_TIMEOUT)
            await self._dismiss_loading(page)

            state_options = await self._get_select_options(page, _SEL["state_select"])
            if not state_options:
                logger.warning("No state options found on IReV portal")
                return

            for state_code, state_name in state_options:
                try:
                    await self._scrape_state(context, page, state_code, state_name)
                except Exception as exc:
                    logger.error("State scrape failed", state=state_name, error=str(exc))
        finally:
            await page.close()

    async def _scrape_state(
        self, context: BrowserContext, page: Page, state_code: str, state_name: str
    ):
        logger.bind(state=state_name).info("Scraping state")
        await self._select_option(page, _SEL["state_select"], state_code)
        await self._dismiss_loading(page)

        lga_options = await self._get_select_options(page, _SEL["lga_select"])
        if not lga_options:
            logger.bind(state=state_name).warning("No LGAs found")
            return

        for lga_code, lga_name in lga_options:
            try:
                await self._scrape_lga(context, page, state_code, state_name, lga_code, lga_name)
            except Exception as exc:
                logger.error("LGA scrape failed", state=state_name, lga=lga_name, error=str(exc))

    async def _scrape_lga(
        self,
        context: BrowserContext,
        page: Page,
        state_code: str,
        state_name: str,
        lga_code: str,
        lga_name: str,
    ):
        await self._select_option(page, _SEL["lga_select"], lga_code)
        await self._dismiss_loading(page)

        ward_options = await self._get_select_options(page, _SEL["ward_select"])
        if not ward_options:
            return

        for ward_code, ward_name in ward_options:
            try:
                await self._scrape_ward(
                    context, page,
                    state_code, state_name,
                    lga_code, lga_name,
                    ward_code, ward_name,
                )
            except Exception as exc:
                logger.error(
                    "Ward scrape failed",
                    state=state_name, lga=lga_name, ward=ward_name,
                    error=str(exc),
                )

    async def _scrape_ward(
        self,
        context: BrowserContext,
        page: Page,
        state_code: str,
        state_name: str,
        lga_code: str,
        lga_name: str,
        ward_code: str,
        ward_name: str,
    ):
        await self._select_option(page, _SEL["ward_select"], ward_code)
        await self._dismiss_loading(page)

        pu_options = await self._get_select_options(page, _SEL["pu_select"])
        if not pu_options:
            await self._queue_log_entry(
                state_code=state_code, state_name=state_name,
                lga_code=lga_code, lga_name=lga_name,
                ward_code=ward_code, ward_name=ward_name,
                portal_url=page.url,
                is_missing_url=True,
                error_message="No polling units found for ward",
            )
            return

        for pu_code, pu_name in pu_options:
            async with self._semaphore:
                await self._scrape_polling_unit(
                    page,
                    state_code, state_name,
                    lga_code, lga_name,
                    ward_code, ward_name,
                    pu_code, pu_name,
                )

    async def _scrape_polling_unit(
        self,
        page: Page,
        state_code: str,
        state_name: str,
        lga_code: str,
        lga_name: str,
        ward_code: str,
        ward_name: str,
        pu_code: str,
        pu_name: str,
    ):
        t0 = time.monotonic()
        await self._select_option(page, _SEL["pu_select"], pu_code)
        await self._dismiss_loading(page)

        portal_url = page.url
        image_url: Optional[str] = None
        is_missing_url = False
        is_broken_url = False
        http_status: Optional[int] = None
        error_message: Optional[str] = None
        image_hash: Optional[str] = None
        image_size: Optional[int] = None
        image_path: Optional[str] = None

        try:
            image_url = await self._find_result_image_url(page)
        except Exception as exc:
            logger.warning("Could not find result image", pu_code=pu_code, error=str(exc))

        if not image_url:
            is_missing_url = True
            self._run.missing_url_count += 1
            logger.warning("Missing EC8A image URL", pu_code=pu_code)
        else:
            try:
                raw_bytes, http_status = await self._download_image(image_url)
                image_hash = hashlib.sha256(raw_bytes).hexdigest()
                image_size = len(raw_bytes)
                image_path = await self._save_image(raw_bytes, pu_code, image_hash)
                self._run.images_downloaded += 1
            except httpx.HTTPStatusError as exc:
                is_broken_url = True
                http_status = exc.response.status_code
                error_message = f"HTTP {http_status}"
                self._run.broken_url_count += 1
            except Exception as exc:
                is_broken_url = True
                error_message = str(exc)[:500]
                self._run.broken_url_count += 1

        self._run.total_polling_units_visited += 1

        await self._queue_log_entry(
            state_code=state_code, state_name=state_name,
            lga_code=lga_code, lga_name=lga_name,
            ward_code=ward_code, ward_name=ward_name,
            pu_code=pu_code, pu_name=pu_name,
            portal_url=portal_url,
            image_url=image_url,
            image_hash=image_hash,
            image_size=image_size,
            image_path=image_path,
            http_status=http_status,
            is_missing_url=is_missing_url,
            is_broken_url=is_broken_url,
            duration_ms=int((time.monotonic() - t0) * 1000),
            error_message=error_message,
        )

    # ------------------------------------------------------------------
    # Image download and local storage
    # ------------------------------------------------------------------

    async def _download_image(self, url: str) -> tuple[bytes, int]:
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.content, resp.status_code

    async def _save_image(self, raw_bytes: bytes, pu_code: str, image_hash: str) -> str:
        storage_dir = Path(settings.image_storage_dir)
        shard = image_hash[:2]
        target = storage_dir / shard
        target.mkdir(parents=True, exist_ok=True)
        fname = f"{pu_code.replace('/', '_')}_{image_hash[:16]}.jpg"
        dest = target / fname
        if not dest.exists():
            dest.write_bytes(raw_bytes)
        return str(dest)

    # ------------------------------------------------------------------
    # Image chain
    # ------------------------------------------------------------------

    def _extend_chain(self, image_hash: str) -> tuple[int, str, str]:
        """Returns (new_sequence, prev_used, new_block_hash)."""
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
        *,
        state_code: str,
        state_name: str,
        lga_code: Optional[str] = None,
        lga_name: Optional[str] = None,
        ward_code: Optional[str] = None,
        ward_name: Optional[str] = None,
        pu_code: Optional[str] = None,
        pu_name: Optional[str] = None,
        portal_url: Optional[str] = None,
        image_url: Optional[str] = None,
        image_hash: Optional[str] = None,
        image_size: Optional[int] = None,
        image_path: Optional[str] = None,
        http_status: Optional[int] = None,
        is_missing_url: bool = False,
        is_broken_url: bool = False,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ):
        chain_seq: Optional[int] = None
        prev_block: Optional[str] = None
        block_hash: Optional[str] = None

        if image_hash:
            chain_seq, prev_block, block_hash = self._extend_chain(image_hash)

        entry = ScrapeLogEntry(
            run_id=self._run.id,
            state_code=state_code,
            state_name=state_name,
            lga_code=lga_code,
            lga_name=lga_name,
            ward_code=ward_code,
            ward_name=ward_name,
            pu_code=pu_code,
            pu_name=pu_name,
            portal_url=portal_url,
            image_url=image_url,
            image_downloaded=image_hash is not None,
            image_hash_sha256=image_hash,
            image_size_bytes=image_size,
            image_stored_path=image_path,
            chain_sequence=chain_seq,
            prev_image_block_hash=prev_block,
            image_block_hash=block_hash,
            is_missing_url=is_missing_url,
            is_broken_url=is_broken_url,
            http_status=http_status,
            scraped_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            error_message=error_message,
        )
        self._batch.append(entry)

        if len(self._batch) >= _INSERT_BATCH:
            await self._flush_batch()

    async def _flush_batch(self):
        if not self._batch:
            return
        await ScrapeLogEntry.insert_many(self._batch)
        self._batch.clear()

    # ------------------------------------------------------------------
    # Playwright helpers
    # ------------------------------------------------------------------

    async def _get_select_options(self, page: Page, selector: str) -> list[tuple[str, str]]:
        try:
            await page.wait_for_selector(selector, timeout=_DROPDOWN_TIMEOUT)
        except PlaywrightTimeout:
            return []
        return await page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return [];
                return Array.from(el.options)
                    .filter(o => o.value && o.value !== '' && o.value !== '0')
                    .map(o => [o.value.trim(), o.text.trim()]);
            }""",
            selector,
        )

    async def _select_option(self, page: Page, selector: str, value: str):
        await page.wait_for_selector(selector, timeout=_DROPDOWN_TIMEOUT)
        await page.select_option(selector, value=value)
        await page.wait_for_timeout(800)

    async def _dismiss_loading(self, page: Page):
        try:
            await page.wait_for_selector(_SEL["loading"], state="hidden", timeout=10_000)
        except PlaywrightTimeout:
            pass

    async def _find_result_image_url(self, page: Page) -> Optional[str]:
        try:
            await page.wait_for_selector(_SEL["result_image"], timeout=_IMAGE_TIMEOUT)
            src = await page.get_attribute(_SEL["result_image"], "src")
            if src:
                return src if src.startswith("http") else urljoin(page.url, src)
        except PlaywrightTimeout:
            pass

        # Fallback: scan all <img> for EC8A-related src patterns
        srcs: list[str] = await page.evaluate(
            """() => Array.from(document.images)
                    .map(i => i.src)
                    .filter(s => s && (
                        s.includes('ec8') || s.includes('result') ||
                        s.includes('sheet') || s.includes('form')
                    ))"""
        )
        if srcs:
            return srcs[0] if srcs[0].startswith("http") else urljoin(page.url, srcs[0])
        return None

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
        rows = await ScrapeLogEntry.get_pymongo_collection().aggregate(pipeline).to_list(None)

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
