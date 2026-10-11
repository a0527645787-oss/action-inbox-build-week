"""Table metadata setup using the existing Google connection."""
import json
import re
from pathlib import Path
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .auth import require_personal_user
from .action_csrf import csrf_token, require_action_csrf
from .database import get_db
from .models import TableDestination
from .table_actions import FIELD_LABELS, TableError, inspect_headers, validate_headers, validate_mapping

router = APIRouter(dependencies=[Depends(require_action_csrf)])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["csrf_token"] = csrf_token


def page(request, user, **context):
    return templates.TemplateResponse(request, "tables.html", {"current_user": user, "fields": FIELD_LABELS, **context})


def owned(db, user, destination_id):
    dest = db.scalar(select(TableDestination).where(TableDestination.id == destination_id, TableDestination.user_id == user.id))
    if dest is None:
        raise HTTPException(404, "Table not found")
    return dest


@router.get("/settings/tables")
def tables(request: Request, db=Depends(get_db), user=Depends(require_personal_user)):
    return page(request, user, destinations=db.scalars(select(TableDestination).where(TableDestination.user_id == user.id).order_by(TableDestination.id)).all())


@router.post("/settings/tables/inspect")
def inspect_table(request: Request, name: str = Form(...), sheet_url: str = Form(...), tab: str = Form(...), db=Depends(get_db), user=Depends(require_personal_user)):
    match = re.fullmatch(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{10,160})(?:/[^\s]*)?", sheet_url.strip())
    if not match or not name.strip() or len(name.strip()) > 100 or not tab.strip() or len(tab) > 100:
        return page(request, user, error="Enter a name, a Google Sheets link, and the exact tab name.")
    target = match.group(1)
    try:
        headers = inspect_headers(target, tab)
    except TableError as exc:
        return page(request, user, error=str(exc))
    dest = TableDestination(user_id=user.id, display_name=name.strip(), target=target, tab_name=tab,
        enabled=False, schema_snapshot=json.dumps(headers), column_mapping=json.dumps(["blank"] * len(headers)))
    db.add(dest)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return page(request, user, error="This table is already configured. Open its mapping from your tables.")
    return RedirectResponse(f"/settings/tables/{dest.id}", 303)


@router.get("/settings/tables/{destination_id}")
def mapping_page(destination_id: int, request: Request, db=Depends(get_db), user=Depends(require_personal_user)):
    dest = owned(db, user, destination_id)
    try:
        headers = inspect_headers(dest.target, dest.tab_name)
    except TableError as exc:
        return page(request, user, error=str(exc))
    old = dict(zip(json.loads(dest.schema_snapshot), json.loads(dest.column_mapping)))
    return page(request, user, destination=dest, headers=headers, mapping=[old.get(h, "blank") for h in headers], inspected_schema=json.dumps(headers))


@router.post("/settings/tables/{destination_id}/save")
def save_table(destination_id: int, request: Request, inspected_schema: str = Form(...), field: list[str] = Form(...), enabled: bool = Form(False), db=Depends(get_db), user=Depends(require_personal_user)):
    dest = owned(db, user, destination_id)
    try:
        headers = inspect_headers(dest.target, dest.tab_name)
        validate_headers(headers, json.loads(inspected_schema))
        validate_mapping(headers, field)
    except (TableError, json.JSONDecodeError) as exc:
        return page(request, user, error=str(exc) if isinstance(exc, TableError) else "Inspect the table again before saving.")
    dest.schema_snapshot, dest.column_mapping, dest.enabled = json.dumps(headers), json.dumps(field), enabled
    db.commit()
    return RedirectResponse("/settings/tables", 303)


@router.post("/settings/tables/{destination_id}/disable")
def disable_table(destination_id: int, db=Depends(get_db), user=Depends(require_personal_user)):
    owned(db, user, destination_id).enabled = False
    db.commit()
    return RedirectResponse("/settings/tables", 303)
