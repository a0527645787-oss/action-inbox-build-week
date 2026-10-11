import base64
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from .sync_analysis import analyze_sync_batch
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import DEMO_USER_ID
from .models import Analysis, Email, GmailCredential, GmailOAuthState, GmailSyncJob, Task, User, utcnow


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
OAUTH_SCOPE = f"openid email {GMAIL_SCOPE}"
GMAIL_QUERY = "in:inbox newer_than:7d -in:spam -in:trash -category:promotions -category:social -category:forums"
NON_ACTION_CATEGORIES = frozenset({"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"})
GMAIL_PAGE_SIZE = 100
GMAIL_BOOTSTRAP_PAGE_LIMIT = 10
GMAIL_DETAIL_CONCURRENCY = 4
GMAIL_RETRY_LIMIT = 4
GMAIL_JOB_LEASE_MINUTES = 5
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
logger = logging.getLogger(__name__)


class GmailConfigurationError(RuntimeError):
    pass


class GmailSyncError(RuntimeError):
    pass


class GmailReconnectRequired(GmailSyncError):
    pass


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


def begin_oauth(db: Session) -> tuple[str, str]:
    state = secrets.token_urlsafe(32)
    db.add(
        GmailOAuthState(
            user_id=None,
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    db.commit()
    params = {
        "client_id": _required("GMAIL_CLIENT_ID"),
        "redirect_uri": _required("GMAIL_REDIRECT_URI"),
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "false",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state


def _copy_legacy_gmail_data(db: Session, user: User, account_email: str) -> int:
    legacy_credential = db.scalar(
        select(GmailCredential).where(
            GmailCredential.user_id == DEMO_USER_ID,
            GmailCredential.account_email == account_email,
        )
    )
    if legacy_credential is None:
        return 0
    moved = 0
    legacy_emails = db.scalars(
        select(Email).where(Email.user_id == DEMO_USER_ID, Email.source == "gmail")
    ).all()
    for source in legacy_emails:
        target = db.scalar(
            select(Email).where(
                Email.user_id == user.id,
                Email.gmail_message_id == source.gmail_message_id,
            )
        )
        if target is None:
            target = Email(
                user_id=user.id,
                external_id=source.external_id,
                gmail_message_id=source.gmail_message_id,
                gmail_thread_id=source.gmail_thread_id,
                sender=source.sender,
                subject=source.subject,
                received_at=source.received_at,
                body=source.body,
                source="gmail",
                analyzed=source.analyzed,
            )
            db.add(target)
            db.flush()
        if source.analysis and target.analysis is None:
            item = source.analysis
            db.add(
                Analysis(
                    user_id=user.id,
                    email_id=target.id,
                    classification=item.classification,
                    action_required=item.action_required,
                    summary=item.summary,
                    evidence_quote=item.evidence_quote,
                    evidence_start=item.evidence_start,
                    evidence_end=item.evidence_end,
                    suggestion=item.suggestion,
                    structured_result=item.structured_result,
                    source=item.source,
                    model=item.model,
                    error_message=item.error_message,
                    analyzed_at=item.analyzed_at,
                )
            )
        if source.task and target.task is None:
            item = source.task
            db.add(
                Task(
                    user_id=user.id,
                    email_id=target.id,
                    title=item.title,
                    deadline=item.deadline,
                    deadline_text=item.deadline_text,
                )
            )
        db.delete(source)
        moved += 1
    db.delete(legacy_credential)
    db.flush()
    logger.info(
        "Claimed legacy Gmail data account_fingerprint=%s messages_moved=%s",
        hashlib.sha256(account_email.casefold().encode()).hexdigest()[:12],
        moved,
    )
    return moved


def complete_oauth(
    db: Session,
    state: str,
    code: str,
    client: httpx.Client | None = None,
) -> tuple[User, GmailCredential]:
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    oauth_state = db.scalar(
        select(GmailOAuthState).where(GmailOAuthState.state_hash == state_hash)
    )
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
        profile = owned.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        profile.raise_for_status()
        identity = profile.json()
        if not isinstance(identity, dict):
            raise GmailSyncError("Google identity information is missing or invalid")
        google_subject = identity.get("sub")
        account_email = identity.get("email")
        if (
            not isinstance(google_subject, str)
            or not google_subject.strip()
            or not isinstance(account_email, str)
            or not account_email.strip()
            or account_email.count("@") != 1
        ):
            raise GmailSyncError("Google identity information is missing or invalid")
        google_subject = google_subject.strip()
        account_email = account_email.strip()
        display_name = identity.get("name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = account_email.split("@", 1)[0]
        else:
            display_name = display_name.strip()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise GmailSyncError("Google OAuth exchange failed") from exc
    finally:
        if client is None:
            owned.close()
    tokens["expires_at"] = (datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat()
    user = db.scalar(select(User).where(User.google_subject == google_subject))
    if user is None:
        user = db.scalar(select(User).where(User.email == account_email))
        if user is not None and user.id == DEMO_USER_ID:
            user = None
    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            google_subject=google_subject,
            email=account_email,
            display_name=display_name[:255],
        )
        db.add(user)
        db.flush()
    elif user.google_subject is None:
        user.google_subject = google_subject
    user.email = account_email
    user.display_name = display_name[:255]
    _copy_legacy_gmail_data(db, user, account_email)
    credential = db.scalar(
        select(GmailCredential).where(
            GmailCredential.user_id == user.id,
            GmailCredential.account_email == account_email,
        )
    )
    if credential is None:
        credential = GmailCredential(user_id=user.id, account_email=account_email, encrypted_token="", scopes=GMAIL_SCOPE)
        db.add(credential)
    credential.encrypted_token = encrypt_tokens(tokens)
    credential.scopes = GMAIL_SCOPE
    oauth_state.user_id = user.id
    db.commit()
    db.refresh(credential)
    return user, credential


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
    except httpx.HTTPStatusError as exc:
        error_code = ""
        try:
            error_code = exc.response.json().get("error", "")
        except (ValueError, AttributeError):
            pass
        if error_code == "invalid_grant":
            raise GmailReconnectRequired("Gmail authorization expired; reconnect Gmail") from exc
        raise GmailSyncError("Gmail token refresh failed") from exc
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


def _is_non_action_category(message: dict) -> bool:
    """Use Gmail's existing category labels as a free, deterministic pre-filter."""
    return bool(set(message.get("labelIds", [])).intersection(NON_ACTION_CATEGORIES))


def enqueue_gmail_sync(db: Session, user: User, credential: GmailCredential) -> GmailSyncJob:
    if user.id == DEMO_USER_ID or credential.user_id != user.id or GMAIL_SCOPE not in credential.scopes.split():
        raise GmailSyncError("Gmail credential ownership or scope is invalid")
    active = db.scalar(
        select(GmailSyncJob).where(
            GmailSyncJob.credential_id == credential.id,
            GmailSyncJob.active_slot == 1,
        )
    )
    if active:
        return active
    job = GmailSyncJob(
        user_id=user.id,
        credential_id=credential.id,
        status="queued",
        active_slot=1,
        mode="history" if credential.history_id else "bootstrap",
        start_history_id=credential.history_id,
        page_token=credential.bootstrap_page_token if not credential.history_id else None,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        active = db.scalar(select(GmailSyncJob).where(GmailSyncJob.credential_id == credential.id, GmailSyncJob.active_slot == 1))
        if active:
            return active
        raise
    db.refresh(job)
    return job


def claim_gmail_sync_job(db: Session) -> GmailSyncJob | None:
    now = utcnow()
    candidate_id = db.scalar(
        select(GmailSyncJob.id).where(
            or_(
                GmailSyncJob.status == "queued",
                (GmailSyncJob.status == "running") & (GmailSyncJob.lease_expires_at < now),
            )
        ).order_by(GmailSyncJob.id).limit(1)
    )
    if candidate_id is None:
        return None
    claimed = db.execute(
        update(GmailSyncJob).where(
            GmailSyncJob.id == candidate_id,
            or_(
                GmailSyncJob.status == "queued",
                (GmailSyncJob.status == "running") & (GmailSyncJob.lease_expires_at < now),
            ),
        ).values(
            status="running",
            started_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=GMAIL_JOB_LEASE_MINUTES),
            attempts=GmailSyncJob.attempts + 1,
        )
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    job = db.get(GmailSyncJob, candidate_id)
    if job.attempts == 1:
        job.queue_wait_ms = max(0, round((now - job.created_at).total_seconds() * 1000))
        db.commit()
    return job


@contextmanager
def _timed(job, field):
    started = time.perf_counter()
    try:
        yield
    finally:
        setattr(job, field, (getattr(job, field) or 0) + round((time.perf_counter() - started) * 1000))


def _retry_after_seconds(response, attempt: int) -> float:
    value = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
    try:
        return min(max(float(value), 0.0), 30.0)
    except (TypeError, ValueError):
        return min(2 ** attempt, 8)


def _gmail_get(client: httpx.Client, url: str, headers: dict, params: dict | None = None):
    for attempt in range(GMAIL_RETRY_LIMIT):
        response = None
        try:
            response = client.get(url, headers=headers, params=params)
            status = response.status_code
            if status in {401, 403}:
                raise GmailReconnectRequired("Gmail authorization expired; reconnect Gmail")
            if status == 404 and url.endswith("/history"):
                raise GmailHistoryExpired("Gmail history cursor expired")
            if status != 429 and status < 500:
                response.raise_for_status()
                return response
            if attempt + 1 == GMAIL_RETRY_LIMIT:
                response.raise_for_status()
        except (GmailReconnectRequired, GmailHistoryExpired):
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise GmailSyncError("Gmail request was rejected") from exc
            if attempt + 1 == GMAIL_RETRY_LIMIT:
                raise
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt + 1 == GMAIL_RETRY_LIMIT:
                raise
        time.sleep(_retry_after_seconds(response, attempt))
    raise GmailSyncError("Gmail retry limit exhausted")


class GmailHistoryExpired(GmailSyncError):
    pass


def _history_message_ids(data: dict) -> list[str]:
    ids = []
    for event in data.get("history", []):
        for addition in event.get("messagesAdded", []):
            message_id = addition.get("message", {}).get("id")
            if message_id:
                ids.append(message_id)
    return list(dict.fromkeys(ids))


def _fetch_detail(client, headers, message_id):
    try:
        response = _gmail_get(client, f"{GMAIL_API}/messages/{message_id}", headers, {"format": "full"})
        return message_id, response.json(), None
    except GmailReconnectRequired:
        raise
    except Exception as exc:
        return message_id, None, "MESSAGE_FETCH_FAILED"


def _heartbeat(job: GmailSyncJob) -> None:
    now = utcnow()
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(minutes=GMAIL_JOB_LEASE_MINUTES)


def _store_page(db: Session, job: GmailSyncJob, messages: list[dict | None], message_ids: list[str]) -> None:
    existing = set(db.scalars(select(Email.gmail_message_id).where(Email.user_id == job.user_id, Email.gmail_message_id.in_(message_ids))).all())
    for message in messages:
        if not message:
            continue
        try:
            message_id = message.get("id")
            if not message_id:
                job.failures += 1
                continue
            if message_id in existing:
                job.duplicates += 1
                continue
            labels = set(message.get("labelIds", []))
            if "INBOX" not in labels or labels.intersection({"SPAM", "TRASH"}):
                job.skipped += 1
                continue
            if _is_non_action_category(message):
                job.skipped += 1
                continue
            body = _body(message.get("payload", {})).strip()[:20000]
            if not body:
                job.skipped += 1
                continue
            email = Email(
                user_id=job.user_id,
                external_id=f"gmail:{message_id}",
                gmail_message_id=message_id,
                gmail_thread_id=message.get("threadId"),
                sender=_header(message, "From")[:255] or "Unknown sender",
                subject=_header(message, "Subject")[:255] or "(no subject)",
                received_at=_received_at(message),
                body=body,
                source="gmail",
                analyzed=False,
                sync_job_id=job.id,
            )
        except Exception:
            job.failures += 1
            continue
        try:
            with db.begin_nested():
                db.add(email)
                db.flush()
            existing.add(message_id)
            job.imported += 1
        except IntegrityError:
            job.duplicates += 1


def _finish_job(db: Session, job: GmailSyncJob, credential: GmailCredential, status: str, safe_error: str | None = None) -> None:
    job.status = status
    job.phase = "finished"
    job.safe_error = safe_error
    job.active_slot = None
    job.completed_at = utcnow()
    job.lease_expires_at = None
    if status in {"succeeded", "partial"}:
        credential.last_synced_at = utcnow()
    db.commit()
    logger.info("sync_timing fetch_ms=%d storage_ms=%d triage_ms=%d queue_wait_ms=%d imported=%d tasks_created=%d",
                job.fetch_ms, job.storage_ms, job.triage_ms, job.queue_wait_ms, job.imported, job.tasks_created)


def run_gmail_sync_job(db: Session, job: GmailSyncJob, client: httpx.Client | None = None, *, triage: bool = True) -> None:
    credential = db.get(GmailCredential, job.credential_id)
    if not credential or credential.user_id != job.user_id or GMAIL_SCOPE not in credential.scopes.split():
        _finish_job(db, job, credential, "failed", "CREDENTIAL_UNAVAILABLE") if credential else _fail_orphan_job(db, job)
        return
    owned = client or httpx.Client(timeout=30)
    try:
        job.phase = "fetching"
        with _timed(job, "fetch_ms"):
            access_token = _access_token(credential, owned)
        headers = {"Authorization": f"Bearer {access_token}"}
        pages_this_run = 0
        while True:
            _heartbeat(job)
            db.commit()
            if job.mode == "history" and credential.history_id:
                params = {"startHistoryId": job.start_history_id or credential.history_id, "historyTypes": "messageAdded", "labelId": "INBOX", "maxResults": GMAIL_PAGE_SIZE}
                if job.page_token:
                    params["pageToken"] = job.page_token
                try:
                    with _timed(job, "fetch_ms"):
                        listing = _gmail_get(owned, f"{GMAIL_API}/history", headers, params).json()
                except GmailHistoryExpired:
                    credential.history_id = None
                    credential.bootstrap_page_token = None
                    job.mode = "bootstrap"
                    job.start_history_id = None
                    job.page_token = None
                    job.safe_error = "HISTORY_CURSOR_EXPIRED"
                    db.commit()
                    continue
                message_ids = _history_message_ids(listing)
            else:
                params = {"labelIds": "INBOX", "q": GMAIL_QUERY, "maxResults": GMAIL_PAGE_SIZE}
                token = job.page_token or credential.bootstrap_page_token
                if token:
                    params["pageToken"] = token
                with _timed(job, "fetch_ms"):
                    listing = _gmail_get(owned, f"{GMAIL_API}/messages", headers, params).json()
                message_ids = list(dict.fromkeys(item.get("id") for item in listing.get("messages", []) if item.get("id")))
            job.pages_listed += 1
            job.candidates += len(message_ids)
            with _timed(job, "storage_ms"):
                existing_ids = set(db.scalars(
                    select(Email.gmail_message_id).where(
                        Email.user_id == job.user_id,
                        Email.gmail_message_id.in_(message_ids),
                    )
                ).all()) if message_ids else set()
            job.duplicates += len(existing_ids)
            fetch_ids = [message_id for message_id in message_ids if message_id not in existing_ids]
            failures = 0
            details = []
            with _timed(job, "fetch_ms"):
                with ThreadPoolExecutor(max_workers=GMAIL_DETAIL_CONCURRENCY) as pool:
                    futures = [pool.submit(_fetch_detail, owned, headers, message_id) for message_id in fetch_ids]
                    for future in futures:
                        _, message, error = future.result()
                        failures += bool(error)
                        details.append(message)
            job.details_fetched += len(fetch_ids) - failures
            job.failures += failures
            with _timed(job, "storage_ms"):
                _store_page(db, job, details, fetch_ids)
                db.commit()
            next_token = listing.get("nextPageToken")
            job.page_token = next_token
            job.pending_history_id = listing.get("historyId") or job.pending_history_id
            _heartbeat(job)
            db.commit()
            if triage:
                analyze_sync_batch(db, job, _heartbeat)
            job.phase = "fetching"
            pages_this_run += 1
            if next_token and job.mode == "bootstrap" and pages_this_run >= GMAIL_BOOTSTRAP_PAGE_LIMIT:
                credential.bootstrap_page_token = next_token
                job.status = "queued"
                job.safe_error = None
                job.lease_expires_at = None
                db.commit()
                return
            if not next_token:
                break
        if job.mode == "bootstrap" and not job.failures:
            with _timed(job, "fetch_ms"):
                profile = _gmail_get(owned, f"{GMAIL_API}/profile", headers).json()
            credential.history_id = str(profile.get("historyId")) if profile.get("historyId") else None
            credential.bootstrap_page_token = None
        elif job.pending_history_id and not job.failures:
            credential.history_id = job.pending_history_id
        _finish_job(db, job, credential, "partial" if job.failures or job.analysis_failures else "succeeded", "MESSAGE_FAILURES" if job.failures or job.analysis_failures else None)
    except GmailReconnectRequired:
        db.rollback()
        job = db.get(GmailSyncJob, job.id)
        credential = db.get(GmailCredential, job.credential_id)
        _finish_job(db, job, credential, "failed", "RECONNECT_REQUIRED")
    except Exception as exc:
        db.rollback()
        job = db.get(GmailSyncJob, job.id)
        credential = db.get(GmailCredential, job.credential_id)
        _finish_job(db, job, credential, "failed", "GMAIL_SYNC_FAILED")
        logger.error("Gmail sync worker failed safely")
    finally:
        if client is None:
            owned.close()


def _fail_orphan_job(db: Session, job: GmailSyncJob) -> None:
    job.status = "failed"
    job.safe_error = "CREDENTIAL_UNAVAILABLE"
    job.active_slot = None
    job.completed_at = utcnow()
    job.lease_expires_at = None
    db.commit()


def disconnect_gmail(
    db: Session,
    user: User,
    credential: GmailCredential,
    client: httpx.Client | None = None,
) -> None:
    if credential.user_id != user.id:
        raise GmailSyncError("Gmail credential ownership is invalid")
    owned = client or httpx.Client(timeout=15)
    try:
        tokens = decrypt_tokens(credential.encrypted_token)
        token = tokens.get("refresh_token") or tokens.get("access_token")
        if token:
            try:
                owned.post(GOOGLE_REVOKE_URL, data={"token": token})
            except httpx.HTTPError:
                logger.warning(
                    "Gmail revocation request failed user_fingerprint=%s",
                    hashlib.sha256(user.id.encode()).hexdigest()[:12],
                )
        db.delete(credential)
        db.commit()
    finally:
        if client is None:
            owned.close()
