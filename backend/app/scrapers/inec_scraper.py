"""
INEC results portal scraper.
Uses Playwright for JavaScript-rendered pages, BeautifulSoup for parsing.
Beanie documents replace SQLAlchemy ORM — no session object required.
"""
import asyncio
from datetime import datetime, timezone
from typing import Optional

import structlog
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.config import settings
from app.models.elections import Election, ElectionStatus, PollingUnit
from app.models.results import ElectionResult, ResultSource, ResultStatus
from app.models.audit import AuditLog, AuditAction, HashChainEntry
from app.utils.hash_chain import build_chain_entry

logger = structlog.get_logger()


class INECScraper:
    def __init__(self):
        self.base_url = settings.inec_results_base_url

    async def run(self):
        active_elections = await Election.find(
            Election.status == ElectionStatus.ONGOING
        ).to_list()

        if not active_elections:
            logger.info("No active elections to scrape")
            return

        for election in active_elections:
            await self._scrape_election(election)

    async def _scrape_election(self, election: Election):
        logger.info("Scraping election", election_id=str(election.id), name=election.name)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=settings.scraper_user_agent,
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                page = await context.new_page()

                try:
                    await page.goto(
                        f"{self.base_url}/results/{election.id}",
                        wait_until="networkidle",
                        timeout=30_000,
                    )
                    html = await page.content()
                    await self._parse_and_store(html, election)
                finally:
                    await browser.close()

            await AuditLog(
                action=AuditAction.SCRAPE_COMPLETED,
                actor_type="system",
                actor_id="inec_scraper",
                entity_type="election",
                entity_id=str(election.id),
                details={"source_url": f"{self.base_url}/results/{election.id}"},
            ).insert()

        except Exception as exc:
            logger.error("Scrape failed for election", election_id=str(election.id), error=str(exc))
            raise

    async def _parse_and_store(self, html: str, election: Election):
        soup = BeautifulSoup(html, "lxml")
        result_rows = soup.select("table.results-table tbody tr")

        if not result_rows:
            logger.warning("No result rows found in scraped HTML", election_id=str(election.id))
            return

        # Fetch the current chain tip
        last_entry = (
            await HashChainEntry.find()
            .sort("-sequence")
            .limit(1)
            .first_or_none()
        )
        last_seq: int = last_entry.sequence if last_entry else 0
        last_hash: Optional[str] = last_entry.block_hash if last_entry else None

        for row in result_rows:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            try:
                pu_code = cols[0].get_text(strip=True)
                accredited = int(cols[1].get_text(strip=True).replace(",", "") or 0)
                total_cast = int(cols[2].get_text(strip=True).replace(",", "") or 0)
                valid = int(cols[3].get_text(strip=True).replace(",", "") or 0)
                rejected = int(cols[4].get_text(strip=True).replace(",", "") or 0)

                # TODO: parse actual party vote columns once INEC table structure is confirmed
                party_votes: dict[str, int] = {}

                polling_unit = await PollingUnit.find_one(
                    PollingUnit.inec_pu_code == pu_code
                )
                if not polling_unit:
                    logger.debug("Unknown polling unit", pu_code=pu_code)
                    continue

                now = datetime.now(timezone.utc)
                last_seq += 1
                chain = build_chain_entry(
                    election_id=str(election.id),
                    polling_unit_id=str(polling_unit.id),
                    party_votes=party_votes,
                    accredited_voters=accredited,
                    total_votes_cast=total_cast,
                    valid_votes=valid,
                    rejected_votes=rejected,
                    source=ResultSource.INEC_SCRAPER,
                    created_at=now,
                    prev_hash=last_hash,
                    sequence=last_seq,
                )

                result_doc = ElectionResult(
                    election_id=election.id,
                    polling_unit_id=polling_unit.id,
                    party_votes=party_votes,
                    accredited_voters=accredited,
                    total_votes_cast=total_cast,
                    valid_votes=valid,
                    rejected_votes=rejected,
                    source=ResultSource.INEC_SCRAPER,
                    status=ResultStatus.PRELIMINARY,
                    source_url=f"{self.base_url}/results/{election.id}",
                    content_hash=chain["content_hash"],
                    prev_hash=chain["prev_hash"],
                    chain_sequence=last_seq,
                    created_at=now,
                    created_by="inec_scraper",
                )
                await result_doc.insert()

                chain_entry = HashChainEntry(
                    result_id=result_doc.id,
                    sequence=last_seq,
                    election_id=election.id,
                    polling_unit_id=polling_unit.id,
                    content_hash=chain["content_hash"],
                    prev_hash=chain["prev_hash"],
                    block_hash=chain["block_hash"],
                    created_at=now,
                )
                await chain_entry.insert()
                last_hash = chain["block_hash"]

            except (ValueError, IndexError) as exc:
                logger.warning("Failed to parse result row", error=str(exc))
                continue
