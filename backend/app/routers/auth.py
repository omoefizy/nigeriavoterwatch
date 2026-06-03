"""
Authentication endpoints.

POST /api/auth/setup    Bootstrap first admin account (one-time, secret-gated)
POST /api/auth/login    Email + password → access + refresh tokens
POST /api/auth/refresh  Refresh token → new access token
"""
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException, Request
from passlib.context import CryptContext

from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.config import settings
from app.models.user import User, UserRole
from app.schemas.auth import (
    AccessTokenResponse,
    AdminSetupRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter()
log = structlog.get_logger()
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Setup ─────────────────────────────────────────────────────────────────────

@router.post("/setup", response_model=TokenResponse, status_code=201)
async def setup_admin(body: AdminSetupRequest, request: Request):
    """
    One-time endpoint: creates the first admin account.
    Protected by setup_secret (must match settings.app_secret_key).
    Returns a 409 if an admin already exists.
    """
    if body.setup_secret != settings.app_secret_key:
        raise HTTPException(status_code=403, detail="Invalid setup secret")

    existing_admin = await User.find_one(User.role == UserRole.admin)
    if existing_admin:
        raise HTTPException(status_code=409, detail="Admin account already exists")

    existing = await User.find_one(User.email == body.email.lower())
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email.lower(),
        password_hash=_pwd.hash(body.password),
        role=UserRole.admin,
        full_name=body.full_name,
    )
    await user.insert()

    access = create_access_token(str(user.id), user.email, user.role.value)
    refresh = create_refresh_token(str(user.id))

    log.info("Admin account created", email=user.email, ip=request.client.host if request.client else None)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        role=user.role.value,
    )


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    user = await User.find_one(User.email == body.email.lower())
    if not user or not _pwd.verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    user.last_login_at = datetime.now(timezone.utc)
    await user.save()

    access = create_access_token(str(user.id), user.email, user.role.value)
    refresh = create_refresh_token(str(user.id))

    log.info("User logged in", email=user.email, role=user.role.value,
             ip=request.client.host if request.client else None)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        role=user.role.value,
    )


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_token(body: RefreshRequest):
    payload = decode_refresh_token(body.refresh_token)
    from beanie import PydanticObjectId

    try:
        oid = PydanticObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    user = await User.get(oid)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    access = create_access_token(str(user.id), user.email, user.role.value)
    return AccessTokenResponse(
        access_token=access,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
