"""
Set required environment variables before any app module is imported.
These defaults are safe for unit tests that do not touch MongoDB.
"""
import os

os.environ.setdefault("GENESIS_BLOCK_SEED", "ci-test-genesis-2027")
os.environ.setdefault("MONGODB_URI",        "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME",    "test_nigeriavoterwatch")
os.environ.setdefault("JWT_SECRET_KEY",     "ci-test-jwt-secret-key")
os.environ.setdefault("APP_SECRET_KEY",     "ci-test-app-secret-key")
