"""
Periodic hash chain verification — runs every 15 minutes.
Streams all HashChainEntry documents sorted by sequence and verifies linkage.
Flags any breach as a CRITICAL AuditLog entry.
"""
import structlog
from app.models.audit import HashChainEntry, AuditLog, AuditAction
from app.utils.hash_chain import verify_chain_segment

logger = structlog.get_logger()


async def verify_hash_chain():
    logger.info("Starting hash chain verification")
    try:
        entries = (
            await HashChainEntry.find()
            .sort("+sequence")
            .to_list()
        )

        if not entries:
            logger.info("No chain entries to verify")
            return

        chain_dicts = [
            {
                "sequence": e.sequence,
                "content_hash": e.content_hash,
                "prev_hash": e.prev_hash,
                "block_hash": e.block_hash,
            }
            for e in entries
        ]

        is_valid, breach_seq = verify_chain_segment(chain_dicts)

        audit = AuditLog(
            action=AuditAction.CHAIN_VERIFIED if is_valid else AuditAction.CHAIN_BREACH_DETECTED,
            actor_type="system",
            actor_id="chain_verifier",
            entity_type="hash_chain",
            details={
                "entries_checked": len(entries),
                "breach_sequence": breach_seq,
            },
        )
        await audit.insert()

        if not is_valid:
            logger.critical("HASH CHAIN BREACH DETECTED", breach_sequence=breach_seq)
        else:
            logger.info("Hash chain intact", entries_checked=len(entries))

    except Exception as exc:
        logger.error("Chain verification failed", error=str(exc))
        raise
