from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .analysis import analyze_email, highlighted_parts
from .auth import get_current_user
from .database import get_db
from .demo_data import ingest_demo_emails
from .execution import PACKAGE_EXECUTORS, build_execution_package, build_review_prompt, codex_deep_link, package_as_text, parse_structured_result
from .gmail import GMAIL_MESSAGE_LIMIT, GMAIL_QUERY, GMAIL_SCOPE, GMAIL_TASK_LIMIT, GmailSyncError, begin_oauth, complete_oauth, gmail_configured, sync_gmail
from .mcp import handle_mcp
from .models import BusinessResource, Email, GmailCredential, Task, User
from .resources import MAX_RESOURCE_CHARS, RESOURCE_TYPES, seed_demo_resources
from .triage import triage_unanalyzed_emails


ROOT = Path(__file__).parent


@asynccontextmanager
async def lifespan(app):
    # Schema creation and upgrades are intentionally owned by Alembic.
    yield


app = FastAPI(title="ActionInbox", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def landing(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "landing.html", {"current_user": current_user})


@app.post("/demo")
def demo(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    seed_demo_resources(db, current_user)
    ingest_demo_emails(db, current_user)
    result = triage_unanalyzed_emails(db, current_user.id)
    return RedirectResponse(f"/dashboard?emails_checked={result.emails_checked}&tasks_created={result.tasks_created}", 303)


@app.get("/inbox")
def inbox(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emails = db.scalars(
        select(Email).where(Email.user_id == current_user.id).order_by(Email.received_at.desc())
    ).all()
    return templates.TemplateResponse(request, "inbox.html", {"emails": emails})


@app.get("/gmail")
def gmail_page(request: Request, candidates: int = 0, imported: int = 0, tasks_created: int = 0,
               db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    credential = db.scalar(select(GmailCredential).where(GmailCredential.user_id == current_user.id).order_by(GmailCredential.updated_at.desc()))
    return templates.TemplateResponse(request, "gmail.html", {"credential": credential, "configured": gmail_configured(),
        "scope": GMAIL_SCOPE, "query": GMAIL_QUERY, "message_limit": GMAIL_MESSAGE_LIMIT, "task_limit": GMAIL_TASK_LIMIT,
        "candidates": max(candidates, 0), "imported": max(imported, 0), "tasks_created": max(tasks_created, 0)})


@app.get("/auth/google/start")
def gmail_oauth_start(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return RedirectResponse(begin_oauth(db, current_user), 303)


@app.get("/auth/google/callback")
def gmail_oauth_callback(state: str, code: str = "", error: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if error or not code:
        raise HTTPException(400, "Google authorization was not completed")
    try:
        complete_oauth(db, current_user, state, code)
    except GmailSyncError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/gmail", 303)


@app.post("/gmail/sync")
def gmail_sync(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    credential = db.scalar(select(GmailCredential).where(GmailCredential.user_id == current_user.id).order_by(GmailCredential.updated_at.desc()))
    if not credential:
        raise HTTPException(409, "Connect Gmail before syncing")
    try:
        result = sync_gmail(db, current_user, credential)
    except GmailSyncError as exc:
        raise HTTPException(502, str(exc)) from exc
    return RedirectResponse(f"/gmail?candidates={result.candidates}&imported={result.new_messages}&tasks_created={result.tasks_created}", 303)


@app.post("/mcp")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    return await handle_mcp(request, db)


@app.get("/integrations")
def integrations_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "integrations.html", {"mcp_url": "https://actioninbox.16-192-83-71.nip.io/mcp"})


@app.post("/api/inbox/analyze-all")
def analyze_inbox(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = triage_unanalyzed_emails(db, current_user.id)
    return RedirectResponse(f"/dashboard?emails_checked={result.emails_checked}&tasks_created={result.tasks_created}", 303)


@app.get("/emails/{email_id}")
def email_detail(email_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    email = db.scalar(select(Email).where(Email.id == email_id, Email.user_id == current_user.id))
    if not email:
        raise HTTPException(404, "Email not found")
    return templates.TemplateResponse(request, "email.html", {"email": email})


def _owned_email(db: Session, email_id: int, user_id: str) -> Email:
    email = db.scalar(
        select(Email)
        .options(joinedload(Email.analysis), joinedload(Email.task))
        .where(Email.id == email_id, Email.user_id == user_id)
    )
    if not email:
        raise HTTPException(404, "Email not found")
    return email


@app.post("/api/emails/{email_id}/analyze")
def analyze(email_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    email = _owned_email(db, email_id, current_user.id)
    analysis = analyze_email(db, email)
    return RedirectResponse(f"/tasks/{email.task.id}" if analysis.action_required else f"/emails/{email.id}", 303)


@app.post("/api/emails/{email_id}/reanalyze")
def reanalyze(email_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    email = _owned_email(db, email_id, current_user.id)
    analysis = analyze_email(db, email, force=True)
    destination = f"/tasks/{email.task.id}" if analysis.action_required and email.task else f"/emails/{email.id}"
    return RedirectResponse(destination, 303)


@app.get("/dashboard")
def dashboard(request: Request, emails_checked: int = 0, tasks_created: int = 0, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tasks = db.scalars(
        select(Task)
        .options(joinedload(Task.email).joinedload(Email.analysis))
        .where(Task.user_id == current_user.id)
        .order_by(Task.deadline)
    ).all()
    return templates.TemplateResponse(request, "dashboard.html", {"tasks": tasks, "emails_checked": max(emails_checked, 0), "tasks_created": max(tasks_created, 0)})


def _owned_task(db: Session, task_id: int, user_id: str) -> Task:
    task = db.scalar(
        select(Task)
        .options(joinedload(Task.email).joinedload(Email.analysis))
        .where(Task.id == task_id, Task.user_id == user_id)
    )
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.get("/tasks/{task_id}")
def task_detail(task_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = _owned_task(db, task_id, current_user.id)
    before, evidence, after = highlighted_parts(task.email)
    guidance_views = []
    result = parse_structured_result(task.email.analysis.structured_result)
    structured = result.model_dump()
    for guidance in structured.get("resource_guidance", []):
        try:
            resource_id = int(guidance["resource_id"].removeprefix("resource-"))
        except (KeyError, ValueError):
            continue
        resource = db.scalar(
            select(BusinessResource).where(
                BusinessResource.id == resource_id,
                BusinessResource.user_id == current_user.id,
            )
        )
        evidence_data = guidance.get("resource_evidence", {})
        if not resource or resource.title != guidance.get("resource_title"):
            continue
        start = evidence_data.get("start_offset")
        end = evidence_data.get("end_offset")
        quote = evidence_data.get("exact_quote")
        if not isinstance(start, int) or not isinstance(end, int) or resource.content[start:end] != quote:
            continue
        guidance_views.append({"guidance": guidance, "resource": resource, "before": resource.content[:start], "quote": quote, "after": resource.content[end:]})
    work_prompt = build_review_prompt(task, result, "CHATGPT_WORK") if result.execution_guidance else None
    codex_url = codex_deep_link(task, result) if result.execution_guidance else None
    return templates.TemplateResponse(request, "task.html", {"task": task, "before": before, "evidence": evidence, "after": after, "guidance_views": guidance_views, "result": result, "execution": result.execution_guidance, "work_prompt": work_prompt, "codex_url": codex_url})


@app.get("/tasks/{task_id}/execution-package", response_class=HTMLResponse)
def execution_package_preview(task_id: int, request: Request, executor: str = "CHATGPT_WORK", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if executor not in PACKAGE_EXECUTORS:
        raise HTTPException(422, "Unsupported package executor")
    task = _owned_task(db, task_id, current_user.id)
    try:
        result = parse_structured_result(task.email.analysis.structured_result)
        package = build_execution_package(task, result, executor)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return templates.TemplateResponse(request, "execution_package.html", {"task": task, "result": result, "execution": result.execution_guidance, "package": package, "package_text": package_as_text(package), "executor": executor})


@app.get("/tasks/{task_id}/execution-package/download")
def execution_package_download(task_id: int, executor: str = "CHATGPT_WORK", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if executor not in PACKAGE_EXECUTORS:
        raise HTTPException(422, "Unsupported package executor")
    task = _owned_task(db, task_id, current_user.id)
    try:
        package = build_execution_package(task, parse_structured_result(task.email.analysis.structured_result), executor)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(package_as_text(package), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="actioninbox-task-{task.id}.json"'})


@app.get("/resources")
def resources_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    items = db.scalars(
        select(BusinessResource)
        .where(BusinessResource.user_id == current_user.id)
        .order_by(BusinessResource.updated_at.desc())
    ).all()
    return templates.TemplateResponse(request, "resources.html", {"resources": items})


@app.get("/resources/new")
def resource_new(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "resource_form.html", {"resource": None, "resource_types": RESOURCE_TYPES})


@app.post("/resources")
def resource_create(title: str = Form(...), resource_type: str = Form(...), content: str = Form(...), organization_team: str = Form(""), enabled: bool = Form(False), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    title, content = title.strip(), content.strip()
    if not title or not content or len(content) > MAX_RESOURCE_CHARS or resource_type not in RESOURCE_TYPES:
        raise HTTPException(422, "Invalid business resource")
    db.add(BusinessResource(user_id=current_user.id, title=title, resource_type=resource_type, content=content, organization_team=organization_team.strip() or None, enabled=enabled))
    db.commit()
    return RedirectResponse("/resources", 303)


def _owned_resource(db: Session, resource_id: int, user_id: str) -> BusinessResource:
    resource = db.scalar(
        select(BusinessResource).where(BusinessResource.id == resource_id, BusinessResource.user_id == user_id)
    )
    if not resource:
        raise HTTPException(404, "Resource not found")
    return resource


@app.get("/resources/{resource_id}")
def resource_view(resource_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "resource_view.html", {"resource": _owned_resource(db, resource_id, current_user.id)})


@app.get("/resources/{resource_id}/edit")
def resource_edit(resource_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "resource_form.html", {"resource": _owned_resource(db, resource_id, current_user.id), "resource_types": RESOURCE_TYPES})


@app.post("/resources/{resource_id}")
def resource_update(resource_id: int, title: str = Form(...), resource_type: str = Form(...), content: str = Form(...), organization_team: str = Form(""), enabled: bool = Form(False), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    resource = _owned_resource(db, resource_id, current_user.id)
    title, content = title.strip(), content.strip()
    if not title or not content or len(content) > MAX_RESOURCE_CHARS or resource_type not in RESOURCE_TYPES:
        raise HTTPException(422, "Invalid business resource")
    resource.title, resource.resource_type, resource.content = title, resource_type, content
    resource.organization_team, resource.enabled = organization_team.strip() or None, enabled
    db.commit()
    return RedirectResponse(f"/resources/{resource.id}", 303)


@app.post("/resources/{resource_id}/toggle")
def resource_toggle(resource_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    resource = _owned_resource(db, resource_id, current_user.id)
    resource.enabled = not resource.enabled
    db.commit()
    return RedirectResponse("/resources", 303)


@app.post("/resources/{resource_id}/delete")
def resource_delete(resource_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db.delete(_owned_resource(db, resource_id, current_user.id))
    db.commit()
    return RedirectResponse("/resources", 303)
