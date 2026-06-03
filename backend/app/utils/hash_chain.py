"""
Hash chain implementation for the append-only tamper-evident results ledger.

Each block: SHA-256(canonical_payload + prev_block_hash)
Genesis prev_hash: SHA-256(GENESIS_BLOCK_SEED)

IDs are strings (MongoDB ObjectId hex strings or any stable identifier).
"""
import hashlib
import json
from datetime import datetime
from typing import Optional

from app.config import settings


def _genesis_hash() -> str:
    return hashlib.sha256(settings.genesis_block_seed.encode()).hexdigest()


def canonical_payload(
    election_id: str,
    polling_unit_id: str,
    party_votes: dict,
    accredited_voters: int,
    total_votes_cast: int,
    valid_votes: int,
    rejected_votes: int,
    source: str,
    created_at: datetime,
) -> str:
    """Deterministic JSON serialization — keys sorted, no whitespace."""
    data = {
        "accredited_voters": accredited_voters,
        "created_at": created_at.isoformat(),
        "election_id": election_id,
        "party_votes": {k: party_votes[k] for k in sorted(party_votes)},
        "polling_unit_id": polling_unit_id,
        "rejected_votes": rejected_votes,
        "source": source,
        "total_votes_cast": total_votes_cast,
        "valid_votes": valid_votes,
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def compute_content_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_block_hash(content_hash: str, prev_hash: str) -> str:
    return hashlib.sha256(f"{content_hash}{prev_hash}".encode()).hexdigest()


def build_chain_entry(
    election_id: str,
    polling_unit_id: str,
    party_votes: dict,
    accredited_voters: int,
    total_votes_cast: int,
    valid_votes: int,
    rejected_votes: int,
    source: str,
    created_at: datetime,
    prev_hash: Optional[str] = None,
    sequence: Optional[int] = None,
) -> dict:
    """
    Returns a dict with content_hash, prev_hash, block_hash, chain_sequence
    ready to be set on an ElectionResult document.
    """
    if prev_hash is None:
        prev_hash = _genesis_hash()

    payload = canonical_payload(
        election_id=election_id,
        polling_unit_id=polling_unit_id,
        party_votes=party_votes,
        accredited_voters=accredited_voters,
        total_votes_cast=total_votes_cast,
        valid_votes=valid_votes,
        rejected_votes=rejected_votes,
        source=source,
        created_at=created_at,
    )
    content_hash = compute_content_hash(payload)
    block_hash = compute_block_hash(content_hash, prev_hash)

    return {
        "content_hash": content_hash,
        "prev_hash": prev_hash,
        "block_hash": block_hash,
        "chain_sequence": sequence,
    }


def verify_chain_entry(
    content_hash: str,
    prev_hash: str,
    stored_block_hash: str,
) -> bool:
    return compute_block_hash(content_hash, prev_hash) == stored_block_hash


def verify_chain_segment(entries: list[dict]) -> tuple[bool, Optional[int]]:
    """
    Verify a sorted (by sequence) list of HashChainEntry dicts.
    Returns (True, None) if intact, or (False, breached_sequence_number).
    """
    for i, entry in enumerate(entries):
        if not verify_chain_entry(
            entry["content_hash"],
            entry["prev_hash"],
            entry["block_hash"],
        ):
            return False, entry["sequence"]

        if i > 0 and entry["prev_hash"] != entries[i - 1]["block_hash"]:
            return False, entry["sequence"]

    return True, None
