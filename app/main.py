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
from .database import get_db, initialize_database
from .demo_data import load_demo_emails
from .models import BusinessResource, Email, Task
from .resources import MAX_RESOURCE_CHARS, RESOURCE_TYPES, seed_demo_resources

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
    load_demo_emails(db); seed_demo_resources(db); return RedirectResponse("/inbox",303)
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
    guidance_views=[]
    structured=json.loads(task.email.analysis.structured_result or "{}")
    for guidance in structured.get("resource_guidance",[]):
        try: resource_id=int(guidance["resource_id"].removeprefix("resource-"))
        except (KeyError,ValueError): continue
        resource=db.get(BusinessResource,resource_id); ev=guidance.get("resource_evidence",{})
        if not resource or resource.title!=guidance.get("resource_title"): continue
        start=ev.get("start_offset"); end=ev.get("end_offset"); quote=ev.get("exact_quote")
        if not isinstance(start,int) or not isinstance(end,int) or resource.content[start:end]!=quote: continue
        guidance_views.append({"guidance":guidance,"resource":resource,"before":resource.content[:start],"quote":quote,"after":resource.content[end:]})
    return templates.TemplateResponse(request,"task.html",{"task":task,"before":before,"evidence":evidence,"after":after,"guidance_views":guidance_views})

@app.get("/resources")
def resources_page(request:Request,db:Session=Depends(get_db)):
    items=db.scalars(select(BusinessResource).order_by(BusinessResource.updated_at.desc())).all()
    return templates.TemplateResponse(request,"resources.html",{"resources":items})
@app.get("/resources/new")
def resource_new(request:Request): return templates.TemplateResponse(request,"resource_form.html",{"resource":None,"resource_types":RESOURCE_TYPES})
@app.post("/resources")
def resource_create(title:str=Form(...),resource_type:str=Form(...),content:str=Form(...),organization_team:str=Form(""),enabled:bool=Form(False),db:Session=Depends(get_db)):
    title=title.strip(); content=content.strip()
    if not title or not content or len(content)>MAX_RESOURCE_CHARS or resource_type not in RESOURCE_TYPES: raise HTTPException(422,"Invalid business resource")
    item=BusinessResource(title=title,resource_type=resource_type,content=content,organization_team=organization_team.strip() or None,enabled=enabled); db.add(item); db.commit()
    return RedirectResponse("/resources",303)
@app.get("/resources/{resource_id}")
def resource_view(resource_id:int,request:Request,db:Session=Depends(get_db)):
    item=db.get(BusinessResource,resource_id)
    if not item: raise HTTPException(404,"Resource not found")
    return templates.TemplateResponse(request,"resource_view.html",{"resource":item})
@app.get("/resources/{resource_id}/edit")
def resource_edit(resource_id:int,request:Request,db:Session=Depends(get_db)):
    item=db.get(BusinessResource,resource_id)
    if not item: raise HTTPException(404,"Resource not found")
    return templates.TemplateResponse(request,"resource_form.html",{"resource":item,"resource_types":RESOURCE_TYPES})
@app.post("/resources/{resource_id}")
def resource_update(resource_id:int,title:str=Form(...),resource_type:str=Form(...),content:str=Form(...),organization_team:str=Form(""),enabled:bool=Form(False),db:Session=Depends(get_db)):
    item=db.get(BusinessResource,resource_id)
    if not item: raise HTTPException(404,"Resource not found")
    title=title.strip(); content=content.strip()
    if not title or not content or len(content)>MAX_RESOURCE_CHARS or resource_type not in RESOURCE_TYPES: raise HTTPException(422,"Invalid business resource")
    item.title=title; item.resource_type=resource_type; item.content=content; item.organization_team=organization_team.strip() or None; item.enabled=enabled; db.commit()
    return RedirectResponse(f"/resources/{item.id}",303)
@app.post("/resources/{resource_id}/toggle")
def resource_toggle(resource_id:int,db:Session=Depends(get_db)):
    item=db.get(BusinessResource,resource_id)
    if not item: raise HTTPException(404,"Resource not found")
    item.enabled=not item.enabled; db.commit(); return RedirectResponse("/resources",303)
@app.post("/resources/{resource_id}/delete")
def resource_delete(resource_id:int,db:Session=Depends(get_db)):
    item=db.get(BusinessResource,resource_id)
    if not item: raise HTTPException(404,"Resource not found")
    db.delete(item); db.commit(); return RedirectResponse("/resources",303)
