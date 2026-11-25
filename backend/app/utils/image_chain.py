"""
IReV EC8A image hash chain verification (read-only).

The image chain lives in the scrape_log collection. Every entry with an
image carries:
    chain_sequence         1, 2, 3, … (no gaps, no repeats)
    image_hash_sha256      SHA-256 of the raw EC8A file
    prev_image_block_hash  image_block_hash of entry (sequence - 1), or genesis
    image_block_hash       SHA-256(image_hash_sha256 + prev_image_block_hash)

Genesis = SHA-256(GENESIS_BLOCK_SEED + ":irev-images").

verify_image_chain() streams the entries in sequence order (O(n) time, O(1)
memory), recomputes every block hash and reports the first problem it finds.
It only reads; it never writes to the collection.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import settings


def image_genesis_hash() -> str:
    return hashlib.sha256(f"{settings.genesis_block_seed}:irev-images".encode()).hexdigest()


def compute_image_block_hash(image_hash: str, prev_block_hash: str) -> str:
    return hashlib.sha256(f"{image_hash}{prev_block_hash}".encode()).hexdigest()


@dataclass
class ImageChainReport:
    valid: bool
    entries_checked: int
    head_sequence: int                 # 0 when the chain is empty
    head_hash: str                     # genesis hash when the chain is empty
    problem: Optional[str] = None      # "gap" | "duplicate_sequence" | "broken_link" |
                                       # "bad_block_hash" | "missing_image_hash"
    problem_sequence: Optional[int] = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "entries_checked": self.entries_checked,
            "head_sequence": self.head_sequence,
            "head_hash": self.head_hash,
            "problem": self.problem,
            "problem_sequence": self.problem_sequence,
            "detail": self.detail,
        }


_PROJECTION = {
    "_id": 1, "chain_sequence": 1, "image_hash_sha256": 1,
    "prev_image_block_hash": 1, "image_block_hash": 1, "pu_code": 1,
}


async def verify_image_chain(collection) -> ImageChainReport:
    """Walk scrape_log by chain_sequence and verify every link.

    `collection` is a Motor collection for scrape_log. Stops at the first
    problem, since everything after a broken link is unverifiable.
    """
    expected_seq = 1
    prev_block = image_genesis_hash()
    checked = 0

    def fail(problem: str, seq: int, **detail) -> ImageChainReport:
        return ImageChainReport(False, checked, expected_seq - 1, prev_block,
                                problem, seq, detail)

    cursor = collection.find(
        {"chain_sequence": {"$type": "number"}}, _PROJECTION
    ).sort("chain_sequence", 1)

    async for doc in cursor:
        seq = doc["chain_sequence"]
        ref = {"_id": str(doc["_id"]), "pu_code": doc.get("pu_code")}

        if seq < expected_seq:
            return fail("duplicate_sequence", seq, **ref)
        if seq > expected_seq:
            return fail("gap", expected_seq, missing_from=expected_seq,
                        missing_to=seq - 1, next_entry=ref)

        image_hash = doc.get("image_hash_sha256")
        if not image_hash:
            return fail("missing_image_hash", seq, **ref)

        stored_prev = doc.get("prev_image_block_hash")
        if stored_prev != prev_block:
            return fail("broken_link", seq, expected_prev=prev_block,
                        stored_prev=stored_prev, **ref)

        recomputed = compute_image_block_hash(image_hash, stored_prev)
        if doc.get("image_block_hash") != recomputed:
            return fail("bad_block_hash", seq, recomputed=recomputed,
                        stored=doc.get("image_block_hash"), **ref)

        prev_block = recomputed
        expected_seq += 1
        checked += 1

    return ImageChainReport(True, checked, expected_seq - 1, prev_block)
