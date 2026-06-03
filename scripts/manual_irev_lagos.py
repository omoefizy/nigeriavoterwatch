"""
Manual one-time IReV scrape — LAGOS STATE ONLY.

Connects to MongoDB, runs the IReV scraper filtered to Lagos, and prints
every significant step: URL navigation, dropdown options, image downloads,
hash chain extensions, and MongoDB write counts.

Usage (from project root):
    python scripts/manual_irev_lagos.py

Or from backend/:
    python ../scripts/manual_irev_lagos.py
"""

import asyncio
import hashlib
import logging
import sys
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

# ── path setup ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

# ── logging: plain stdout, step-by-step ─────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("lagos_scrape")

# Quiet noisy libraries
for _lib in ("httpx", "httpcore", "playwright", "asyncio", "motor", "beanie"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

# ── structlog: pipe through Python logging so everything lands in stdout ─────
import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
    logger_factory=structlog.PrintLoggerFactory(),
)

# ── app imports (after path setup) ──────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(_HERE, "..", ".env"))

from app.database import init_db, close_db
from app.config import settings
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus
from app.scrapers.irev_scraper import IRevScraper

# Playwright
from playwright.async_api import (
    BrowserContext,
    Page,
    async_playwright,
    TimeoutError as PlaywrightTimeout,
)

import httpx


