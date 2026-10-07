import os
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt
from bson import ObjectId
from fastapi import HTTPException, Request

JWT_ALGORITHM = "HS256"
ROLES = ["admin", "engineer", "viewer"]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


DEV_ENVS = {"development", "dev", "local", "test"}
_DEV_SECRET = "aptimizer-local-dev-jwt-secret-key-2026"


def _secret() -> str:
    """The signing key. The built-in fallback is public (it is in this file), so it is only
    ever used when ENV says this is a developer machine; anywhere else a missing
    JWT_SECRET is a hard error rather than a server that signs forgeable tokens."""
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret
    if (os.environ.get("ENV") or "").strip().lower() in DEV_ENVS:
        return _DEV_SECRET
    raise RuntimeError("JWT_SECRET is not set. Set it (e.g. `openssl rand -hex 32`) or set "
                       "ENV=development for a local machine.")


def check_secret() -> None:
    """Called at startup so a misconfigured deployment fails on boot, not on first login."""
    _secret()


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def set_auth_cookies(response, access_token: str, refresh_token: str):
    is_secure = (os.environ.get("COOKIE_SECURE") or "").strip().lower() in {"1", "true", "yes", "on"} or bool(os.environ.get("RENDER"))
    samesite = "none" if is_secure else "lax"
    response.set_cookie("access_token", access_token, httponly=True, secure=is_secure,
                        samesite=samesite, max_age=43200, path="/")
    response.set_cookie("refresh_token", refresh_token, httponly=True, secure=is_secure,
                        samesite=samesite, max_age=604800, path="/")


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])


def extract_token(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[7:]
    if not token:
        token = request.query_params.get("token")
    return token


def public_user(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "email": user["email"],
        "name": user.get("name", ""),
        "role": user.get("role", "engineer"),
        "org": user.get("org", ""),
        "contact": user.get("contact", ""),
    }


async def current_user_from_request(request: Request, db) -> dict:
    token = extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    try:
        user_oid = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid user identifier in token")
    user = await db.users.find_one({"_id": user_oid})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
