"""User model for JWT authentication."""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING


class UserRole(str, Enum):
    admin = "admin"
    observer = "observer"


class User(Document):
    email: str
    password_hash: str
    role: UserRole = UserRole.observer
    full_name: str
    observer_id: Optional[str] = None  # links to Observer._id (string for simplicity)
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: Optional[datetime] = None

    class Settings:
        name = "users"
        indexes = [
            IndexModel([("email", ASCENDING)], unique=True),
        ]
