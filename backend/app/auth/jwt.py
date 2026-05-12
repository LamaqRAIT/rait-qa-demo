"""
JWT token creation and validation.
Access token: 1h expiry, HS256, role claim included.
Refresh token: 7d expiry, stored in HttpOnly cookie.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from app.config import get_settings


def _settings():
    return get_settings()


def create_access_token(user_id: str, email: str, role: str, team_id: str) -> str:
    s = _settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=s.access_token_expire_minutes)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "team_id": team_id,
        "type": "access",
        "exp": expire,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    s = _settings()
    expire = datetime.now(timezone.utc) + timedelta(days=s.refresh_token_expire_days)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    s = _settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
