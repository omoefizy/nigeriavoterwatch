from app.models.elections import State, LGA, Ward, PollingUnit, Election, ElectionType, ElectionStatus
from app.models.results import ElectionResult, ResultDocument, ResultSource, ResultStatus
from app.models.audit import AuditLog, HashChainEntry, AuditAction
from app.models.observers import Observer, ObserverReport, Anomaly, AnomalyType, AnomalySeverity
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry, ScrapeRunStatus

# Ordered list passed to beanie.init_beanie()
ALL_DOCUMENT_MODELS = [
    State, LGA, Ward, PollingUnit, Election,
    ElectionResult, ResultDocument,
    AuditLog, HashChainEntry,
    Observer, ObserverReport, Anomaly,
    IRevScrapeRun, ScrapeLogEntry,
]

__all__ = [
    # Elections
    "State", "LGA", "Ward", "PollingUnit", "Election",
    "ElectionType", "ElectionStatus",
    # Results
    "ElectionResult", "ResultDocument",
    "ResultSource", "ResultStatus",
    # Audit
    "AuditLog", "HashChainEntry", "AuditAction",
    # Observers
    "Observer", "ObserverReport", "Anomaly",
    "AnomalyType", "AnomalySeverity",
    # Scrape log
    "IRevScrapeRun", "ScrapeLogEntry", "ScrapeRunStatus",
    # Init helper
    "ALL_DOCUMENT_MODELS",
]
