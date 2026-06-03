from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List

# Walk up from backend/app/ → backend/ → project root → .env
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "development"
    app_secret_key: str = "dev-secret"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    allowed_origins: str = (
        "https://nigeriavoterwatch.vercel.app,"
        "https://nigeriavoterwatch.onrender.com,"
        "http://localhost:3000,"
        "http://localhost:5173"
    )

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    # MongoDB Atlas
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "nigeriavoterwatch"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Scraper — legacy INEC portal
    inec_results_base_url: str = "https://results.inecnigeria.org"
    inec_portal_base_url: str = "https://inecnigeria.org"
    scraper_user_agent: str = "NigeriaVoteWatch/1.0"
    scraper_interval_seconds: int = 300
    scraper_concurrency: int = 4

    # IReV scraper
    irev_base_url: str = "https://inecelectionresults.ng"
    irev_scraper_interval_seconds: int = 300  # 5 minutes
    irev_scraper_concurrency: int = 2

    # Storage paths
    upload_dir: str = "./uploads"
    image_storage_dir: str = "./uploads/ec8a_images"
    public_snapshot_path: str = "../frontend/public/latest.json"

    # S3-compatible (optional — leave blank to use local disk)
    s3_bucket: str = ""
    s3_region: str = "af-south-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_endpoint_url: str = ""

    # OCR
    tesseract_cmd: str = "/usr/bin/tesseract"
    ocr_language: str = "eng"
    google_application_credentials: str = ""
    google_cloud_project: str = ""

    # JWT
    jwt_secret_key: str = "dev-jwt-secret"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # Observer
    observer_report_rate_limit: int = 10
    observer_invite_code_ttl_hours: int = 48

    # Integrity & hash chain
    hash_algorithm: str = "sha256"
    genesis_block_seed: str = "NigeriaVoteWatch-genesis-2027"
    chain_verification_interval_minutes: int = 15

    # Notifications
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "alerts@nigeriavoterwatch.ng"

    # Monitoring
    sentry_dsn: str = ""
    log_level: str = "INFO"

    # Security
    rate_limit_per_minute: int = 60


settings = Settings()
