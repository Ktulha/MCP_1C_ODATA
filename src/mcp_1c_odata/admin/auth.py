import os
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError

from mcp_1c_odata.admin.config import (
    ADMIN_PASSWORD, ADMIN_SECRET, JWT_ALGORITHM, JWT_TTL_HOURS
)
from mcp_1c_odata.admin.audit import log as audit_log

COOKIE_NAME = "mcp_admin_session"


def _check_master_password(password: str) -> bool:
    expected = ADMIN_PASSWORD
    if not expected:
        return False
    # Поддержка как plain, так и bcrypt-хэша
    if expected.startswith("$2b$") or expected.startswith("$2a$"):
        return bcrypt.checkpw(password.encode(), expected.encode())
    return password == expected


def create_session_token() -> str:
    payload = {
        "sub": "admin",
        "exp": datetime.utcnow() + timedelta(hours=JWT_TTL_HOURS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, ADMIN_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, ADMIN_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


def require_login(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    payload = decode_session_token(token)
    if not payload:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return payload


def login_redirect(token: str, next_url: str = "/") -> RedirectResponse:
    resp = RedirectResponse(next_url, status_code=303)
    resp.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="lax",
        max_age=JWT_TTL_HOURS * 3600,
    )
    return resp


def logout_redirect() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


def audit(action: str, request: Request, target: str | None = None, details: str | None = None):
    ip = request.client.host if request.client else None
    token = request.cookies.get(COOKIE_NAME)
    actor = "anonymous"
    if token:
        payload = decode_session_token(token)
        if payload:
            actor = payload.get("sub", "unknown")
    audit_log(actor=actor, action=action,
              target=target, details=details, ip=ip)
