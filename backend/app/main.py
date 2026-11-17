"""HTTP API: auth, projects, human controls, monitoring, live event stream and exports."""
import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime

import markdown as md
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from . import config
from .agents import AGENTS
from .llm import get_provider
from .models import Approval, Deliverable, Event, Message, Project, SessionLocal, Task, Usage, User, init_db
from .orchestrator import DONE, Orchestrator
from .security import create_token, current_user, hash_password, verify_password

orchestrator: Orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    init_db()
    orchestrator = Orchestrator(get_provider())
    orchestrator.recover()
    yield


app = FastAPI(title="ORCHESTRA AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["Content-Disposition"])

ACTIVE = ("planning", "awaiting_plan_approval", "running", "paused", "awaiting_approval")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------------------------------------------------------------------- serializers

def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def project_json(p: Project) -> dict:
    return {"id": p.id, "title": p.title, "objective": p.objective, "status": p.status, "summary": p.summary,
            "questions": p.questions, "assumptions": p.assumptions, "notes": p.notes,
            "budget_usd": p.budget_usd, "error": p.error, "created_at": iso(p.created_at),
            "updated_at": iso(p.updated_at), "completed_at": iso(p.completed_at)}


def task_json(t: Task, by_key: dict[str, Task]) -> dict:
    # "state" is what the workflow graph shows: completed / running / blocked / failed / waiting.
    deps = [by_key[k] for k in t.depends_on if k in by_key]
    if t.status in ("running", "in_review", "needs_revision"):
        state = "running"
    elif t.status in DONE:
        state = "completed"
    elif t.status in ("failed", "cancelled"):
        state = "failed"
    elif t.status == "awaiting_approval" or any(d.status in ("failed", "cancelled", "awaiting_approval") for d in deps):
        state = "blocked"
    else:
        state = "waiting"
    return {"id": t.id, "key": t.key, "title": t.title, "instructions": t.instructions,
            "acceptance_criteria": t.acceptance_criteria, "agent": t.agent, "depends_on": t.depends_on,
            "priority": t.priority, "requires_approval": t.requires_approval, "is_deliverable": t.is_deliverable,
            "status": t.status, "state": state, "output": t.output, "qa_feedback": t.qa_feedback,
            "qa_score": t.qa_score, "error": t.error, "attempts": t.attempts, "history": t.history,
            "started_at": iso(t.started_at), "finished_at": iso(t.finished_at)}


def approval_json(a: Approval) -> dict:
    return {"id": a.id, "project_id": a.project_id, "task_id": a.task_id, "kind": a.kind, "title": a.title,
            "details": a.details, "approve_label": a.approve_label, "reject_label": a.reject_label,
            "status": a.status, "note": a.note, "created_at": iso(a.created_at), "decided_at": iso(a.decided_at)}


def deliverable_json(d: Deliverable, full: bool = True) -> dict:
    out = {"id": d.id, "project_id": d.project_id, "kind": d.kind, "title": d.title, "status": d.status,
           "qa_notes": d.qa_notes, "created_at": iso(d.created_at)}
    if full:
        out["content"] = d.content
    return out


def owned_project(db, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:  # same response either way: no existence leak
        raise HTTPException(404, "Project not found")
    return project


# ---------------------------------------------------------------------------- auth

class Credentials(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=120)


@app.post("/api/auth/register")
def register(body: Credentials):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, "An account with this email already exists")
        user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
        db.add(user)
        db.commit()
        return {"token": create_token(user.id), "user": {"id": user.id, "email": user.email, "name": user.name}}


@app.post("/api/auth/login")
def login(body: Credentials):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return {"token": create_token(user.id), "user": {"id": user.id, "email": user.email, "name": user.name}}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "email": user.email, "name": user.name}


@app.get("/api/meta")
def meta():
    provider = orchestrator.provider
    return {"llm": provider.name, "model": provider.model, "simulated": provider.name == "mock",
            "agents": {k: {"name": v["name"], "role": v["role"]} for k, v in AGENTS.items()},
            "limits": {"max_attempts": config.MAX_ATTEMPTS, "max_concurrency": config.MAX_CONCURRENCY}}


# ---------------------------------------------------------------------------- dashboard

