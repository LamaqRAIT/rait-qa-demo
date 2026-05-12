"""
FastAPI dependency injection for authentication and role-based access.
Usage:
    @router.post("/approve/{run_id}")
    async def approve(run_id: str, user: dict = Depends(require_role("qa_engineer", "qa_manager", "super_admin"))):
        ...
"""
from typing import Optional
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import app.db as db
from app.auth.jwt import decode_token

bearer = HTTPBearer(auto_error=False)

ROLE_HIERARCHY = {
    "super_admin": 5,
    "qa_manager": 4,
    "qa_engineer": 3,
    "developer": 2,
    "system_agent": 1,
}


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Security(bearer),
) -> Optional[dict]:
    """Returns the decoded token payload dict, or None if unauthenticated."""
    if not creds:
        return None
    payload = decode_token(creds.credentials)
    if not payload or payload.get("type") != "access":
        return None
    user = await db.get_user_by_id(payload["sub"])
    if not user or not user.is_active:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "team_id": user.team_id,
        "full_name": user.full_name,
    }


def require_role(*roles: str):
    """Returns a FastAPI Depends function that enforces at least one of the given roles."""
    async def _dep(user: Optional[dict] = Depends(get_current_user)) -> dict:
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_level = ROLE_HIERARCHY.get(user["role"], 0)
        required_level = min(ROLE_HIERARCHY.get(r, 0) for r in roles)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user['role']}' is not permitted. Required: one of {roles}",
            )
        return user
    return _dep


def optional_auth():
    """Soft auth — returns user dict or None. Endpoints open to all but aware of who's calling."""
    async def _dep(user: Optional[dict] = Depends(get_current_user)) -> Optional[dict]:
        return user
    return _dep
