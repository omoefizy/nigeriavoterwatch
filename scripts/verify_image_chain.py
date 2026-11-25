#!/usr/bin/env python3
"""
Verify the IReV EC8A image hash chain in scrape_log. Read-only.

Walks every chained scrape_log entry in chain_sequence order, recomputes each
image_block_hash = SHA-256(image_hash_sha256 + prev_image_block_hash), checks
each prev_image_block_hash against the previous entry (genesis for seq 1), and
reports the first broken link, duplicate or gap. Prints the verified head.

Connects with Motor directly and does not initialise Beanie, so it creates no
indexes and writes nothing.

Usage (from the repo root):
    backend/venv/bin/python scripts/verify_image_chain.py [--json]

Exit code: 0 = chain intact, 1 = problem found.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import motor.motor_asyncio  # noqa: E402

from app.config import settings  # noqa: E402
from app.utils.image_chain import verify_image_chain  # noqa: E402


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = p.parse_args()

    client = motor.motor_asyncio.AsyncIOMotorClient(
        settings.mongodb_uri, serverSelectionTimeoutMS=30000
    )
    try:
        coll = client[settings.mongodb_db_name]["scrape_log"]
        report = await verify_image_chain(coll)
    finally:
        client.close()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print("IReV image chain:", "INTACT" if report.valid else "BROKEN")
        print(f"  entries checked : {report.entries_checked}")
        print(f"  verified head   : seq {report.head_sequence}  {report.head_hash}")
        if not report.valid:
            print(f"  problem         : {report.problem} at seq {report.problem_sequence}")
            for k, v in report.detail.items():
                print(f"    {k}: {v}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