@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user)):
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project).where(Project.user_id == user.id)
                                   .order_by(Project.updated_at.desc())))
        ids = [p.id for p in projects] or [""]
        titles = {p.id: p.title for p in projects}
        approvals = list(db.scalars(select(Approval).where(Approval.project_id.in_(ids),
                                                           Approval.status == "pending")
                                    .order_by(Approval.created_at.desc())))
        deliverables = list(db.scalars(select(Deliverable).where(Deliverable.project_id.in_(ids))
                                       .order_by(Deliverable.created_at.desc()).limit(8)))
        tokens_in, tokens_out, cost = db.execute(
            select(func.coalesce(func.sum(Usage.input_tokens), 0), func.coalesce(func.sum(Usage.output_tokens), 0),
                   func.coalesce(func.sum(Usage.cost_usd), 0.0)).where(Usage.project_id.in_(ids))).one()
        tasks = list(db.execute(select(Task.status, Task.history).where(Task.project_id.in_(ids))))
        progress = {}
        for pid, status, n in db.execute(select(Task.project_id, Task.status, func.count())
                                         .where(Task.project_id.in_(ids)).group_by(Task.project_id, Task.status)):
            done, total = progress.get(pid, (0, 0))
            progress[pid] = (done + (n if status in DONE else 0), total + n)
    finished = [p for p in projects if p.status in ("completed", "failed", "cancelled")]
    reviews = sum(1 for _, h in tasks for a in h if a["outcome"] in ("completed", "qa_rejected"))
    rejections = sum(1 for _, h in tasks for a in h if a["outcome"] == "qa_rejected")
    return {
        "projects": [{**project_json(p), "tasks_done": progress.get(p.id, (0, 0))[0],
                      "tasks_total": progress.get(p.id, (0, 0))[1]} for p in projects],
        "pending_approvals": [{**approval_json(a), "project_title": titles[a.project_id]} for a in approvals],
        "recent_deliverables": [{**deliverable_json(d, full=False), "project_title": titles[d.project_id]}
                                for d in deliverables],
        "stats": {
            "active": sum(p.status in ACTIVE for p in projects),
            "running": sum(p.status == "running" for p in projects),
            "completed": sum(p.status == "completed" for p in projects),
            "tasks_completed": sum(s == "completed" for s, _ in tasks),
            "input_tokens": tokens_in, "output_tokens": tokens_out, "cost_usd": round(cost, 4),
            "workflow_completion_rate": round(sum(p.status == "completed" for p in finished) / len(finished), 3)
            if finished else None,
            "qa_rejection_rate": round(rejections / reviews, 3) if reviews else None,
        },
    }


# ---------------------------------------------------------------------------- projects

class ProjectCreate(BaseModel):
    objective: str = Field(min_length=10, max_length=20000)
    budget_usd: float | None = Field(default=None, gt=0, le=1000)


@app.post("/api/projects")
async def create_project(body: ProjectCreate, user: User = Depends(current_user)):
    objective = body.objective.strip()
    with SessionLocal() as db:
        project = Project(user_id=user.id, objective=objective, title=objective[:60],
                          budget_usd=body.budget_usd or config.DEFAULT_BUDGET_USD)
        db.add(project)
        db.commit()
        out = project_json(project)
    orchestrator.emit(project.id, "project_created", "Project created")
    orchestrator.start_planning(project.id)
    return out


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        project = owned_project(db, project_id, user)
        tasks = list(db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.position)))
        approvals = list(db.scalars(select(Approval).where(Approval.project_id == project_id)
                                    .order_by(Approval.created_at.desc())))
        deliverables = list(db.scalars(select(Deliverable).where(Deliverable.project_id == project_id)
                                       .order_by(Deliverable.kind, Deliverable.created_at)))
        events = list(db.scalars(select(Event).where(Event.project_id == project_id)
                                 .order_by(Event.id.desc()).limit(300)))
        messages = list(db.scalars(select(Message).where(Message.project_id == project_id)
                                   .order_by(Message.created_at.desc()).limit(300)))
        usage = list(db.execute(select(Usage.agent, func.count(), func.sum(Usage.input_tokens),
                                       func.sum(Usage.output_tokens), func.sum(Usage.cost_usd))
                                .where(Usage.project_id == project_id).group_by(Usage.agent)))
    by_key = {t.key: t for t in tasks}
    by_id = {t.id: t for t in tasks}
    usage_by_agent = {a: {"calls": n, "input_tokens": i, "output_tokens": o, "cost_usd": round(c, 4)}
                      for a, n, i, o, c in usage}
    agents = []
    for key, spec in AGENTS.items():
        mine = [t for t in tasks if t.agent == key]
        current = [t for t in tasks if (t.agent == key and t.status == "running")
                   or (key == "qa" and t.status == "in_review")]
        errors = [{"task": t.key, **h} for t in mine for h in t.history if h["outcome"] != "completed"]
        agents.append({
            "key": key, "name": spec["name"], "role": spec["role"],
            "status": "working" if current else "idle",
            "current_tasks": [{"id": t.id, "key": t.key, "title": t.title} for t in current],
            "assigned": len(mine), "completed": sum(t.status == "completed" for t in mine),
            "retries": sum(max(0, t.attempts - 1) for t in mine), "errors": errors,
            "usage": usage_by_agent.get(key, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0}),
        })
    total_cost = round(sum(u["cost_usd"] for u in usage_by_agent.values()), 4)
    return {
        "project": project_json(project),
        "tasks": [task_json(t, by_key) for t in tasks],
        "agents": agents,
        "approvals": [approval_json(a) for a in approvals],
        "deliverables": [deliverable_json(d) for d in deliverables],
        "events": [{"id": e.id, "kind": e.kind, "agent": e.agent, "message": e.message,
                    "task_key": by_id[e.task_id].key if e.task_id in by_id else None,
                    "created_at": iso(e.created_at)} for e in events],
        "messages": [{"id": m.id, "sender": m.sender, "recipient": m.recipient, "type": m.type,
                      "content": m.content, "output_ref": m.output_ref, "status": m.status,
                      "task_key": by_id[m.task_id].key if m.task_id in by_id else None,
                      "created_at": iso(m.created_at)} for m in messages],
        "usage": {"cost_usd": total_cost, "budget_usd": project.budget_usd,
                  "input_tokens": sum(u["input_tokens"] for u in usage_by_agent.values()),
                  "output_tokens": sum(u["output_tokens"] for u in usage_by_agent.values())},
    }


