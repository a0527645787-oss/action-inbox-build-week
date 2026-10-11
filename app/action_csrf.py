"""Session-bound CSRF tokens for connector settings and action mutations."""
import hashlib
import hmac
import os
from fastapi import Form, HTTPException, Request


def csrf_token(request):
    secret = os.environ.get("SESSION_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Session security is not configured")
    return hmac.new(secret.encode(), ("action-csrf:" + request.cookies.get("actioninbox_session", "")).encode(), hashlib.sha256).hexdigest()


def require_action_csrf(request: Request, csrf: str = Form("")):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    supplied = request.headers.get("X-CSRF-Token") or csrf
    if not supplied or not hmac.compare_digest(supplied, csrf_token(request)):
        raise HTTPException(403, "Please reload this page before submitting the action.")
