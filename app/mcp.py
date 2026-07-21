import json
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .auth import DEMO_USER_ID
from .demo_data import DEMO_EMAILS
from .execution import build_execution_package, parse_structured_result
from .models import Email, Task


PROTOCOL_VERSION = "2025-03-26"


DEMO_EXTERNAL_IDS = {item["external_id"] for item in DEMO_EMAILS}


def _tool(name: str, description: str, schema: dict, *, public_demo: bool) -> dict:
    annotations = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
    security = [{"type": "noauth"}] if public_demo else [{"type": "http", "scheme": "bearer"}]
    return {"name": name, "description": description, "inputSchema": schema, "annotations": annotations,
            "securitySchemes": security, "_meta": {"securitySchemes": security}}


def _tools(public_demo: bool) -> list[dict]:
    scope = "synthetic demo" if public_demo else "authenticated user's"
    return [
        _tool("list_actioninbox_tasks", f"List {scope} evidence-backed ActionInbox tasks. Returns no full email bodies.",
              {"type": "object", "properties": {}, "additionalProperties": False}, public_demo=public_demo),
        _tool("get_actioninbox_task", f"Get one {scope} task with its summary and exact supporting evidence, without the full email body.",
              {"type": "object", "properties": {"task_id": {"type": "integer", "minimum": 1}}, "required": ["task_id"], "additionalProperties": False}, public_demo=public_demo),
        _tool("prepare_task_execution", f"Prepare a review-only package for one {scope} task. This never executes, sends, or modifies anything.",
              {"type": "object", "properties": {"task_id": {"type": "integer", "minimum": 1}, "target": {"type": "string", "enum": ["CHATGPT_WORK", "CODEX"]}}, "required": ["task_id"], "additionalProperties": False}, public_demo=public_demo),
    ]


def _response(request_id, result=None, error=None, status=200):
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return JSONResponse(payload, status_code=status)


def _task_scope(db: Session, user_id: str, public_demo: bool):
    query = select(Task).join(Task.email).options(joinedload(Task.email).joinedload(Email.analysis)).where(Task.user_id == user_id)
    if public_demo:
        query = query.where(Email.source == "demo", Email.external_id.in_(DEMO_EXTERNAL_IDS))
    return query


def _owned_task(db: Session, task_id: int, user_id: str, public_demo: bool) -> Task | None:
    return db.scalar(_task_scope(db, user_id, public_demo).where(Task.id == task_id))


def _task_view(task: Task) -> dict:
    result = parse_structured_result(task.email.analysis.structured_result)
    return {"id": task.id, "title": task.title, "deadline": task.deadline_text, "summary": task.email.analysis.summary,
            "classification": task.email.analysis.classification,
            "evidence": [{"type": fact.type, "value": fact.value, "exact_quote": fact.evidence.exact_quote} for fact in result.email_facts],
            "review_required": True}


async def handle_mcp(request: Request, db: Session):
    configured = os.getenv("MCP_ACCESS_TOKEN", "")
    supplied = request.headers.get("authorization", "")
    authenticated = False
    if supplied:
        if not configured or not supplied.startswith("Bearer ") or not secrets.compare_digest(supplied[7:], configured):
            return JSONResponse({"error": "MCP authentication required"}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="ActionInbox MCP"'})
        authenticated = True
    public_demo = not authenticated
    try:
        body = await request.json()
    except ValueError:
        return _response(None, error={"code": -32700, "message": "Parse error"}, status=400)
    method, request_id = body.get("method"), body.get("id")
    if method == "initialize":
        requested = body.get("params", {}).get("protocolVersion")
        return _response(request_id, {"protocolVersion": requested or PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "ActionInbox", "version": "1.0.0"}})
    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "tools/list":
        return _response(request_id, {"tools": _tools(public_demo)})
    if method != "tools/call":
        return _response(request_id, error={"code": -32601, "message": "Method not found"})
    params = body.get("params", {}); name = params.get("name"); arguments = params.get("arguments") or {}
    user_id = DEMO_USER_ID if public_demo else os.getenv("MCP_USER_ID", DEMO_USER_ID)
    if name == "list_actioninbox_tasks":
        tasks = db.scalars(_task_scope(db, user_id, public_demo).order_by(Task.deadline)).all()
        data = [{"id": task.id, "title": task.title, "deadline": task.deadline_text, "summary": task.email.analysis.summary} for task in tasks]
    elif name in {"get_actioninbox_task", "prepare_task_execution"}:
        try:
            task_id = int(arguments["task_id"])
        except (KeyError, TypeError, ValueError):
            return _response(request_id, error={"code": -32602, "message": "A valid task_id is required"})
        task = _owned_task(db, task_id, user_id, public_demo)
        if not task:
            return _response(request_id, error={"code": -32602, "message": "Task not found"})
        if name == "get_actioninbox_task":
            data = _task_view(task)
        else:
            target = arguments.get("target", "CHATGPT_WORK")
            if target not in {"CHATGPT_WORK", "CODEX"}:
                return _response(request_id, error={"code": -32602, "message": "Unsupported target"})
            data = build_execution_package(task, parse_structured_result(task.email.analysis.structured_result), target)
            data["review_required"] = True
            data["executed"] = False
    else:
        return _response(request_id, error={"code": -32602, "message": "Unknown tool"})
    return _response(request_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "structuredContent": data, "isError": False})
