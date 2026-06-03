"""
Dry-run IReV portal probe — LAGOS STATE ONLY (no MongoDB writes).

Navigates the IReV portal through State → LGA → Ward → PU dropdowns,
logs every URL hit, every option found, and every image URL discovered.
No data is written anywhere — useful for verifying the portal is live
and that the scraper selectors still work.

Usage (from project root):
    python scripts/manual_irev_lagos_dryrun.py
"""

import asyncio
import hashlib
import logging
import sys
import os
import time
from typing import Optional
from urllib.parse import urljoin

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "backend"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("irev_dryrun")
for _lib in ("httpx", "httpcore", "playwright", "asyncio"):
    logging.getLogger(_lib).setLevel(logging.WARNING)

from dotenv import load_dotenv
load_dotenv(os.path.join(_HERE, "..", ".env"))

from app.config import settings

import httpx
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

_SEL = {
    "state_select": "select[data-testid='state-select'], select#state, select[name='state']",
    "lga_select":   "select[data-testid='lga-select'],   select#lga,   select[name='lga']",
    "ward_select":  "select[data-testid='ward-select'],  select#ward,  select[name='ward']",
    "pu_select":    "select[data-testid='pu-select'], select#pu, select[name='pu'], select[name='polling_unit']",
    "result_image": "img[data-testid='result-sheet'], img.result-sheet, img[alt*='EC8A'], img[src*='ec8a'], img[src*='result']",
    "loading":      ".loading-overlay, [data-testid='loading'], .spinner",
}
_DROPDOWN_TIMEOUT = 15_000
_IMAGE_TIMEOUT    = 20_000
_PAGE_TIMEOUT     = 30_000

# Limit to first N items at each level to keep dry-run fast
MAX_LGAS   = 3   # probe first 3 LGAs in Lagos
MAX_WARDS  = 2   # probe first 2 wards per LGA
MAX_PUS    = 3   # probe first 3 PUs per ward


async def get_options(page, selector):
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


async def select_option(page, selector, value):
    await page.wait_for_selector(selector, timeout=_DROPDOWN_TIMEOUT)
    await page.select_option(selector, value=value)
    await page.wait_for_timeout(800)


async def dismiss_loading(page):
    try:
        await page.wait_for_selector(_SEL["loading"], state="hidden", timeout=8_000)
    except PlaywrightTimeout:
        pass


async def find_image_url(page):
    try:
        await page.wait_for_selector(_SEL["result_image"], timeout=_IMAGE_TIMEOUT)
        src = await page.get_attribute(_SEL["result_image"], "src")
        if src:
            return src if src.startswith("http") else urljoin(page.url, src)
    except PlaywrightTimeout:
        pass
    srcs = await page.evaluate(
        """() => Array.from(document.images)
               .map(i => i.src)
               .filter(s => s && (s.includes('ec8') || s.includes('result') || s.includes('sheet') || s.includes('form')))"""
    )
    if srcs:
        return srcs[0] if srcs[0].startswith("http") else urljoin(page.url, srcs[0])
    return None


