"""
Authentication API — login, refresh, logout, me.
POST /auth/login  → {access_token, token_type, user}
POST /auth/refresh → {access_token, token_type}
POST /auth/logout → {status: "ok"}
GET  /auth/me     → user object
"""
from fastapi import APIRouter, HTTPException, Depends, Response, Cookie
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
import structlog

import app.db as db
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.auth.deps import get_current_user

log = structlog.get_logger()
router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    try:
        from passlib.context import CryptContext
        pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
    except ImportError:
        raise HTTPException(status_code=500, detail="Auth not available — passlib not installed")

    user = await db.get_user_by_email(body.email)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not pwd.verify(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access = create_access_token(user.id, user.email, user.role, user.team_id or "")
    refresh = create_refresh_token(user.id)

    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/auth/refresh",
    )
    log.info("auth.login", email=user.email, role=user.role)
    return {
        "access_token": access,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "team_id": user.team_id,
        },
    }


@router.post("/refresh")
async def refresh(response: Response, refresh_token: Optional[str] = Cookie(None)):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await db.get_user_by_id(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    access = create_access_token(user.id, user.email, user.role, user.team_id or "")
    new_refresh = create_refresh_token(user.id)
    response.set_cookie(
        key="refresh_token",
        value=new_refresh,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/auth/refresh",
    )
    return {"access_token": access, "token_type": "bearer"}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("refresh_token", path="/auth/refresh")
    return {"status": "ok"}


@router.get("/me")
async def me(user: Optional[dict] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/demo-users")
async def demo_users():
    """Returns seeded demo user credentials for the demo login panel."""
    return [
        {"email": "admin@rait.ai",   "password": "admin123",   "role": "super_admin",  "full_name": "Super Admin"},
        {"email": "manager@rait.ai", "password": "manager123", "role": "qa_manager",   "full_name": "QA Manager"},
        {"email": "qa@rait.ai",      "password": "qa123",      "role": "qa_engineer",  "full_name": "QA Engineer"},
        {"email": "dev@rait.ai",     "password": "dev123",     "role": "developer",    "full_name": "Developer"},
    ]
