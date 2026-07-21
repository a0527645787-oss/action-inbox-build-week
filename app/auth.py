import os

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USER_EMAIL = "demo@actioninbox.local"


def local_demo_auth_enabled() -> bool:
    return os.getenv("LOCAL_DEMO_AUTH_ENABLED", "").casefold() in {"1", "true", "yes"}


def ensure_demo_user(db: Session) -> User:
    user = db.get(User, DEMO_USER_ID)
    if user is None:
        user = User(id=DEMO_USER_ID, email=DEMO_USER_EMAIL, display_name="ActionInbox Demo")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_current_user(db: Session = Depends(get_db)) -> User:
    if not local_demo_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )
    return ensure_demo_user(db)
