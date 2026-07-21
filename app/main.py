from contextlib import asynccontextmanager
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .analysis import analyze_email, highlighted_parts
from .auth import get_current_user
from .database import get_db
from .demo_data import load_demo_emails
from .models import BusinessResource, Email, Task, User
from .resources import MAX_RESOURCE_CHARS, RESOURCE_TYPES, seed_demo_resources


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


@app.get("/demo")
def demo(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    load_demo_emails(db, current_user)
    seed_demo_resources(db, current_user)
    return RedirectResponse("/inbox", 303)


@app.get("/inbox")
def inbox(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    emails = db.scalars(
        select(Email).where(Email.user_id == current_user.id).order_by(Email.received_at.desc())
    ).all()
    return templates.TemplateResponse(request, "inbox.html", {"emails": emails})


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
def dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tasks = db.scalars(
        select(Task)
        .options(joinedload(Task.email).joinedload(Email.analysis))
        .where(Task.user_id == current_user.id)
        .order_by(Task.deadline)
    ).all()
    return templates.TemplateResponse(request, "dashboard.html", {"tasks": tasks})


@app.get("/tasks/{task_id}")
def task_detail(task_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = db.scalar(
        select(Task)
        .options(joinedload(Task.email).joinedload(Email.analysis))
        .where(Task.id == task_id, Task.user_id == current_user.id)
    )
    if not task:
        raise HTTPException(404, "Task not found")
    before, evidence, after = highlighted_parts(task.email)
    guidance_views = []
    structured = json.loads(task.email.analysis.structured_result or "{}")
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
    return templates.TemplateResponse(request, "task.html", {"task": task, "before": before, "evidence": evidence, "after": after, "guidance_views": guidance_views})


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