async def main():
    log.info("=" * 64)
    log.info("IReV DRY-RUN — LAGOS ONLY (no MongoDB writes)")
    log.info(f"  Portal URL : {settings.irev_base_url}")
    log.info(f"  Limits     : {MAX_LGAS} LGAs × {MAX_WARDS} wards × {MAX_PUS} PUs")
    log.info("=" * 64)

    stats = {
        "lgas_seen": 0, "wards_seen": 0, "pus_seen": 0,
        "images_found": 0, "images_missing": 0,
        "images_downloaded_ok": 0, "images_broken": 0,
        "total_bytes": 0,
    }
    chain_seq = 0
    genesis_seed = f"{settings.genesis_block_seed}:irev-images"
    prev_block = hashlib.sha256(genesis_seed.encode()).hexdigest()
    log.info(f"  Chain genesis prev_hash: {prev_block[:24]}…")
    log.info("=" * 64)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        headers={"User-Agent": settings.scraper_user_agent},
    ) as http:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    user_agent=settings.scraper_user_agent,
                    extra_http_headers={"Accept-Language": "en-NG,en;q=0.9"},
                    viewport={"width": 1280, "height": 900},
                )
                page = await ctx.new_page()

                # ── navigate to portal ─────────────────────────────────────
                log.info(f"[NAV] GET {settings.irev_base_url}")
                t0 = time.monotonic()
                await page.goto(settings.irev_base_url, wait_until="networkidle", timeout=_PAGE_TIMEOUT)
                log.info(f"[NAV] Page loaded in {(time.monotonic()-t0)*1000:.0f}ms → {page.url}")
                await dismiss_loading(page)

                # ── read state dropdown ────────────────────────────────────
                log.info("[DROPDOWN] Reading state select …")
                state_options = await get_options(page, _SEL["state_select"])
                if not state_options:
                    log.error("[DROPDOWN] No states found — portal DOM may have changed or is down.")
                    return

                log.info(f"[DROPDOWN] {len(state_options)} states on portal:")
                for code, name in state_options:
                    log.info(f"           {code:>6}  {name}")

                lagos = [(c, n) for c, n in state_options if "LAGOS" in n.upper()]
                if not lagos:
                    log.error("[FILTER] Lagos not found in state dropdown.")
                    return
                state_code, state_name = lagos[0]
                log.info(f"\n[SELECT] Choosing Lagos — code={state_code!r}, name={state_name!r}")

                # ── select Lagos ───────────────────────────────────────────
                await select_option(page, _SEL["state_select"], state_code)
                await dismiss_loading(page)
                log.info(f"[NAV] After state select → {page.url}")

                # ── read LGA dropdown ──────────────────────────────────────
                lga_options = await get_options(page, _SEL["lga_select"])
                stats["lgas_seen"] = len(lga_options)
                if not lga_options:
                    log.warning("[LGA] No LGAs found under Lagos.")
                    return

                log.info(f"\n[LGA] {len(lga_options)} LGAs under Lagos:")
                for code, name in lga_options:
                    log.info(f"       {code:>6}  {name}")

                # ── iterate LGAs (capped) ──────────────────────────────────
                for lga_code, lga_name in lga_options[:MAX_LGAS]:
                    log.info(f"\n  [LGA→] Selecting {lga_name!r} (code={lga_code!r})")
                    await select_option(page, _SEL["lga_select"], lga_code)
                    await dismiss_loading(page)

                    ward_options = await get_options(page, _SEL["ward_select"])
                    if not ward_options:
                        log.warning(f"  [LGA] No wards under {lga_name}")
                        continue
                    stats["wards_seen"] += len(ward_options)
                    log.info(f"  [LGA] {len(ward_options)} wards (showing first {MAX_WARDS}):")
                    for wc, wn in ward_options[:MAX_WARDS]:
                        log.info(f"         {wc:>8}  {wn}")

                    for ward_code, ward_name in ward_options[:MAX_WARDS]:
                        log.info(f"\n    [WARD→] Selecting {ward_name!r} (code={ward_code!r})")
                        await select_option(page, _SEL["ward_select"], ward_code)
                        await dismiss_loading(page)

                        pu_options = await get_options(page, _SEL["pu_select"])
                        if not pu_options:
                            log.warning(f"    [WARD] No PUs under {ward_name!r}")
                            stats["images_missing"] += 1
                            continue
                        stats["pus_seen"] += len(pu_options)
                        log.info(f"    [WARD] {len(pu_options)} PUs (probing first {MAX_PUS}):")

                        for pu_code, pu_name in pu_options[:MAX_PUS]:
                            t_pu = time.monotonic()
                            log.info(f"      [PU] {pu_code!r} — {pu_name!r}")
                            await select_option(page, _SEL["pu_select"], pu_code)
                            await dismiss_loading(page)
                            log.info(f"      [PU] Portal URL: {page.url}")

                            image_url = await find_image_url(page)
                            if not image_url:
                                log.warning(f"      [PU] EC8A image URL: MISSING")
                                stats["images_missing"] += 1
                            else:
                                log.info(f"      [PU] EC8A image URL: {image_url}")
                                stats["images_found"] += 1

                                # Download and hash
                                try:
                                    resp = await http.get(image_url)
                                    resp.raise_for_status()
                                    raw = resp.content
                                    sha256 = hashlib.sha256(raw).hexdigest()
                                    # Extend image hash chain
                                    chain_seq += 1
                                    new_block = hashlib.sha256(
                                        f"{sha256}{prev_block}".encode()
                                    ).hexdigest()
                                    log.info(
                                        f"      [DL] HTTP {resp.status_code}, "
                                        f"{len(raw):,} bytes, "
                                        f"SHA256={sha256[:16]}…"
                                    )
                                    log.info(
                                        f"      [CHAIN] seq={chain_seq}: "
                                        f"prev={prev_block[:12]}… → "
                                        f"block={new_block[:12]}…"
                                    )
                                    log.info(
                                        f"      [MONGO] Would insert ScrapeLogEntry: "
                                        f"state={state_name}, lga={lga_name}, "
                                        f"ward={ward_name}, pu={pu_code}, "
                                        f"image_hash={sha256[:16]}…, "
                                        f"chain_sequence={chain_seq}"
                                    )
                                    prev_block = new_block
                                    stats["images_downloaded_ok"] += 1
                                    stats["total_bytes"] += len(raw)
                                except httpx.HTTPStatusError as exc:
                                    log.error(f"      [DL] HTTP {exc.response.status_code} — broken URL")
                                    stats["images_broken"] += 1
                                except Exception as exc:
                                    log.error(f"      [DL] Download failed: {exc}")
                                    stats["images_broken"] += 1

                            elapsed = int((time.monotonic() - t_pu) * 1000)
                            log.info(f"      [PU] Done in {elapsed}ms")

                await page.close()
            finally:
                await browser.close()

    log.info("\n" + "=" * 64)
    log.info("DRY-RUN SUMMARY (Lagos state, capped probe)")
    log.info(f"  Total LGAs in Lagos     : {stats['lgas_seen']}")
    log.info(f"  LGAs probed             : {min(MAX_LGAS, stats['lgas_seen'])}")
    log.info(f"  Wards seen (probed LGAs): {stats['wards_seen']}")
    log.info(f"  PUs seen (probed wards) : {stats['pus_seen']}")
    log.info(f"  EC8A URLs found         : {stats['images_found']}")
    log.info(f"  EC8A URLs missing       : {stats['images_missing']}")
    log.info(f"  Images downloaded OK    : {stats['images_downloaded_ok']}")
    log.info(f"  Images broken           : {stats['images_broken']}")
    log.info(f"  Total bytes downloaded  : {stats['total_bytes']:,}")
    log.info(f"  Chain tip seq           : {chain_seq}")
    log.info(f"  Chain tip hash          : {prev_block[:24]}…")
    log.info("=" * 64)
    log.info("\nTo run the full scrape with MongoDB writes, fix the Atlas")
    log.info("credentials in .env and run: python scripts/manual_irev_lagos.py")


if __name__ == "__main__":
    asyncio.run(main())
