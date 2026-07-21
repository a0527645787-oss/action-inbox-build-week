import base64
import hashlib
import html
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis import analyze_email
from .models import Email, GmailCredential, GmailOAuthState, User, utcnow


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_QUERY = "in:inbox newer_than:7d -in:spam -in:trash"
GMAIL_MESSAGE_LIMIT = 25
GMAIL_TASK_LIMIT = 20
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class GmailConfigurationError(RuntimeError):
    pass


class GmailSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class GmailSyncResult:
    scope: str
    candidates: int
    new_messages: int
    tasks_created: int


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise GmailConfigurationError(f"{name} is not configured")
    return value


def gmail_configured() -> bool:
    return all(os.getenv(name, "").strip() for name in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REDIRECT_URI", "TOKEN_ENCRYPTION_KEY"))


def _fernet() -> Fernet:
    try:
        return Fernet(_required("TOKEN_ENCRYPTION_KEY").encode())
    except (ValueError, TypeError) as exc:
        raise GmailConfigurationError("TOKEN_ENCRYPTION_KEY is invalid") from exc


def encrypt_tokens(tokens: dict) -> str:
    return _fernet().encrypt(json.dumps(tokens, separators=(",", ":")).encode()).decode()


def decrypt_tokens(value: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(value.encode()).decode())
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise GmailConfigurationError("Stored Gmail credential cannot be decrypted") from exc


def begin_oauth(db: Session, user: User) -> str:
    state = secrets.token_urlsafe(32)
    db.add(GmailOAuthState(user_id=user.id, state_hash=hashlib.sha256(state.encode()).hexdigest(), expires_at=utcnow() + timedelta(minutes=10)))
    db.commit()
    params = {
        "client_id": _required("GMAIL_CLIENT_ID"),
        "redirect_uri": _required("GMAIL_REDIRECT_URI"),
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def complete_oauth(db: Session, user: User, state: str, code: str, client: httpx.Client | None = None) -> GmailCredential:
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    oauth_state = db.scalar(select(GmailOAuthState).where(GmailOAuthState.state_hash == state_hash, GmailOAuthState.user_id == user.id))
    if not oauth_state or oauth_state.used_at or oauth_state.expires_at < utcnow():
        raise GmailSyncError("OAuth state is invalid or expired")
    oauth_state.used_at = utcnow()
    db.commit()
    owned = client or httpx.Client(timeout=30)
    try:
        response = owned.post(GOOGLE_TOKEN_URL, data={
            "client_id": _required("GMAIL_CLIENT_ID"), "client_secret": _required("GMAIL_CLIENT_SECRET"),
            "code": code, "grant_type": "authorization_code", "redirect_uri": _required("GMAIL_REDIRECT_URI"),
        })
        response.raise_for_status()
        tokens = response.json()
        granted = set(tokens.get("scope", "").split())
        if GMAIL_SCOPE not in granted:
            raise GmailSyncError("Gmail read-only scope was not granted")
        profile = owned.get(f"{GMAIL_API}/profile", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        profile.raise_for_status()
        account_email = profile.json()["emailAddress"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise GmailSyncError("Google OAuth exchange failed") from exc
    finally:
        if client is None:
            owned.close()
    tokens["expires_at"] = (datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat()
    credential = db.scalar(select(GmailCredential).where(GmailCredential.user_id == user.id, GmailCredential.account_email == account_email))
    if credential is None:
        credential = GmailCredential(user_id=user.id, account_email=account_email, encrypted_token="", scopes=GMAIL_SCOPE)
        db.add(credential)
    credential.encrypted_token = encrypt_tokens(tokens)
    credential.scopes = GMAIL_SCOPE
    db.commit(); db.refresh(credential)
    return credential


def _access_token(credential: GmailCredential, client: httpx.Client) -> str:
    tokens = decrypt_tokens(credential.encrypted_token)
    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at > datetime.now(UTC) + timedelta(minutes=1):
        return tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise GmailSyncError("Gmail authorization must be renewed")
    try:
        response = client.post(GOOGLE_TOKEN_URL, data={
            "client_id": _required("GMAIL_CLIENT_ID"), "client_secret": _required("GMAIL_CLIENT_SECRET"),
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        })
        response.raise_for_status(); refreshed = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise GmailSyncError("Gmail token refresh failed") from exc
    tokens.update(refreshed); tokens["refresh_token"] = refresh_token
    tokens["expires_at"] = (datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat()
    credential.encrypted_token = encrypt_tokens(tokens)
    return tokens["access_token"]


def _decode(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", errors="replace")


def _body(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and mime == "text/plain":
        return _decode(data)
    parts = payload.get("parts", [])
    for part in parts:
        text = _body(part)
        if text:
            return text
    if data and mime == "text/html":
        raw = _decode(data)
        return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()
    return ""


def _header(message: dict, name: str) -> str:
    return next((item.get("value", "") for item in message.get("payload", {}).get("headers", []) if item.get("name", "").casefold() == name.casefold()), "")


def _received_at(message: dict) -> datetime:
    internal = message.get("internalDate")
    if internal:
        return datetime.fromtimestamp(int(internal) / 1000, UTC).replace(tzinfo=None)
    try:
        return parsedate_to_datetime(_header(message, "Date")).astimezone(UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        return utcnow()


def sync_gmail(db: Session, user: User, credential: GmailCredential, client: httpx.Client | None = None) -> GmailSyncResult:
    if credential.user_id != user.id or credential.scopes != GMAIL_SCOPE:
        raise GmailSyncError("Gmail credential ownership or scope is invalid")
    owned = client or httpx.Client(timeout=30)
    try:
        access_token = _access_token(credential, owned)
        headers = {"Authorization": f"Bearer {access_token}"}
        response = owned.get(f"{GMAIL_API}/messages", headers=headers, params={"labelIds": "INBOX", "q": GMAIL_QUERY, "maxResults": GMAIL_MESSAGE_LIMIT})
        response.raise_for_status()
        candidates = response.json().get("messages", [])[:GMAIL_MESSAGE_LIMIT]
        known = set(db.scalars(select(Email.gmail_message_id).where(Email.user_id == user.id, Email.gmail_message_id.is_not(None))).all())
        imported = tasks_created = 0
        for item in candidates:
            message_id = item.get("id")
            if not message_id or message_id in known or tasks_created >= GMAIL_TASK_LIMIT:
                continue
            detail = owned.get(f"{GMAIL_API}/messages/{message_id}", headers=headers, params={"format": "full"})
            detail.raise_for_status(); message = detail.json()
            labels = set(message.get("labelIds", []))
            if "INBOX" not in labels or labels.intersection({"SPAM", "TRASH"}):
                continue
            body = _body(message.get("payload", {})).strip()[:20000]
            if not body:
                continue
            email = Email(user_id=user.id, external_id=f"gmail:{message_id}", gmail_message_id=message_id,
                          gmail_thread_id=message.get("threadId"), sender=_header(message, "From")[:255] or "Unknown sender",
                          subject=_header(message, "Subject")[:255] or "(no subject)", received_at=_received_at(message),
                          body=body, source="gmail", analyzed=False)
            db.add(email); db.commit(); db.refresh(email)
            analysis = analyze_email(db, email)
            imported += 1
            if analysis.action_required and email.task:
                tasks_created += 1
            known.add(message_id)
        credential.last_synced_at = utcnow(); db.commit()
        return GmailSyncResult(GMAIL_QUERY, len(candidates), imported, tasks_created)
    except httpx.HTTPError as exc:
        raise GmailSyncError("Gmail read-only sync failed") from exc
    finally:
        if client is None:
            owned.close()
