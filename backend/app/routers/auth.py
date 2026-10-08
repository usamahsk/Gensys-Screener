"""Mock dashboard authentication: register + signin, no API enforcement.

All voice-agent tool + webhook + dashboard APIs stay OPEN (no key / no
Authorization header required) because the Dograh API key lives in the tool
call / integration settings, not in a user session. This router only issues
opaque signed session tokens so the dashboard UI can gate itself.
"""

import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models import AppUser

router = APIRouter(prefix="/api/auth", tags=["auth"])

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days — mock session, not a real JWT


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"pbkdf2$120000${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, hexdk = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexdk)
    except Exception:
        return False


def _sign_token(user_id: int, email: str) -> str:
    secret = get_settings().auth_secret.encode()
    exp = int(time.time()) + TOKEN_TTL_SECONDS
    body = f"{user_id}:{email}:{exp}".encode()
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    raw = f"{user_id}:{email}:{exp}:{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _parse_token(token: str) -> tuple[int, str]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        uid_s, email, exp_s, sig = raw.split(":", 3)
        secret = get_settings().auth_secret.encode()
        body = f"{uid_s}:{email}:{exp_s}".encode()
        expect = hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            raise ValueError("bad signature")
        if int(exp_s) < int(time.time()):
            raise ValueError("expired")
        return int(uid_s), email
    except Exception:
        raise HTTPException(401, "Invalid or expired session — please sign in again")


class RegisterRequest(BaseModel):
    name: str = ""
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _norm_email(cls, v: object) -> str:
        s = str(v or "").strip().lower()
        if "@" not in s or "." not in s.split("@")[-1]:
            raise ValueError("Enter a valid email address")
        return s[:256]


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def _norm_email(cls, v: object) -> str:
        return str(v or "").strip().lower()[:256]


class AuthUserOut(BaseModel):
    id: int
    name: str
    email: str


class AuthResponse(BaseModel):
    token: str
    user: AuthUserOut


def _out(user: AppUser) -> AuthUserOut:
    return AuthUserOut(id=user.id, name=user.name or "", email=user.email)


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    email = req.email.strip().lower()
    if len(req.password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
    existing = (
        await db.execute(select(AppUser).where(AppUser.email == email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "This email is already registered — please sign in")
    user = AppUser(email=email, name=req.name.strip()[:128],
                   password_hash=_hash_password(req.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return AuthResponse(token=_sign_token(user.id, user.email), user=_out(user))


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    email = req.email.strip().lower()
    user = (
        await db.execute(select(AppUser).where(AppUser.email == email))
    ).scalar_one_or_none()
    if not user or not _verify_password(req.password, user.password_hash or ""):
        raise HTTPException(401, "Invalid email or password")
    return AuthResponse(token=_sign_token(user.id, user.email), user=_out(user))


@router.get("/me", response_model=AuthUserOut)
async def me(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing session — please sign in")
    uid, _ = _parse_token(authorization.split(" ", 1)[1].strip())
    user = (
        await db.execute(select(AppUser).where(AppUser.id == uid))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(401, "Session user no longer exists — please sign in again")
    return _out(user)
