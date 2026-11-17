"""Password hashing (scrypt) and JWT bearer authentication."""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config
from .models import SessionLocal, User

_bearer = HTTPBearer(auto_error=False)
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, digest_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    except ValueError:
        return False
    actual = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return hmac.compare_digest(actual, expected)


def create_token(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_TTL_HOURS)
    return jwt.encode({"sub": user_id, "exp": exp}, config.SECRET_KEY, algorithm="HS256")


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> User:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    with SessionLocal() as db:
        user = db.get(User, payload.get("sub"))
    if user is None:
        raise HTTPException(401, "User no longer exists")
    return user
