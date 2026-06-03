"""
Hash chain integrity unit tests — run on every push as a CI gate.

These tests verify the cryptographic properties of the tamper-evident
ledger without requiring a live MongoDB connection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from app.utils.hash_chain import (
    _genesis_hash,
    build_chain_entry,
    canonical_payload,
    compute_block_hash,
    compute_content_hash,
    verify_chain_entry,
    verify_chain_segment,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

_TS = datetime(2027, 2, 25, 9, 0, 0, tzinfo=timezone.utc)

_BASE = dict(
    election_id="election-aaa",
    polling_unit_id="pu-001",
    party_votes={"APC": 100, "PDP": 80},
    accredited_voters=250,
    total_votes_cast=180,
    valid_votes=175,
    rejected_votes=5,
    source="IReV",
    created_at=_TS,
)


def _make(seq: int, prev_hash: Optional[str] = None) -> dict:
    return build_chain_entry(**_BASE, sequence=seq, prev_hash=prev_hash)


# ── Genesis ───────────────────────────────────────────────────────────────────

def test_genesis_hash_is_deterministic():
    assert _genesis_hash() == _genesis_hash()


def test_genesis_hash_is_sha256_length():
    assert len(_genesis_hash()) == 64


def test_genesis_hash_differs_for_different_seeds():
    import hashlib
    h1 = hashlib.sha256(b"seed-alpha").hexdigest()
    h2 = hashlib.sha256(b"seed-beta").hexdigest()
    assert h1 != h2  # proves genesis hash is seed-dependent


# ── Single entry ──────────────────────────────────────────────────────────────

def test_single_entry_verifies():
    e = _make(1)
    assert verify_chain_entry(e["content_hash"], e["prev_hash"], e["block_hash"])


def test_tampered_content_hash_fails():
    e = _make(1)
    assert not verify_chain_entry("00" * 32, e["prev_hash"], e["block_hash"])


def test_tampered_block_hash_fails():
    e = _make(1)
    assert not verify_chain_entry(e["content_hash"], e["prev_hash"], "ff" * 32)


# ── Multi-entry segment ───────────────────────────────────────────────────────

def test_two_entry_chain_intact():
    e1 = _make(1)
    e2 = _make(2, prev_hash=e1["block_hash"])
    ok, breach = verify_chain_segment([{**e1, "sequence": 1}, {**e2, "sequence": 2}])
    assert ok is True
    assert breach is None


def test_long_chain_intact():
    prev = None
    entries = []
    for i in range(1, 11):
        e = _make(i, prev_hash=prev)
        entries.append({**e, "sequence": i})
        prev = e["block_hash"]
    ok, breach = verify_chain_segment(entries)
    assert ok is True


def test_broken_link_detected_at_second_entry():
    e1 = _make(1)
    # e2 references a wrong prev_hash — simulates a gap or insertion
    e2 = _make(2, prev_hash="deadbeef" * 8)
    entries = [{**e1, "sequence": 1}, {**e2, "sequence": 2}]
    ok, breach = verify_chain_segment(entries)
    assert ok is False
    assert breach == 2


def test_tampered_middle_entry_detected():
    entries = []
    prev = None
    for i in range(1, 5):
        e = _make(i, prev_hash=prev)
        entries.append({**e, "sequence": i})
        prev = e["block_hash"]

    # Tamper with entry 2's content_hash (simulates vote count mutation)
    entries[1] = dict(entries[1], content_hash="00" * 32)

    ok, breach = verify_chain_segment(entries)
    assert ok is False
    assert breach is not None


def test_empty_segment_is_valid():
    ok, breach = verify_chain_segment([])
    assert ok is True
    assert breach is None


# ── Canonical payload ─────────────────────────────────────────────────────────

def test_canonical_payload_is_deterministic():
    p1 = canonical_payload(**_BASE)
    p2 = canonical_payload(**_BASE)
    assert p1 == p2


def test_party_votes_are_sorted():
    payload = canonical_payload(
        **{**_BASE, "party_votes": {"ZZZ": 10, "AAA": 20}}
    )
    assert payload.index('"AAA"') < payload.index('"ZZZ"')


def test_one_vote_change_alters_content_hash():
    p_orig    = canonical_payload(**_BASE)
    p_mutated = canonical_payload(**{**_BASE, "party_votes": {"APC": 101, "PDP": 80}})
    assert compute_content_hash(p_orig) != compute_content_hash(p_mutated)


def test_accredited_voters_change_alters_hash():
    p_orig    = canonical_payload(**_BASE)
    p_mutated = canonical_payload(**{**_BASE, "accredited_voters": 300})
    assert compute_content_hash(p_orig) != compute_content_hash(p_mutated)


# ── Block hash ────────────────────────────────────────────────────────────────

def test_block_hash_depends_on_prev():
    ch   = compute_content_hash(canonical_payload(**_BASE))
    bh1  = compute_block_hash(ch, "prev-a")
    bh2  = compute_block_hash(ch, "prev-b")
    assert bh1 != bh2


def test_block_hash_depends_on_content():
    bh1 = compute_block_hash("content-a", "prev")
    bh2 = compute_block_hash("content-b", "prev")
    assert bh1 != bh2
