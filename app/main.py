from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from .analysis import analyze_email, highlighted_parts
from .database import get_db, initialize_database
from .demo_data import load_demo_emails
from .models import Email, Task

ROOT = Path(__file__).parent
@asynccontextmanager
async def lifespan(app):
    initialize_database(); yield
app = FastAPI(title="ActionInbox", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT/"static"), name="static")
templates = Jinja2Templates(directory=ROOT/"templates")

@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/")
def landing(request: Request): return templates.TemplateResponse(request,"landing.html")
@app.get("/demo")
def demo(db: Session=Depends(get_db)):
    load_demo_emails(db); return RedirectResponse("/inbox",303)
@app.get("/inbox")
def inbox(request: Request, db: Session=Depends(get_db)):
    return templates.TemplateResponse(request,"inbox.html",{"emails":db.scalars(select(Email).order_by(Email.received_at.desc())).all()})
@app.get("/emails/{email_id}")
def email_detail(email_id:int, request:Request, db:Session=Depends(get_db)):
    email=db.get(Email,email_id)
    if not email: raise HTTPException(404,"Email not found")
    return templates.TemplateResponse(request,"email.html",{"email":email})
@app.post("/api/emails/{email_id}/analyze")
def analyze(email_id:int, db:Session=Depends(get_db)):
    email=db.scalar(select(Email).options(joinedload(Email.analysis),joinedload(Email.task)).where(Email.id==email_id))
    if not email: raise HTTPException(404,"Email not found")
    analysis=analyze_email(db,email)
    return RedirectResponse(f"/tasks/{email.task.id}" if analysis.action_required else f"/emails/{email.id}",303)
@app.post("/api/emails/{email_id}/reanalyze")
def reanalyze(email_id:int, db:Session=Depends(get_db)):
    email=db.scalar(select(Email).options(joinedload(Email.analysis),joinedload(Email.task)).where(Email.id==email_id))
    if not email: raise HTTPException(404,"Email not found")
    analysis=analyze_email(db,email,force=True)
    return RedirectResponse(f"/tasks/{email.task.id}" if analysis.action_required and email.task else f"/emails/{email.id}",303)
@app.get("/dashboard")
def dashboard(request:Request, db:Session=Depends(get_db)):
    tasks=db.scalars(select(Task).options(joinedload(Task.email).joinedload(Email.analysis)).order_by(Task.deadline)).all()
    return templates.TemplateResponse(request,"dashboard.html",{"tasks":tasks})
@app.get("/tasks/{task_id}")
def task_detail(task_id:int, request:Request, db:Session=Depends(get_db)):
    task=db.scalar(select(Task).options(joinedload(Task.email).joinedload(Email.analysis)).where(Task.id==task_id))
    if not task: raise HTTPException(404,"Task not found")
    before,evidence,after=highlighted_parts(task.email)
    return templates.TemplateResponse(request,"task.html",{"task":task,"before":before,"evidence":evidence,"after":after})
