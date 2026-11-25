"""
Periodic hash chain verification — runs every 15 minutes (results chain and IReV image chain).
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


async def verify_irev_image_chain():
    """Verify the IReV EC8A image chain in scrape_log (read-only walk).

    Writes one AuditLog row: CHAIN_VERIFIED or CHAIN_BREACH_DETECTED with
    entity_type="irev_image_chain".
    """
    from app.models.scrape_log import ScrapeLogEntry
    from app.utils.image_chain import verify_image_chain

    logger.info("Starting IReV image chain verification")
    try:
        report = await verify_image_chain(ScrapeLogEntry.get_motor_collection())

        await AuditLog(
            action=AuditAction.CHAIN_VERIFIED if report.valid else AuditAction.CHAIN_BREACH_DETECTED,
            actor_type="system",
            actor_id="image_chain_verifier",
            entity_type="irev_image_chain",
            details=report.as_dict(),
        ).insert()

        if report.valid:
            logger.info("IReV image chain intact", entries_checked=report.entries_checked,
                        head_sequence=report.head_sequence)
        else:
            logger.critical("IREV IMAGE CHAIN BREACH DETECTED", problem=report.problem,
                            sequence=report.problem_sequence, detail=report.detail)

    except Exception as exc:
        logger.error("IReV image chain verification failed", error=str(exc))
        raise


async def verify_all_chains():
    """15-minute integrity job: results chain and IReV image chain.

    Each verifier runs even if the other raises; the first error is re-raised
    afterwards so APScheduler still records the job as failed.
    """
    errors: list[Exception] = []
    for verifier in (verify_hash_chain, verify_irev_image_chain):
        try:
            await verifier()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise errors[0]