# ── selectors (same as parent) ───────────────────────────────────────────────
_SEL = {
    "state_select": "select[data-testid='state-select'], select#state, select[name='state']",
    "lga_select":   "select[data-testid='lga-select'],   select#lga,   select[name='lga']",
    "ward_select":  "select[data-testid='ward-select'],  select#ward,  select[name='ward']",
    "pu_select":    (
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
_IMAGE_TIMEOUT    = 20_000
_PAGE_TIMEOUT     = 30_000
_INSERT_BATCH     = 100


class LagosIRevScraper(IRevScraper):
    """
    IRevScraper subclass that scrapes ONLY Lagos state.
    Every significant action is logged to stdout for manual inspection.
    """

    def __init__(self):
        super().__init__()
        self._lgas_found = 0
        self._wards_found = 0
        self._pus_found = 0
        self._images_ok = 0
        self._images_missing = 0
        self._images_broken = 0
        self._db_inserts = 0

    # ── top-level run (override to log DB stats at the end) ─────────────────

    async def run(self):
        log.info("=" * 60)
        log.info("LAGOS IReV MANUAL SCRAPE — START")
        log.info(f"  IReV base URL : {settings.irev_base_url}")
        log.info(f"  MongoDB db    : {settings.mongodb_db_name}")
        log.info(f"  Genesis seed  : {settings.genesis_block_seed}")
        log.info("=" * 60)

        await super().run()

        log.info("=" * 60)
        log.info("FINAL SUMMARY")
        log.info(f"  Run ID          : {self._run.id if self._run else 'n/a'}")
        log.info(f"  Status          : {self._run.status if self._run else 'n/a'}")
        log.info(f"  LGAs traversed  : {self._lgas_found}")
        log.info(f"  Wards traversed : {self._wards_found}")
        log.info(f"  PUs visited     : {self._pus_found}")
        log.info(f"  Images OK       : {self._images_ok}")
        log.info(f"  Images missing  : {self._images_missing}")
        log.info(f"  Images broken   : {self._images_broken}")
        log.info(f"  MongoDB inserts : {self._db_inserts}")
        log.info(f"  Chain tip seq   : {self._chain_sequence}")
        log.info(f"  Chain tip hash  : {self._prev_block_hash[:24]}…")
        if self._run and self._run.error_message:
            log.error(f"  Error           : {self._run.error_message}")
        log.info("=" * 60)

    # ── only visit Lagos state ───────────────────────────────────────────────

    async def _scrape_all_states(self, context: BrowserContext):
        page = await context.new_page()
        try:
            log.info(f"[NAV] Navigating to IReV portal → {settings.irev_base_url}")
            await page.goto(
                settings.irev_base_url, wait_until="networkidle", timeout=_PAGE_TIMEOUT
            )
            log.info(f"[NAV] Page loaded. Current URL: {page.url}")
            await self._dismiss_loading(page)

            log.info("[DROPDOWN] Reading state select options …")
            state_options = await self._get_select_options(page, _SEL["state_select"])

            if not state_options:
                log.warning("[DROPDOWN] No state options found — portal may have changed DOM or is offline.")
                return

            log.info(f"[DROPDOWN] {len(state_options)} states found on portal:")
            for code, name in state_options:
                log.info(f"           code={code!r:6}  name={name}")

            lagos_options = [
                (code, name) for code, name in state_options
                if "LAGOS" in name.upper()
            ]

            if not lagos_options:
                log.error("[FILTER] Lagos state not found in dropdown — aborting.")
                return

            state_code, state_name = lagos_options[0]
            log.info(f"[FILTER] Targeting Lagos: code={state_code!r}, name={state_name!r}")

            try:
                await self._scrape_state(context, page, state_code, state_name)
            except Exception as exc:
                log.error(f"[ERROR] Lagos state scrape failed: {exc}", exc_info=True)
        finally:
            await page.close()

    # ── state level ──────────────────────────────────────────────────────────

    async def _scrape_state(
        self, context: BrowserContext, page: Page, state_code: str, state_name: str
    ):
        log.info(f"[STATE] Selecting {state_name!r} (code={state_code!r}) …")
        await self._select_option(page, _SEL["state_select"], state_code)
        await self._dismiss_loading(page)
        log.info(f"[STATE] After select — URL: {page.url}")

        lga_options = await self._get_select_options(page, _SEL["lga_select"])
        self._lgas_found = len(lga_options)

        if not lga_options:
            log.warning(f"[STATE] No LGAs found under {state_name}")
            return

        log.info(f"[STATE] {len(lga_options)} LGAs found under {state_name}:")
        for code, name in lga_options:
            log.info(f"         LGA code={code!r:6}  name={name}")

        for lga_code, lga_name in lga_options:
            try:
                await self._scrape_lga(
                    context, page, state_code, state_name, lga_code, lga_name
                )
            except Exception as exc:
                log.error(f"[ERROR] LGA {lga_name!r} failed: {exc}", exc_info=True)

    # ── LGA level ────────────────────────────────────────────────────────────

    async def _scrape_lga(
        self, context, page, state_code, state_name, lga_code, lga_name
    ):
        log.info(f"  [LGA] → {lga_name!r} (code={lga_code!r})")
        await self._select_option(page, _SEL["lga_select"], lga_code)
        await self._dismiss_loading(page)

        ward_options = await self._get_select_options(page, _SEL["ward_select"])
        if not ward_options:
            log.warning(f"  [LGA] No wards found under {lga_name}")
            return

        self._wards_found += len(ward_options)
        log.info(f"  [LGA] {len(ward_options)} wards under {lga_name}")

        for ward_code, ward_name in ward_options:
            try:
                await self._scrape_ward(
                    context, page,
                    state_code, state_name,
                    lga_code, lga_name,
                    ward_code, ward_name,
                )
            except Exception as exc:
                log.error(
                    f"  [ERROR] Ward {ward_name!r} in {lga_name!r} failed: {exc}",
                    exc_info=True,
                )

    # ── ward level ───────────────────────────────────────────────────────────

    async def _scrape_ward(
        self, context, page, state_code, state_name, lga_code, lga_name, ward_code, ward_name
    ):
        log.info(f"    [WARD] → {ward_name!r} (code={ward_code!r})")
        await self._select_option(page, _SEL["ward_select"], ward_code)
        await self._dismiss_loading(page)

        pu_options = await self._get_select_options(page, _SEL["pu_select"])
        if not pu_options:
            log.warning(f"    [WARD] No PUs found under {ward_name!r}")
            await self._queue_log_entry(
                state_code=state_code, state_name=state_name,
                lga_code=lga_code, lga_name=lga_name,
                ward_code=ward_code, ward_name=ward_name,
                portal_url=page.url,
                is_missing_url=True,
                error_message="No polling units found for ward",
            )
            self._db_inserts += 1
            return

        self._pus_found += len(pu_options)
        log.info(f"    [WARD] {len(pu_options)} PUs under {ward_name!r}")

        for pu_code, pu_name in pu_options:
            async with self._semaphore:
                await self._scrape_polling_unit(
                    page,
                    state_code, state_name,
                    lga_code, lga_name,
                    ward_code, ward_name,
                    pu_code, pu_name,
                )
            self._db_inserts += 1

    # ── polling unit level ───────────────────────────────────────────────────

    async def _scrape_polling_unit(
        self, page, state_code, state_name, lga_code, lga_name,
        ward_code, ward_name, pu_code, pu_name
    ):
        t0 = time.monotonic()
        log.info(f"      [PU] {pu_code!r} — {pu_name!r}")
        await self._select_option(page, _SEL["pu_select"], pu_code)
        await self._dismiss_loading(page)

        portal_url = page.url
        log.info(f"      [PU] Portal URL: {portal_url}")

        image_url: Optional[str] = None
        is_missing_url = False
        is_broken_url = False
        http_status: Optional[int] = None
        error_message: Optional[str] = None
        image_hash: Optional[str] = None
        image_size: Optional[int] = None
        image_path: Optional[str] = None

        # Step 1: find image URL
        try:
            image_url = await self._find_result_image_url(page)
        except Exception as exc:
            log.warning(f"      [PU] Could not find result image: {exc}")

        if not image_url:
            is_missing_url = True
            self._run.missing_url_count += 1
            self._images_missing += 1
            log.warning(f"      [PU] EC8A image URL MISSING for {pu_code!r}")
        else:
            log.info(f"      [PU] Image URL found: {image_url}")

            # Step 2: download image
            try:
                log.info(f"      [PU] Downloading image …")
                raw_bytes, http_status = await self._download_image(image_url)
                image_hash = hashlib.sha256(raw_bytes).hexdigest()
                image_size = len(raw_bytes)
                image_path = await self._save_image(raw_bytes, pu_code, image_hash)
                self._run.images_downloaded += 1
                self._images_ok += 1
                log.info(
                    f"      [PU] Download OK — HTTP {http_status}, "
                    f"{image_size:,} bytes, SHA256={image_hash[:16]}…"
                )
                log.info(f"      [PU] Stored at: {image_path}")
            except httpx.HTTPStatusError as exc:
                is_broken_url = True
                http_status = exc.response.status_code
                error_message = f"HTTP {http_status}"
                self._run.broken_url_count += 1
                self._images_broken += 1
                log.error(f"      [PU] HTTP error downloading image: {error_message}")
            except Exception as exc:
                is_broken_url = True
                error_message = str(exc)[:500]
                self._run.broken_url_count += 1
                self._images_broken += 1
                log.error(f"      [PU] Download failed: {error_message}")

        self._run.total_polling_units_visited += 1
        elapsed = int((time.monotonic() - t0) * 1000)

        # Step 3: extend hash chain (if image downloaded)
        if image_hash:
            chain_seq, prev_block, new_block = self._extend_chain(image_hash)
            log.info(
                f"      [CHAIN] Sequence {chain_seq}: "
                f"prev={prev_block[:12]}… → block={new_block[:12]}…"
            )

        # Step 4: queue MongoDB insert
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
            duration_ms=elapsed,
            error_message=error_message,
        )
        log.info(f"      [PU] Queued for MongoDB insert (batch size: {len(self._batch)})")

    # ── batch flush override to log counts ───────────────────────────────────

    async def _flush_batch(self):
        n = len(self._batch)
        if n:
            log.info(f"[MONGO] Flushing batch of {n} ScrapeLogEntry documents to MongoDB …")
            await ScrapeLogEntry.insert_many(self._batch)
            self._batch.clear()
            log.info(f"[MONGO] Batch inserted OK.")


# ── main ─────────────────────────────────────────────────────────────────────

async def main():
    log.info("Initialising MongoDB (Beanie) …")
    client = await init_db()
    log.info("MongoDB ready.")

    try:
        scraper = LagosIRevScraper()
        await scraper.run()
    finally:
        await close_db()
        log.info("MongoDB connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
