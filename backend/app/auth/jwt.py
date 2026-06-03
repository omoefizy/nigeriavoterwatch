"""
JWT token creation and FastAPI auth dependencies.

All GET endpoints are public — only POST/PATCH/DELETE require a valid JWT.
Roles:
  admin    — full access
  observer — can submit reports; cannot manage elections or resolve anomalies
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

_security = HTTPBearer(auto_error=False)


def create_access_token(user_id: str, email: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    return jwt.encode(
        {"sub": user_id, "email": email, "role": role, "type": "access", "exp": expire},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    return jwt.encode(
        {"sub": user_id, "type": "refresh", "exp": expire},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def _decode(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


def decode_refresh_token(token: str) -> dict:
    return _decode(token, "refresh")


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_security),
):
    if not creds:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = _decode(creds.credentials, "access")
    from app.models.user import User
    from beanie import PydanticObjectId

    try:
        oid = PydanticObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    user = await User.get(oid)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def require_admin(user=Depends(get_current_user)):
    from app.models.user import UserRole

    if user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_observer_or_admin(user=Depends(get_current_user)):
    return user