class Action(BaseModel):
    action: str


@app.post("/api/projects/{project_id}/control")
async def control(project_id: str, body: Action, user: User = Depends(current_user)):
    with SessionLocal() as db:
        status = owned_project(db, project_id, user).status
    if body.action == "pause" and status == "running":
        orchestrator.pause(project_id)
    elif body.action == "resume" and status == "paused":
        orchestrator.resume(project_id)
    elif body.action == "cancel" and status in ACTIVE:
        orchestrator.cancel(project_id)
    elif body.action == "replan" and status in ("awaiting_plan_approval", "failed"):
        orchestrator.start_planning(project_id)
    else:
        raise HTTPException(409, f"Cannot {body.action} a project that is {status.replace('_', ' ')}")
    return {"ok": True}


class Note(BaseModel):
    note: str = Field(min_length=1, max_length=5000)


@app.post("/api/projects/{project_id}/notes")
def add_note(project_id: str, body: Note, user: User = Depends(current_user)):
    """Add shared project context (for example, answers to clarifying questions)."""
    with SessionLocal() as db:
        project = owned_project(db, project_id, user)
        project.notes = [*project.notes, body.note.strip()]
        db.commit()
    orchestrator.emit(project_id, "context_added", "You added project context")
    return {"ok": True}


class TaskEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    instructions: str | None = Field(default=None, max_length=20000)
    agent: str | None = None


@app.patch("/api/tasks/{task_id}")
def edit_task(task_id: str, body: TaskEdit, user: User = Depends(current_user)):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        owned_project(db, task.project_id, user)
        if task.status in ("running", "in_review"):
            raise HTTPException(409, "This task is running; pause the workflow or wait for it to finish")
        if body.agent is not None:
            if body.agent not in AGENTS:
                raise HTTPException(422, "Unknown agent")
            task.agent = body.agent
        if body.title is not None:
            task.title = body.title
        if body.instructions is not None:
            task.instructions = body.instructions
        db.commit()
        key, pid = task.key, task.project_id
    orchestrator.emit(pid, "task_edited", f"[{key}] edited by you", task_id=task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        project = owned_project(db, task.project_id, user)
        if project.status not in ("running", "paused", "awaiting_approval", "failed", "completed"):
            raise HTTPException(409, f"Cannot re-run tasks while the project is {project.status.replace('_', ' ')}")
        if task.status in ("running", "in_review"):
            raise HTTPException(409, "This task is already running")
    orchestrator.retry_task(project.id, task_id)
    return {"ok": True}


class Decision(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=5000)


@app.post("/api/approvals/{approval_id}/decide")
async def decide(approval_id: str, body: Decision, user: User = Depends(current_user)):
    try:
        orchestrator.decide(approval_id, body.approve, body.note.strip(), user.id)
    except LookupError:
        raise HTTPException(404, "Approval not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


# ---------------------------------------------------------------------------- live stream

@app.get("/api/projects/{project_id}/stream")
async def stream(project_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        owned_project(db, project_id, user)
    queue = orchestrator.bus.subscribe(project_id)

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            orchestrator.bus.unsubscribe(project_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------- export

_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px/1.6 Georgia,serif;max-width:46rem;margin:3rem auto;padding:0 1.5rem;color:#1c1917}}
h1,h2,h3{{font-family:system-ui,sans-serif;line-height:1.25}}table{{border-collapse:collapse}}
td,th{{border:1px solid #d6d3d1;padding:.35rem .6rem}}blockquote{{border-left:3px solid #d6d3d1;margin-left:0;
padding-left:1rem;color:#57534e}}code{{background:#f5f5f4;padding:.1rem .3rem}}</style></head>
<body>{body}</body></html>"""


@app.get("/api/deliverables/{deliverable_id}/export")
def export(deliverable_id: str, format: str = "md", user: User = Depends(current_user)):
    with SessionLocal() as db:
        d = db.get(Deliverable, deliverable_id)
        if d is None:
            raise HTTPException(404, "Deliverable not found")
        owned_project(db, d.project_id, user)
    slug = re.sub(r"[^a-z0-9]+", "-", d.title.lower()).strip("-")[:60] or "deliverable"
    if format == "html":
        body = md.markdown(d.content, extensions=["tables", "fenced_code"])
        content, media, ext = _HTML.format(title=d.title.replace("<", "&lt;"), body=body), "text/html", "html"
    elif format == "md":
        content, media, ext = d.content, "text/markdown", "md"
    else:
        raise HTTPException(422, "format must be md or html")
    return Response(content, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.{ext}"'})
