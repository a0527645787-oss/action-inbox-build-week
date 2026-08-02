import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, UserSession, utcnow


DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USER_EMAIL = "demo@actioninbox.local"
SESSION_COOKIE_NAME = "actioninbox_session"
OAUTH_COOKIE_NAME = "actioninbox_oauth_state"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


class SessionConfigurationError(RuntimeError):
    pass


def _session_secret() -> bytes:
    value = os.getenv("SESSION_SECRET", "")
    if len(value) < 32:
        raise SessionConfigurationError("SESSION_SECRET must contain at least 32 characters")
    return value.encode()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _signed_value(value: str) -> str:
    signature = hmac.new(_session_secret(), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{signature}"


def _verified_value(value: str | None) -> str | None:
    if not value or "." not in value:
        return None
    raw, signature = value.rsplit(".", 1)
    expected = hmac.new(_session_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return raw if secrets.compare_digest(signature, expected) else None


def ensure_demo_user(db: Session) -> User:
    user = db.get(User, DEMO_USER_ID)
    if user is None:
        user = User(
            id=DEMO_USER_ID,
            email=DEMO_USER_EMAIL,
            display_name="ActionInbox Demo",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def create_user_session(db: Session, user: User) -> str:
    raw_token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_token_hash(raw_token),
            expires_at=utcnow() + timedelta(seconds=SESSION_MAX_AGE_SECONDS),
        )
    )
    db.commit()
    return _signed_value(raw_token)


def _session_from_request(request: Request, db: Session) -> UserSession | None:
    try:
        raw_token = _verified_value(request.cookies.get(SESSION_COOKIE_NAME))
    except SessionConfigurationError:
        return None
    if not raw_token:
        return None
    return db.scalar(
        select(UserSession).where(
            UserSession.token_hash == _token_hash(raw_token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utcnow(),
        )
    )


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    session = _session_from_request(request, db)
    return db.get(User, session.user_id) if session else None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_optional_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Sign in is required",
            headers={"Location": "/"},
        )
    return user


def require_personal_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.id == DEMO_USER_ID:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Connect your own Gmail to access this page",
            headers={"Location": "/"},
        )
    return current_user


def revoke_request_session(request: Request, db: Session) -> None:
    session = _session_from_request(request, db)
    if session is not None:
        session.revoked_at = utcnow()
        db.commit()


def set_session_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value,
        max_age=SESSION_MAX_AGE_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )


def set_oauth_cookie(response: Response, state: str) -> None:
    response.set_cookie(
        OAUTH_COOKIE_NAME,
        _signed_value(state),
        max_age=600,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/auth/google/callback",
    )


def verify_oauth_cookie(request: Request, state: str) -> bool:
    try:
        stored = _verified_value(request.cookies.get(OAUTH_COOKIE_NAME))
    except SessionConfigurationError:
        return False
    return bool(stored and state and secrets.compare_digest(stored, state))


def clear_oauth_cookie(response: Response) -> None:
    response.delete_cookie(
        OAUTH_COOKIE_NAME,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/auth/google/callback",
    )
