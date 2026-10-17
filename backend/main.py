import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

import deadlines
import github_repo
import insights
import pdf_report
import llm_engine
import proof
from auth import (auth_required, current_auth, hash_password, make_token, new_invite_code, normalize_invite,
                  require_lead, verify_password)
from database import engine, get_session, init_db
from models import TASK_STATUSES, AgentLog, Participant, Project, Task, TaskRequest, User, utcnow
from notifier import save_slack_settings, send_slack, send_telegram, slack_settings
from seed import (DEFAULT_INVITE_CODE, ENTERPRISE_INVITE_CODE, ENTERPRISE_PROJECT_NAME, seed_default_project,
                  seed_enterprise_project)
from telegram_service import status_board_html, telegram_service

log = logging.getLogger("coordinator")
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    init_db()
    with Session(engine) as session:
        if not session.exec(select(Project)).first():
            seed_default_project(session)
        # Rows created before invites/workspaces existed
        for p in session.exec(select(Project).where(Project.invite_code == None)).all():  # noqa: E711
            preset = {1: DEFAULT_INVITE_CODE}.get(p.id) or (ENTERPRISE_INVITE_CODE if p.name == ENTERPRISE_PROJECT_NAME else None)
            taken = preset and session.exec(select(Project).where(Project.invite_code == preset)).first()
            p.invite_code = preset if preset and not taken else new_invite_code(session, p.name)
            if p.name == ENTERPRISE_PROJECT_NAME:
                p.workspace_type = "company"
            session.add(p)
            session.flush()
        session.commit()
    await telegram_service.start(handle_text=handle_telegram_text, emit=emit_state_update, handle_file=handle_telegram_file,
                                 handle_request=handle_telegram_request)
    llm_engine.warm_up_local_llm()  # load the Ollama model in the background (no-op if not configured)
    if os.getenv("AUTOPILOT_ON_START", "").strip().lower() in ("1", "true", "yes"):
        start_autopilot(AUTOPILOT["interval_seconds"])
    yield
    stop_autopilot()
    await telegram_service.stop()


app = FastAPI(title="AI Group Project Coordinator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Real-time WebSocket broadcaster ----------

class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        async def send(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2)
            except Exception:
                self.disconnect(ws)
        if self.active:
            await asyncio.gather(*(send(ws) for ws in list(self.active)))


manager = ConnectionManager()


def emit_state_update(reason: str = "STATE_CHANGED") -> None:
    """Thread-safe: callable from async code, sync endpoints (threadpool) or background threads."""
    message = {"event": "STATE_UPDATED", "timestamp": datetime.now(timezone.utc).isoformat(), "reason": reason}
    try:
        asyncio.get_running_loop().create_task(manager.broadcast(message))
    except RuntimeError:  # not in the event loop thread
        if MAIN_LOOP and MAIN_LOOP.is_running():
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), MAIN_LOOP)


@app.middleware("http")
async def broadcast_on_mutation(request: Request, call_next):
    """Any successful write to /api/* (dashboard, Android app, webhooks) pushes STATE_UPDATED to every client."""
    response = await call_next(request)
    if (request.method in ("POST", "PATCH", "PUT", "DELETE") and request.url.path.startswith("/api/")
            and response.status_code < 400):
        await manager.broadcast({"event": "STATE_UPDATED", "timestamp": datetime.now(timezone.utc).isoformat(),
                                 "reason": f"{request.method} {request.url.path}"})
    return response


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_json({"event": "CONNECTED", "timestamp": datetime.now(timezone.utc).isoformat(),
                            "clients": len(manager.active)})
        while True:
            if (await ws.receive_text()).strip().lower() == "ping":
                await ws.send_json({"event": "PONG", "timestamp": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)


# ---------- Request bodies ----------

class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    workspace_type: Literal["college", "company"] = "college"
    github_repo_url: Optional[str] = None
    # Optional project timeline (local ISO datetimes): task deadlines are spread between start and due
    start_at: Optional[str] = None
    due_at: Optional[str] = None


class ParticipantCreate(BaseModel):
    name: str
    role: str = ""
    telegram_username: Optional[str] = None


class TaskRequestCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = ""
    assignee_id: Optional[int] = None  # who should do it (default: the requester)
    relation: Literal["before", "after", "none"] = "none"  # relative to related_task_id (one of the requester's tasks)
    related_task_id: Optional[int] = None
    deadline: str = ""
    requester_id: Optional[int] = None  # only for the Team Lead / scripts; a member always requests as themselves


class TaskRequestDecision(BaseModel):
    action: Literal["approve", "reject"]
    note: str = ""
    assignee_id: Optional[int] = None  # the lead may reassign before approving
    deadline: Optional[str] = None


class TaskCreate(BaseModel):
    title: str
    description: str = ""
    owner_id: Optional[int] = None
    depends_on_task_id: Optional[int] = None
    deadline: str = ""
    task_type: Optional[Literal["github_code", "design_docs", "general"]] = None  # classified from the title if empty


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    owner_id: Optional[int] = None
    status: Optional[str] = None
    progress_note: Optional[str] = None
    blocker_reason: Optional[str] = None
    blocked_by_participant_id: Optional[int] = None
    depends_on_task_id: Optional[int] = None
    deadline: Optional[str] = None


class SimulateReply(BaseModel):
    message: str
    participant_id: Optional[int] = None
    participant_name: Optional[str] = None
    telegram_username: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    task_id: Optional[int] = None
    source: str = "simulator"  # simulator | telegram | voice


class MobileQuickUpdate(BaseModel):
    participant_id: int
    task_id: Optional[int] = None
    action_type: Literal["VOICE_NOTE", "SOS_BLOCKER", "QUICK_STATUS"] = "QUICK_STATUS"
    text: str = ""
    availability: Optional[str] = None


class GenerateProject(BaseModel):
    project_name: str
    description: str = ""
    teammates: list[str] = Field(min_length=1)
    workspace_type: Literal["college", "company"] = "college"
    # Optional project timeline (local ISO datetimes): task deadlines are spread between start and due
    start_at: Optional[str] = None
    due_at: Optional[str] = None


class AutopilotToggle(BaseModel):
    enabled: bool
    cadence: Optional[Literal["live", "hourly", "6h", "daily10"]] = None  # overrides interval_seconds when given
    interval_seconds: int = Field(45, ge=10, le=86400)
    blocker_sla_seconds: Optional[int] = Field(None, ge=10)
    followup_cooldown_seconds: Optional[int] = Field(None, ge=10)


class RegisterLead(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=160)
    password: str = Field(min_length=6, max_length=200)
    org_type: Literal["college", "corporate"] = "college"
    # Preferred spelling; overrides org_type when given
    account_type: Optional[Literal["college_lead", "company_lead"]] = None


class LoginLead(BaseModel):
    email: str
    password: str


class JoinMember(BaseModel):
    invite_code: str  # code or invite link
    auth_pin: str


class TaskReview(BaseModel):
    action: Literal["approve", "reopen"]
    reason: str = ""


class Deliverable(BaseModel):
    url: str = Field(min_length=4, max_length=500)
    note: Optional[str] = None


class PresetLoad(BaseModel):
    mode: Literal["college", "enterprise"]


class CsvImport(BaseModel):
    csv_text: str
    project_name: Optional[str] = None  # set -> import into a NEW project; unset -> active project


class GithubCommit(BaseModel):
    author: str
    commit_message: str
    repo: str = ""
    task_id: Optional[int] = None  # which task this commit updates (default: the author's most urgent task)


class LoginBody(BaseModel):
    name: str = ""
    email: Optional[str] = None
    role: Literal["lead", "member"] = "member"
    invite_code: Optional[str] = None  # code or full invite link; members join that project
    member_role: str = ""  # job role when joining a project as a new member


class DemoLogin(BaseModel):
    role: Literal["lead", "member"] = "lead"
    account_type: Literal["college_lead", "company_lead"] = "college_lead"


class TeamMember(BaseModel):
    name: str
    role: str = ""


class FromGithub(BaseModel):
    project_name: str = ""
    description: str = ""
    github_repo_url: str
    workspace_type: Literal["college", "company"] = "college"
    team_members: list[TeamMember | str] = Field(min_length=1)
    # Optional project timeline (local ISO datetimes): task deadlines are spread between start and due
    start_at: Optional[str] = None
    due_at: Optional[str] = None


class TaskOverride(BaseModel):
    status: Literal["Pending", "In Progress", "Blocked", "Complete"]
    note: Optional[str] = None


class ProjectDeadlines(BaseModel):
    due_at: str  # the project's final deadline
    start_at: Optional[str] = None  # default: now
    distribute: bool = True  # spread deadlines over every unfinished task
    notify: bool = False  # tell each owner their new deadlines on Telegram


class TaskDeadline(BaseModel):
    deadline: str = ""  # ISO datetime, a label like "Tomorrow 6 PM", or "" to clear


class ProjectSettings(BaseModel):
    strict_dependencies: Optional[bool] = None
    github_repo_url: Optional[str] = None
    workspace_type: Optional[Literal["college", "company"]] = None
    description: Optional[str] = None


class IntegrationSettings(BaseModel):
    slack_enabled: bool
    slack_webhook_url: Optional[str] = None


class TakeUpdates(BaseModel):
    project_id: Optional[int] = None


class CoordinateSlots(BaseModel):
    availability: dict[str, str] = {}  # participant id or name -> "Free at 5 PM"
    notify: bool = False  # send the recommended slot to attendees on Telegram


# ---------- Helpers ----------

def get_active_project(session: Session) -> Project:
    project = session.exec(select(Project).where(Project.is_active == True)).first()  # noqa: E712
    if not project:
        project = session.exec(select(Project).order_by(Project.id)).first()
        if not project:
            raise HTTPException(404, "No projects exist")
        project.is_active = True
        session.commit()
        session.refresh(project)
    return project


def set_active(session: Session, project_id: int) -> None:
    for p in session.exec(select(Project)).all():
        p.is_active = p.id == project_id


# ---------- Workspace access: Corporate is for Company Team Leads only ----------

CORPORATE_DENIED = "Corporate Workspace requires a Company Team Lead account"


def account_type_of(user: Optional[User]) -> Optional[str]:
    """'company_lead' | 'college_lead' for Team Leads, None for members."""
    if not user or user.role != "lead":
        return None
    return "company_lead" if user.org_type == "corporate" else "college_lead"


def caller_account_type(session: Session, auth: Optional[dict]) -> Optional[str]:
    if not auth or auth.get("role") != "lead":
        return None
    return account_type_of(session.get(User, auth.get("uid")) if auth.get("uid") else None)


COLLEGE_DENIED = "College Workspace is for College Team Leads. Company accounts work in the Corporate Workspace"


def lead_workspace(account_type: Optional[str]) -> Optional[str]:
    """The one workspace a Team Lead works in: Company leads -> Corporate, College leads -> College."""
    return {"company_lead": "company", "college_lead": "college"}.get(account_type or "")


def ensure_workspace_allowed(session: Session, auth: Optional[dict], workspace_type: Optional[str]) -> None:
    """Each Team Lead is locked to their own workspace: College leads to College, Company leads to Corporate."""
    if not workspace_type:
        return
    allowed = lead_workspace(caller_account_type(session, auth))
    wanted = "company" if workspace_type in ("company", "enterprise") else "college"
    if allowed and wanted != allowed:
        raise HTTPException(403, CORPORATE_DENIED if allowed == "college" else COLLEGE_DENIED)


DEMO_INVITE_CODES = (DEFAULT_INVITE_CODE, ENTERPRISE_INVITE_CODE)


def is_demo_user(user: Optional[User]) -> bool:
    return bool(user and (user.email or "").endswith("@coordina.demo"))


def visible_projects(session: Session, auth: Optional[dict]) -> list[Project]:
    """Projects this caller may see.

    Team Lead: the projects they own (demo accounts also see the seeded demo projects), minus Corporate
    ones for a College lead. Member: the project they joined. Anonymous (scripts, tests): everything."""
    projects = session.exec(select(Project).order_by(Project.id)).all()
    if not auth:
        return projects
    if auth.get("role") != "lead":
        return [pr for pr in projects if pr.id == auth.get("prj")]
    user = session.get(User, auth.get("uid")) if auth.get("uid") else None
    if not user:
        return []
    demo = is_demo_user(user)
    mine = [pr for pr in projects if pr.owner_user_id == user.id or (demo and pr.invite_code in DEMO_INVITE_CODES)]
    workspace = lead_workspace(account_type_of(user))
    return [pr for pr in mine if (pr.workspace_type or "college") == workspace]


def current_project(session: Session, auth: Optional[dict], required: bool = True) -> Optional[Project]:
    """The project this caller is working in. Each Team Lead has their own; members have theirs; scripts use the global one."""
    if not auth:
        return get_active_project(session)
    visible = visible_projects(session, auth)
    project = None
    if auth.get("role") == "lead":
        user = session.get(User, auth.get("uid"))
        project = next((pr for pr in visible if user and pr.id == user.current_project_id), None)
        project = project or (max(visible, key=lambda pr: pr.id) if visible else None)
    else:
        project = visible[0] if visible else None
    if not project and required:
        raise HTTPException(404, "You don't have a project yet. Create one from a GitHub repo to get started.")
    return project


def open_project(session: Session, auth: Optional[dict], project: Project) -> None:
    """Make `project` the caller's current project (and the global one that Auto-Pilot and the bot follow)."""
    if auth and auth.get("role") == "lead" and auth.get("uid"):
        user = session.get(User, auth["uid"])
        if user:
            user.current_project_id = project.id
            session.add(user)
    set_active(session, project.id)


def require_visible(session: Session, auth: Optional[dict], project_id: int) -> Project:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if auth and project.id not in {pr.id for pr in visible_projects(session, auth)}:
        ensure_workspace_allowed(session, auth, project.workspace_type)
        raise HTTPException(403, "That project belongs to another Team Lead")
    return project


def validate_participant(session: Session, project_id: int, participant_id: Optional[int]) -> None:
    if participant_id is None:
        return
    participant = session.get(Participant, participant_id)
    if not participant or participant.project_id != project_id:
        raise HTTPException(400, f"Participant {participant_id} is not in this project")


def validate_dependency(session: Session, project_id: int, dep_id: Optional[int], task_id: Optional[int] = None) -> None:
    if dep_id is None:
        return
    if dep_id == task_id:
        raise HTTPException(400, "A task cannot depend on itself")
    dep = session.get(Task, dep_id)
    if not dep or dep.project_id != project_id:
        raise HTTPException(400, f"Task {dep_id} is not in this project")


def project_participants(session: Session, project_id: int) -> list[Participant]:
    return session.exec(select(Participant).where(Participant.project_id == project_id).order_by(Participant.id)).all()


def project_tasks(session: Session, project_id: int) -> list[Task]:
    """The project's tasks in work order: a task always comes right before the work that waits for it,
    and everything else keeps the order it was created in (so #1, #2... read like the plan)."""
    return insights.work_order(session.exec(select(Task).where(Task.project_id == project_id).order_by(Task.id)).all())



def task_dicts(tasks: list[Task], participants: list[Participant]) -> list[dict]:
    """Tasks with owner names, dependency titles and the computed is_at_risk flag."""
    names = {p.id: p.name for p in participants}
    by_id = {t.id: t for t in tasks}
    out = []
    for t in tasks:
        dep = by_id.get(t.depends_on_task_id) if t.depends_on_task_id else None
        out.append({
            **t.model_dump(),
            "owner_name": names.get(t.owner_id),
            "blocked_by_name": names.get(t.blocked_by_participant_id),
            "depends_on_title": dep.title if dep else None,
            "is_at_risk": bool(dep and dep.status == "Blocked"),
            "proof": proof.proof_level(t),
        })
    return out


def add_log(session: Session, project_id: int, log_type: str, summary: str, details: Optional[dict] = None) -> None:
    session.add(AgentLog(project_id=project_id, log_type=log_type, summary=summary,
                         details_json=json.dumps(details or {}, default=str, ensure_ascii=False)))


def pick_task_for(session: Session, sender: Participant, task_id: Optional[int]) -> Optional[Task]:
    if task_id is not None:
        task = session.get(Task, task_id)
        if not task or task.project_id != sender.project_id:
            raise HTTPException(400, f"Task {task_id} is not in {sender.name}'s project")
        return task
    own = session.exec(select(Task).where(Task.owner_id == sender.id).order_by(Task.id)).all()
    priority = {"Blocked": 0, "In Progress": 1, "Pending": 2, "Complete": 3}
    return min(own, key=lambda t: (priority.get(t.status, 9), t.id)) if own else None


def notify(session: Session, project_id: int, participant: Optional[Participant], text: str,
           summary: str, notifications: list, extra: Optional[dict] = None, log_type: str = "AUTO_RELAY") -> None:
    n = send_telegram(participant.name if participant else "unknown",
                      participant.telegram_chat_id if participant else None, text)
    slack = send_slack(f"*{summary}*\n{text}")
    if slack:
        n["slack"] = slack
    notifications.append(n)
    add_log(session, project_id, log_type, summary, {**(extra or {}), "notification": n})


def notify_handoffs(session: Session, task: Task, by_name: str, notifications: list, actions: list,
                    skip_task_id: Optional[int] = None) -> None:
    """A finished task tells everyone waiting on it; tasks blocked on it are released."""
    for t in project_tasks(session, task.project_id):
        if t.depends_on_task_id != task.id or t.status == "Complete" or t.id == skip_task_id:
            continue
        owner = session.get(Participant, t.owner_id) if t.owner_id else None
        if t.status == "Blocked" and t.blocked_by_participant_id in (None, task.owner_id):
            t.status, t.blocker_reason, t.blocked_by_participant_id = "In Progress", None, None
            t.updated_at = utcnow()
            actions.append({"type": "AUTO_UNBLOCKED", "task_id": t.id, "to": "In Progress"})
        same_person = owner is not None and owner.id == task.owner_id
        text = (f"Next up for you: \"{t.title}\" is ready to start now that \"{task.title}\" is done."
                if same_person else
                f"Heads up {owner.name if owner else ''}: {by_name} completed \"{task.title}\" - "
                f"you can now go full speed on \"{t.title}\".")
        notify(session, task.project_id, owner, text,
               f"{'Next task' if same_person else 'Handoff'}: \"{task.title}\" -> \"{t.title}\"", notifications, {"task_id": t.id})
        actions.append({"type": "HANDOFF_NOTIFIED", "task_id": t.id})


def process_reply(session: Session, sender: Participant, task: Optional[Task], message: str, source: str,
                  action_type: Optional[str] = None, availability: Optional[str] = None,
                  send_reply_telegram: Optional[bool] = None) -> dict:
    """Full agent loop: interpret -> update DB -> relay blockers -> auto-unblock -> handoffs -> log everything."""
    pid = sender.project_id
    participants = project_participants(session, pid)
    tasks = project_tasks(session, pid)
    by_name = {p.name.lower(): p for p in participants}
    tdicts = task_dicts(tasks, participants)
    tdict = next((t for t in tdicts if task and t["id"] == task.id), None)

    project = session.get(Project, pid)
    # Links are proof, not status words: "https://.../broken-login-fix" must not read as a blocker
    readable = proof.ANY_LINK.sub("(link)", message)
    result, meta = llm_engine.analyze_participant_reply(
        readable, sender.model_dump(), tdict, [p.model_dump() for p in participants], tdicts, action_type=action_type,
        strict_dependencies=bool(project and project.strict_dependencies))
    actions, notifications = [], []
    title = task.title if task else "(no task)"

    add_log(session, pid, "REPLY", f"{sender.name} via {source}: {message}",
            {"participant_id": sender.id, "task_id": task.id if task else None, "source": source, "action_type": action_type})
    add_log(session, pid, "AI_REASONING",
            f"[{meta['layer']}] {sender.name}'s \"{title}\" -> {result['new_status']} "
            f"(confidence {result['confidence_score']:.2f})",
            {"result": result, "llm": meta})

    if task:
        previous = task.status
        task.status = result["new_status"]
        task.progress_note = result["progress_note"] or task.progress_note
        if task.status != "Complete":
            task.lead_approved = False  # a reopened task needs a fresh review
        proof_note = attach_proof(session, project, task, message, previous, actions) or proof_reminder(session, sender, actions)
        if proof_note:  # on its own paragraph, so it isn't lost at the end of a long reply
            result["reply_to_sender"] = f"{result['reply_to_sender'] or ''}\n\n{proof_note}".strip()
        if result["is_blocked"]:
            task.blocker_reason = result["blocker_reason"]
            helper = by_name.get((result["blocked_by_teammate_name"] or "").lower())
            task.blocked_by_participant_id = helper.id if helper else None
        else:
            task.blocker_reason = None
            task.blocked_by_participant_id = None
        task.updated_at = utcnow()
        actions.append({"type": "TASK_UPDATED", "task_id": task.id, "from": previous, "to": task.status})

    new_availability = llm_engine.availability_for_storage(availability or result["detected_availability"])
    if new_availability:
        sender.availability = new_availability
        actions.append({"type": "AVAILABILITY_UPDATED", "participant_id": sender.id, "availability": new_availability})

    if result["contradiction_warning"]:
        add_log(session, pid, "BLOCKER_ALERT", f"Contradiction: {result['contradiction_warning']}", {"task_id": task.id if task else None})
        actions.append({"type": "CONTRADICTION_REFUSED", "task_id": task.id if task else None})
    if result["is_blocked"]:
        add_log(session, pid, "BLOCKER_ALERT", f"{sender.name} blocked on \"{title}\": {result['blocker_reason']}",
                {"task_id": task.id if task else None, "blocked_by": result["blocked_by_teammate_name"]})

    # (d) Autonomous cross-teammate blocker relay
    target = by_name.get((result["auto_relay_target_name"] or "").lower())
    if target and result["auto_relay_message"]:
        notify(session, pid, target, result["auto_relay_message"],
               f"Relayed {sender.name}'s blocker to {target.name}", notifications, {"task_id": task.id if task else None})
        actions.append({"type": "BLOCKER_RELAYED", "to": target.name})

    # (d) Auto-unblock loop
    unblocked = None
    if result["unblocked_downstream_task_id"]:
        unblocked = session.get(Task, result["unblocked_downstream_task_id"])
        if unblocked and unblocked.project_id == pid and unblocked.status == "Blocked":
            old_reason = unblocked.blocker_reason
            unblocked.status = "In Progress"
            unblocked.blocker_reason = None
            unblocked.blocked_by_participant_id = None
            unblocked.progress_note = (f"{unblocked.progress_note} | " if unblocked.progress_note else "") + \
                f"Unblocked by {sender.name} ({old_reason or 'blocker'} resolved)"
            unblocked.updated_at = utcnow()
            owner = session.get(Participant, unblocked.owner_id) if unblocked.owner_id else None
            notify(session, pid, owner, result["unblocked_notification_message"],
                   f"Auto-unblocked \"{unblocked.title}\" ({owner.name if owner else 'unassigned'}) after {sender.name}'s update",
                   notifications, {"task_id": unblocked.id, "resolved": old_reason})
            actions.append({"type": "AUTO_UNBLOCKED", "task_id": unblocked.id, "to": "In Progress"})
        else:
            unblocked = None

    # Handoff: a finished task notifies everyone waiting on it
    if task and task.status == "Complete":
        notify_handoffs(session, task, sender.name, notifications, actions, skip_task_id=unblocked.id if unblocked else None)

    if send_reply_telegram is None:
        send_reply_telegram = source == "telegram"
    if send_reply_telegram and sender.telegram_chat_id:
        notifications.append(send_telegram(sender.name, sender.telegram_chat_id, result["reply_to_sender"]))

    session.commit()
    if task:
        session.refresh(task)
    if unblocked:
        session.refresh(unblocked)
    return {
        "result": result,
        "llm": meta,
        "actions": actions,
        "notifications": notifications,
        "task": task.model_dump() if task else None,
        "unblocked_task": unblocked.model_dump() if unblocked else None,
    }


def find_commit_for(session: Session, project: Optional[Project], task: Task) -> Optional[dict]:
    """A recent commit on the project's repo, by the task's owner, that clearly names this task."""
    if not project or not project.github_repo_url or task.task_type == "design_docs":
        return None
    try:
        commits = proof.recent_commits_cached(project.github_repo_url)
    except RuntimeError:
        return None
    people = project_participants(session, project.id)
    for c in commits:
        author = _match_author(people, c["author_login"], c["author_name"])
        if author and author.id != task.owner_id:
            continue
        first_line = c["message"].strip().splitlines()[0] if c["message"].strip() else ""
        if llm_engine.task_match_scores(first_line, [{"title": task.title, "description": task.description}])[0] >= 2:
            return {"sha": c["sha"], "message": first_line, "author": c["author_login"] or c["author_name"], "date": c["date"]}
    return None


def proof_reminder(session: Session, sender: Participant, actions: list) -> Optional[str]:
    """While one of the sender's finished tasks still has no proof, remind them (at most every 10 minutes per task)."""
    waiting = [t for t in session.exec(select(Task).where(Task.owner_id == sender.id, Task.status == "Complete")).all()
               if proof.proof_level(t) == "self" and _cooled_down(f"proofask:{t.id}", 600)]
    if not waiting:
        return None
    t = max(waiting, key=lambda x: x.updated_at)
    _last_ping[f"proofask:{t.id}"] = datetime.now(timezone.utc)
    actions.append({"type": "PROOF_REMINDER", "task_id": t.id})
    return proof.PROOF_REMINDER.format(title=t.title)


def attach_proof(session: Session, project: Optional[Project], task: Task, message: str, previous: str,
                 actions: list) -> Optional[str]:
    """Collect evidence that the work is real. Returns a sentence for the reply to the teammate, if any.

    - a commit / PR link is checked against the project's repo on GitHub
    - any other link must actually open (a sign-in page counts: the document exists)
    - a fresh "done" with no evidence: look for a matching commit; if none, ask for proof"""
    notes = []
    for url in proof.links_in(message):
        if proof.GITHUB_WORK_LINK.match(url):
            info, why = proof.verify_github_link(project.github_repo_url if project else None, url)
            if info:
                task.github_verified, task.last_commit_info = True, proof.commit_summary(info)
                actions.append({"type": "PROOF_VERIFIED", "task_id": task.id, "via": "github_link", "url": url})
                notes.append(f"Verified on GitHub ({task.last_commit_info.split(' · ')[0]}).")
            else:
                actions.append({"type": "PROOF_REJECTED", "task_id": task.id, "url": url, "reason": why})
                notes.append(f"I couldn't verify that link: {why}.")
            continue
        ok, why = proof.check_link(url)
        if ok is False:
            actions.append({"type": "PROOF_REJECTED", "task_id": task.id, "url": url, "reason": why})
            notes.append(f"I couldn't use that link as proof: {why}.")
            continue
        task.deliverable_url = url
        actions.append({"type": "DELIVERABLE_LINKED", "task_id": task.id, "url": url, "checked": ok is True})
        if task.status == "Complete":
            notes.append("Got your link: the work is verified.")
    if task.status == "Complete" and previous != "Complete" and proof.proof_level(task) == "self":
        found = find_commit_for(session, project, task)
        if found:
            task.github_verified = True
            task.last_commit_info = f"{found['sha'][:7]} {found['message'][:80]} · {found['author']} · {found['date'][:10]}"
            actions.append({"type": "PROOF_VERIFIED", "task_id": task.id, "via": "commit_match", "sha": found["sha"][:7]})
            notes.append(f"Verified automatically: I found your commit {found['sha'][:7]} \"{found['message'][:60]}\".")
        else:
            actions.append({"type": "PROOF_REQUESTED", "task_id": task.id})
            notes.append(proof.PROOF_REQUEST.format(title=task.title))
            _last_ping[f"proofask:{task.id}"] = datetime.now(timezone.utc)
    if any(a["type"] in ("PROOF_VERIFIED", "PROOF_REQUESTED", "PROOF_REJECTED") for a in actions):
        add_log(session, task.project_id, "AI_REASONING",
                f"Proof for \"{task.title}\": {proof.proof_level(task)}", {"task_id": task.id, "notes": notes})
    return " ".join(notes) or None


def build_dashboard(session: Session, participant: Participant) -> dict:
    participants = project_participants(session, participant.project_id)
    tasks = project_tasks(session, participant.project_id)
    tdicts = task_dicts(tasks, participants)
    by_id = {t["id"]: t for t in tdicts}

    mine = []
    for t in tdicts:
        if t["owner_id"] != participant.id:
            continue
        up = by_id.get(t["depends_on_task_id"])
        mine.append({
            "id": t["id"], "title": t["title"], "status": t["status"], "progress_note": t["progress_note"],
            "deadline": t["deadline"], "blocker_reason": t["blocker_reason"], "blocked_by_name": t["blocked_by_name"],
            "is_at_risk": t["is_at_risk"],
            "upstream": {"id": up["id"], "title": up["title"], "owner_name": up["owner_name"], "status": up["status"]} if up else None,
            "downstream": [{"id": d["id"], "title": d["title"], "owner_name": d["owner_name"], "status": d["status"]}
                           for d in tdicts if d["depends_on_task_id"] == t["id"]],
        })

    my_ids = {t["id"] for t in mine}
    to_solve = []
    for t in tdicts:
        if t["status"] != "Blocked" or t["owner_id"] == participant.id:
            continue
        if t["blocked_by_participant_id"] == participant.id:
            to_solve.append({"task_id": t["id"], "title": t["title"], "owner_name": t["owner_name"],
                             "need": t["blocker_reason"], "why": "They are waiting on you"})
        elif t["depends_on_task_id"] in my_ids:
            to_solve.append({"task_id": t["id"], "title": t["title"], "owner_name": t["owner_name"],
                             "need": t["blocker_reason"], "why": "Depends on your task"})

    project = session.get(Project, participant.project_id)
    return {
        "participant": {"id": participant.id, "name": participant.name, "role": participant.role,
                        "availability": participant.availability},
        "project": {"id": project.id, "name": project.name},
        "summary": {"my_open_tasks": sum(t["status"] != "Complete" for t in mine),
                    "my_blocked": sum(t["status"] == "Blocked" for t in mine),
                    "teammates_waiting_on_me": len(to_solve)},
        "my_tasks": mine,
        "blockers_i_need_to_solve": to_solve,
        "team_sync": llm_engine.coordinate_team_slots([p.model_dump() for p in participants], tdicts),
        "teammates": [{"id": p.id, "name": p.name, "role": p.role, "availability": p.availability}
                      for p in participants if p.id != participant.id],
    }


def ownership_violation(session: Session, sender: Participant, message: str) -> Optional[str]:
    """Refuse updates that target someone else's task ("Meera: done", or naming only another member's task)."""
    people = project_participants(session, sender.project_id)
    prefix = re.match(r"^\s*([A-Za-z][\w .'-]{0,30}?)\s*[:\-]\s+\S", message)
    target = None
    if prefix:
        target = next((p for p in people if p.name.lower() == prefix.group(1).strip().lower() and p.id != sender.id), None)
    if not target and llm_engine.is_status_update(message) and not llm_engine.is_help_message(message):
        tasks = [t for t in project_tasks(session, sender.project_id) if t.status != "Complete"]
        dicts = [{"title": t.title, "description": t.description} for t in tasks]
        scores = llm_engine.task_match_scores(message, dicts)
        own = max((s for s, t in zip(scores, tasks) if t.owner_id == sender.id), default=0)
        others = [(s, t) for s, t in zip(scores, tasks) if t.owner_id not in (None, sender.id)]
        best = max(others, key=lambda x: x[0], default=(0, None))
        if best[0] >= 2 and own == 0:  # clearly names another member's task and none of the sender's
            target = session.get(Participant, best[1].owner_id)
    if not target:
        return None
    return (f"\U0001F512 Access Denied: You are authenticated as {sender.name}. "
            f"Only {target.name} can update tasks assigned to {target.name}.")


def resolve_reply_task(session: Session, sender: Participant, message: str,
                       task_id: Optional[int]) -> tuple[Optional[Task], Optional[list[dict]]]:
    """Which of the sender's tasks a message is about. Returns (task, None), or (None, options) to ask the person."""
    if task_id is not None:
        return pick_task_for(session, sender, task_id), None
    own = [t for t in project_tasks(session, sender.project_id) if t.owner_id == sender.id]
    open_tasks = [t for t in own if t.status != "Complete"]
    if len(open_tasks) <= 1:
        return pick_task_for(session, sender, None), None
    idx = llm_engine.match_task(message, [{"title": t.title, "description": t.description} for t in open_tasks])
    if idx is not None:
        return open_tasks[idx], None
    if llm_engine.is_status_update(message) and not llm_engine.is_help_message(message):
        return None, [{"id": t.id, "title": t.title, "status": t.status} for t in open_tasks]
    return pick_task_for(session, sender, None), None


def ask_which_task(session: Session, sender: Participant, message: str, source: str, options: list[dict]) -> dict:
    """Generic 'done' from someone with several open tasks: ask which one instead of guessing."""
    listing = "\n".join(f"{i + 1}. {o['title']}" for i, o in enumerate(options))
    reply = f"Nice, {sender.name}! Which task is this about?\n{listing}"
    add_log(session, sender.project_id, "REPLY", f"{sender.name} via {source}: {message}",
            {"participant_id": sender.id, "source": source})
    add_log(session, sender.project_id, "AI_REASONING",
            f"Asked {sender.name} which of {len(options)} open tasks the update is about", {"options": options})
    session.commit()
    return {"result": {"needs_task_choice": True, "task_options": options, "reply_to_sender": reply,
                       "new_status": None, "contradiction_warning": None, "auto_relay_target_name": None,
                       "unblocked_downstream_task_id": None, "confidence_score": 0},
            "actions": [], "notifications": [], "task": None, "unblocked_task": None}


def run_member_update(session: Session, sender: Participant, message: str, source: str, task_id: Optional[int] = None,
                      action_type: Optional[str] = None, availability: Optional[str] = None,
                      send_reply_telegram: Optional[bool] = None, check_ownership: bool = False) -> dict:
    """Shared entry point for member updates from Telegram, the web and the mobile app."""
    if check_ownership:
        denied = ownership_violation(session, sender, message)
        if denied:
            return {"denied": denied, "result": {"reply_to_sender": denied}, "actions": [], "notifications": []}
    task, options = resolve_reply_task(session, sender, message, task_id)
    if options:
        return ask_which_task(session, sender, message, source, options)
    return process_reply(session, sender, task, message, source, action_type, availability, send_reply_telegram)


PROOF_DIR = Path(__file__).resolve().parent / "uploads"
PROOF_URL = "/api/proof-files/"


def handle_telegram_file(participant_id: int, filename: str, caption: str = "", task_id: Optional[int] = None) -> dict:
    """A photo / document sent to the bot as proof of work: attach it to the right task of the sender.

    The task is the one named (button or caption), else the single task waiting for proof, else the single
    one in progress. When it's unclear, return the choices so the bot can ask."""
    with Session(engine) as session:
        sender = session.get(Participant, participant_id)
        mine = [t for t in project_tasks(session, sender.project_id) if t.owner_id == sender.id]
        task = next((t for t in mine if t.id == task_id), None) if task_id else None
        if not task and caption.strip():
            idx = llm_engine.task_match_scores(caption, [{"title": t.title, "description": t.description} for t in mine])
            best = max(idx, default=0)
            task = mine[idx.index(best)] if best >= 2 and idx.count(best) == 1 else None
        if not task:
            waiting = sorted((t for t in mine if proof.proof_level(t) == "self"), key=lambda t: t.updated_at, reverse=True)
            active = [t for t in mine if t.status in ("In Progress", "Blocked")]
            pool = waiting or active
            if len(pool) == 1:
                task = pool[0]
            else:
                choices = pool or [t for t in mine if not proof.is_verified(t)] or mine
                return {"choose": [{"id": t.id, "title": t.title} for t in choices]}
        task.deliverable_url = PROOF_URL + filename
        task.updated_at = utcnow()
        add_log(session, task.project_id, "AI_REASONING",
                f"{sender.name} sent a file as proof for \"{task.title}\" on Telegram",
                {"task_id": task.id, "file": task.deliverable_url, "caption": caption[:200]})
        session.commit()
        return {"task": task.title, "status": task.status, "proof": proof.proof_level(task)}


@app.get("/api/proof-files/{name}")
def proof_file(name: str):
    """Files members sent as proof (random, unguessable names), for the Team Lead to review."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9]{1,8}", name):
        raise HTTPException(404, "File not found")
    path = PROOF_DIR / name
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


def handle_telegram_text(participant_id: int, text: str, task_id: Optional[int] = None) -> dict:
    """Hook for telegram_service: a verified member's message (ownership enforced) through the agent loop."""
    with Session(engine) as session:
        sender = session.get(Participant, participant_id)
        # The bot replies inline in the originating chat (DM or group), so don't DM the reply as well
        return run_member_update(session, sender, text, "telegram", task_id=task_id, send_reply_telegram=False,
                                 check_ownership=True)


# ---------- Follow-ups & Auto-Pilot (hands-free standup + blocker SLA) ----------

AUTOPILOT: dict = {
    "enabled": False,
    "interval_seconds": int(os.getenv("AUTOPILOT_INTERVAL_SECONDS", "45")),
    "blocker_sla_seconds": int(os.getenv("BLOCKER_SLA_SECONDS", "90")),
    "followup_cooldown_seconds": int(os.getenv("FOLLOWUP_COOLDOWN_SECONDS", "90")),
    "stale_after_seconds": int(os.getenv("STALE_IN_PROGRESS_SECONDS", "120")),
    "cycles": 0,
    "last_run_at": None,
    "next_run_at": None,
    "last_actions": [],
    "last_error": None,
    "cadence": "live",
    "cadence_label": "45s Live Demo",
    "cadences": {"live": "45s Live Demo", "hourly": "Every 1 Hour", "6h": "Every 6 Hours", "daily10": "Daily 10 AM"},
}
_autopilot_task: Optional[asyncio.Task] = None
_last_ping: dict[str, datetime] = {}  # "followup:<task_id>" / "sla:<task_id>" -> last sent (UTC)
_escalation_level: dict[int, int] = {}  # task_id -> SLA escalation level
MANAGER_ROLE = re.compile(r"manager|scrum|product owner|\bpm\b", re.I)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _age_seconds(dt: datetime) -> float:
    return (datetime.now(timezone.utc) - _as_utc(dt)).total_seconds()


def _fmt_wait(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def deadline_passed(deadline: str, now: Optional[datetime] = None) -> bool:
    """Understands ISO datetimes and 'Today 8:00 PM' / 'Tomorrow 10 AM' style deadlines."""
    return deadlines.is_overdue(deadline, now)


def schedule_project(session: Session, project: Project, start_at: Optional[str], due_at: str,
                     distribute: bool = True) -> list[Task]:
    """Set the project timeline and spread deadlines over its unfinished tasks along the dependency chain.

    Returns the tasks whose deadline changed."""
    start = deadlines.parse_deadline(start_at) or datetime.now().replace(second=0, microsecond=0)
    due = deadlines.parse_deadline(due_at)
    if not due:
        raise HTTPException(400, "Pick a date and time for the project deadline")
    if due <= start:
        raise HTTPException(400, "The project deadline must be after its start")
    project.start_at, project.due_at = deadlines.to_iso(start), deadlines.to_iso(due)
    session.add(project)
    changed = []
    if distribute:
        open_tasks = [t for t in project_tasks(session, project.id) if t.status != "Complete"]
        for task_id, iso in deadlines.distribute(open_tasks, start, due).items():
            task = session.get(Task, task_id)
            if task.deadline != iso:
                task.deadline, task.updated_at = iso, utcnow()
                session.add(task)
                changed.append(task)
    return changed


def finalize_new_project_deadlines(session: Session, project: Project, start_at: Optional[str], due_at: Optional[str]) -> None:
    """New projects get real dates: spread over the lead's timeline, or the planner's 'Day 2 - 4 PM' labels made concrete."""
    if due_at:
        schedule_project(session, project, start_at, due_at)
        return
    now = datetime.now()
    tasks = project_tasks(session, project.id)
    # "Day 1 - 11 AM" means today; if that's already past (a project made late in the day), Day 1 is tomorrow
    earliest = min((d for d in (deadlines.parse_deadline(t.deadline, now) for t in tasks) if d), default=None)
    base = now + timedelta(days=1) if earliest and earliest < now else now
    for task in tasks:
        iso = deadlines.normalize(task.deadline, base)
        if iso != task.deadline:
            task.deadline = iso
            session.add(task)


def _cooled_down(key: str, seconds: int) -> bool:
    last = _last_ping.get(key)
    return not last or (datetime.now(timezone.utc) - last).total_seconds() >= seconds


def followup_text(task: Task, owner: Participant, upstream: Optional[Task]) -> str:
    due = f" (due {deadlines.humanize(task.deadline)})" if task.deadline else ""
    if task.status == "Pending":
        text = (f"Hi {owner.name}, quick check-in from your AI Coordinator on \"{task.title}\"{due}. "
                f"Have you started? Reply with a quick update - e.g. \"started\", \"done\", or what's blocking you.")
        if upstream and upstream.status != "Complete":
            text += f" (FYI: it depends on \"{upstream.title}\", currently {upstream.status}.)"
        return text
    overdue = " and is past its deadline" if deadline_passed(task.deadline) else ""
    note = f" Last note: \"{task.progress_note}\"." if task.progress_note else ""
    return (f"Hi {owner.name}, \"{task.title}\" is still In Progress{overdue}{due}.{note} "
            f"How's it going? Any blocker I can relay to a teammate for you?")


def blocker_helper(session: Session, task: Task) -> Optional[Participant]:
    """Who can unblock this task: the teammate it's blocked on, else the owner of the unfinished work it depends on."""
    helper = session.get(Participant, task.blocked_by_participant_id) if task.blocked_by_participant_id else None
    if not helper and task.depends_on_task_id:
        upstream = session.get(Task, task.depends_on_task_id)
        if upstream and upstream.status != "Complete" and upstream.owner_id:
            helper = session.get(Participant, upstream.owner_id)
    return helper if helper and helper.id != task.owner_id else None


def send_blocker_reminder(session: Session, task: Task, notifications: list, reason: str,
                          level: Optional[int] = None) -> Optional[dict]:
    """Remind the teammate who can unblock a task, and tell them how to clear it."""
    helper = blocker_helper(session, task)
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    if not helper or not owner:
        return None
    if not task.blocked_by_participant_id:
        task.blocked_by_participant_id = helper.id  # record who the coordinator is chasing
    need = llm_engine._extract_need(task.blocker_reason or "")
    ask = f"Could you send the {need} to {owner.name}?" if need else f"Could you help {owner.name} get unblocked?"
    wait = _fmt_wait(_age_seconds(task.updated_at))
    prefix = f"Reminder (level {level})" if level else "Reminder"
    text = (f"{prefix}: {owner.name} is still blocked on \"{task.title}\" ({wait}) - "
            f"{task.blocker_reason or 'waiting on you'}. {ask} "
            f"Reply here with \"sent\" or \"done\" when it's sorted and I'll unblock {owner.name} automatically.")
    notify(session, task.project_id, helper, text,
           f"Reminded {helper.name} to unblock {owner.name} on \"{task.title}\" ({reason})", notifications,
           {"task_id": task.id, "reason": reason, "helper_id": helper.id}, log_type="BLOCKER_ALERT")
    _last_ping[f"sla:{task.id}"] = datetime.now(timezone.utc)
    return {"type": "BLOCKER_REMINDER", "task_id": task.id, "to": helper.name, "blocked": owner.name, "reason": reason}


def send_followup(session: Session, task: Task, notifications: list, reason: str) -> Optional[dict]:
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    if not owner:
        return None
    upstream = session.get(Task, task.depends_on_task_id) if task.depends_on_task_id else None
    notify(session, task.project_id, owner, followup_text(task, owner, upstream),
           f"Follow-up ping to {owner.name} on \"{task.title}\" ({reason})", notifications,
           {"task_id": task.id, "reason": reason}, log_type="FOLLOW_UP")
    _last_ping[f"followup:{task.id}"] = datetime.now(timezone.utc)
    return {"type": "FOLLOW_UP", "task_id": task.id, "to": owner.name, "reason": reason}


def blocker_sla_snapshot(session: Session, project_id: int) -> list[dict]:
    people = {p.id: p.name for p in project_participants(session, project_id)}
    out = []
    for t in project_tasks(session, project_id):
        if t.status != "Blocked":
            continue
        wait = _age_seconds(t.updated_at)
        out.append({"task_id": t.id, "title": t.title, "owner": people.get(t.owner_id),
                    "blocked_by": people.get(t.blocked_by_participant_id), "blocker_reason": t.blocker_reason,
                    "wait_seconds": int(wait), "wait": _fmt_wait(wait),
                    "sla_seconds": AUTOPILOT["blocker_sla_seconds"],
                    "sla_breached": wait >= AUTOPILOT["blocker_sla_seconds"],
                    "escalation_level": _escalation_level.get(t.id, 0)})
    return out


def run_autopilot_cycle() -> list[dict]:
    """One hands-free sweep over every Team Lead's project (and the active demo project)."""
    actions: list = []
    with Session(engine) as session:
        active = get_active_project(session)
        ids = [active.id] + [pr.id for pr in session.exec(select(Project).where(Project.owner_user_id != None)).all()  # noqa: E711
                             if pr.id != active.id]
    for project_id in ids:
        actions += _autopilot_project(project_id)
    return actions


def _autopilot_project(project_id: int) -> list[dict]:
    """Follow up on Pending / overdue / stale tasks and keep reminding whoever can unblock a Blocked task."""
    actions, notifications = [], []
    with Session(engine) as session:
        project = session.get(Project, project_id)
        tasks = project_tasks(session, project.id)
        people = {p.id: p for p in project_participants(session, project.id)}
        by_id = {t.id: t for t in tasks}
        cooldown, sla = AUTOPILOT["followup_cooldown_seconds"], AUTOPILOT["blocker_sla_seconds"]

        for tid in [tid for tid in _escalation_level if tid not in by_id or by_id[tid].status != "Blocked"]:
            _escalation_level.pop(tid, None)  # resolved blockers reset their SLA clock

        for t in tasks:
            owner = people.get(t.owner_id)
            if not owner:
                continue
            upstream = by_id.get(t.depends_on_task_id)

            if t.status == "Complete":  # done, but no proof yet: keep asking until there is some
                if proof.proof_level(t) == "self" and _cooled_down(f"proofask:{t.id}", cooldown):
                    notify(session, project.id, owner, proof.PROOF_REMINDER.format(title=t.title),
                           f"Asked {owner.name} for proof on \"{t.title}\"", notifications, {"task_id": t.id}, log_type="FOLLOW_UP")
                    _last_ping[f"proofask:{t.id}"] = datetime.now(timezone.utc)
                    actions.append({"type": "PROOF_REMINDER", "task_id": t.id, "to": owner.name})

            elif t.status == "Pending":
                if upstream and upstream.status != "Complete":
                    continue  # legitimately waiting on upstream - don't nag
                if _cooled_down(f"followup:{t.id}", cooldown):
                    actions.append(send_followup(session, t, notifications, "pending"))

            elif t.status == "In Progress":
                reason = ("overdue" if deadline_passed(t.deadline)
                          else "no update in " + _fmt_wait(_age_seconds(t.updated_at))
                          if _age_seconds(t.updated_at) >= AUTOPILOT["stale_after_seconds"] else None)
                if reason and _cooled_down(f"followup:{t.id}", cooldown):
                    actions.append(send_followup(session, t, notifications, reason))

            elif t.status == "Blocked":
                wait = _age_seconds(t.updated_at)
                if wait < sla or not _cooled_down(f"sla:{t.id}", cooldown):
                    continue
                level = _escalation_level.get(t.id, 0) + 1
                helper = blocker_helper(session, t)
                reason = t.blocker_reason or "unspecified blocker"
                if helper:
                    sent = send_blocker_reminder(session, t, notifications, f"blocked {_fmt_wait(wait)}", level)
                    if sent:
                        actions.append({**sent, "type": "SLA_ESCALATION", "level": level})
                if level >= 2 or not helper:  # second strike (or nobody identified): escalate to management
                    for mgr in people.values():
                        if MANAGER_ROLE.search(mgr.role or "") and mgr.id != owner.id:
                            notify(session, project.id, mgr,
                                   f"Escalation for {mgr.name}: \"{t.title}\" ({owner.name}) has been "
                                   f"blocked for {_fmt_wait(wait)} - {reason}. It is putting downstream work at risk.",
                                   f"SLA escalation to manager {mgr.name}: \"{t.title}\"", notifications,
                                   {"task_id": t.id}, log_type="BLOCKER_ALERT")
                            actions.append({"type": "MANAGER_ESCALATION", "task_id": t.id, "to": mgr.name})
                _escalation_level[t.id] = level
                _last_ping[f"sla:{t.id}"] = datetime.now(timezone.utc)

        actions = [a for a in actions if a]
        if actions:
            add_log(session, project.id, "AI_REASONING",
                    f"[Auto-Pilot] cycle #{AUTOPILOT['cycles'] + 1}: {len(actions)} autonomous action(s)",
                    {"actions": actions})
        session.commit()
    return actions


CADENCES = {  # reminder cadence -> (label, seconds between runs; None = scheduled clock time)
    "live": ("45s Live Demo", 45),
    "hourly": ("Every 1 Hour", 3600),
    "6h": ("Every 6 Hours", 6 * 3600),
    "daily10": ("Daily 10 AM", None),
}


def _seconds_until_next_run() -> float:
    if AUTOPILOT.get("cadence") == "daily10":
        now = datetime.now()  # local time: "10 AM" means the laptop's clock
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()
    return AUTOPILOT["interval_seconds"]


async def _autopilot_loop() -> None:
    first = AUTOPILOT.get("cadence") != "daily10"  # interval cadences check in right away; daily waits for 10 AM
    while AUTOPILOT["enabled"]:
        if first:
            try:
                actions = await asyncio.to_thread(run_autopilot_cycle)
                AUTOPILOT.update(cycles=AUTOPILOT["cycles"] + 1, last_actions=actions, last_error=None,
                                 last_run_at=datetime.now(timezone.utc).isoformat())
                emit_state_update("AUTOPILOT_CYCLE")
            except Exception as exc:  # never let the loop die mid-demo
                log.exception("Auto-Pilot cycle failed")
                AUTOPILOT["last_error"] = f"{type(exc).__name__}: {exc}"
        first = True
        wait = _seconds_until_next_run()
        AUTOPILOT["next_run_at"] = (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat()
        await asyncio.sleep(wait)


def start_autopilot(interval_seconds: int, cadence: Optional[str] = None) -> None:
    global _autopilot_task
    if cadence in CADENCES:
        seconds = CADENCES[cadence][1]
        AUTOPILOT.update(cadence=cadence, cadence_label=CADENCES[cadence][0])
        interval_seconds = seconds or interval_seconds
    else:
        AUTOPILOT.update(cadence="custom", cadence_label=f"Every {interval_seconds}s")
    restart = AUTOPILOT["enabled"] and _autopilot_task and not _autopilot_task.done()
    if restart:  # new cadence takes effect immediately
        _autopilot_task.cancel()
        _autopilot_task = None
    AUTOPILOT.update(enabled=True, interval_seconds=interval_seconds)
    if _autopilot_task is None or _autopilot_task.done():
        _autopilot_task = asyncio.get_running_loop().create_task(_autopilot_loop())


def stop_autopilot() -> None:
    global _autopilot_task
    AUTOPILOT.update(enabled=False, next_run_at=None)
    if _autopilot_task and not _autopilot_task.done():
        _autopilot_task.cancel()
    _autopilot_task = None


def autopilot_status() -> dict:
    with Session(engine) as session:
        project = get_active_project(session)
        sla = blocker_sla_snapshot(session, project.id)
    return {**AUTOPILOT, "running": bool(_autopilot_task and not _autopilot_task.done()),
            "project": project.name, "blocker_sla": sla}


# ---------- CSV / GitHub helpers ----------

CSV_STATUS_MAP = {
    **{k: "Pending" for k in ("to do", "todo", "open", "backlog", "pending", "new", "selected for development", "")},
    **{k: "In Progress" for k in ("in progress", "doing", "in development", "in review", "review", "code review",
                                  "qa", "testing", "started")},
    **{k: "Blocked" for k in ("blocked", "impediment", "on hold", "flagged")},
    **{k: "Complete" for k in ("done", "complete", "completed", "closed", "resolved", "released", "shipped")},
}


def _commits_from_payload(payload: dict) -> list[dict]:
    """Accepts the simulated {author, commit_message, repo} body or a real GitHub push payload."""
    if payload.get("commit_message"):
        task_id = payload.get("task_id")
        return [{"author": str(payload.get("author", "")), "message": str(payload["commit_message"]),
                 "repo": str(payload.get("repo", "")), "task_id": int(task_id) if str(task_id or "").isdigit() else None}]
    repo = (payload.get("repository") or {}).get("full_name", "")
    commits = payload.get("commits") or ([payload["head_commit"]] if payload.get("head_commit") else [])
    out = []
    for c in commits:
        a = c.get("author") or {}
        out.append({"author": a.get("username") or a.get("name") or (payload.get("pusher") or {}).get("name", ""),
                    "author_name": a.get("name", ""), "message": c.get("message", ""), "repo": repo})
    return out


def _match_author(people: list[Participant], *handles: str) -> Optional[Participant]:
    for h in filter(None, handles):
        hl = h.strip().lstrip("@").lower()
        for p in people:
            if hl in (p.name.lower(), (p.telegram_username or "").lower()):
                return p
        tokens = set(re.split(r"[^a-z]+", hl))  # "rohan-dev", "Rohan Sharma"
        for p in people:
            if p.name.lower() in tokens:
                return p
    return None


def project_for_commits(session: Session, commits: list[dict], auth: Optional[dict]) -> Project:
    """Which project a commit belongs to: the task it names, the dashboard user's project, or the repo it was pushed to."""
    for c in commits:
        task = session.get(Task, c["task_id"]) if c.get("task_id") else None
        if task:
            return session.get(Project, task.project_id)
    if auth:
        project = current_project(session, auth, required=False)
        if project:
            return project
    repos = {(c.get("repo") or "").lower() for c in commits if c.get("repo")}
    for project in session.exec(select(Project).where(Project.github_repo_url != None).order_by(Project.id.desc())).all():  # noqa: E711
        parsed = github_repo.parse_repo_url(project.github_repo_url)
        if parsed and f"{parsed[0]}/{parsed[1]}".lower() in repos:
            return project
    return get_active_project(session)


def process_commits(commits: list[dict], auth: Optional[dict] = None) -> dict:
    results = []
    with Session(engine) as session:
        project = project_for_commits(session, commits, auth)
        people = project_participants(session, project.id)
        for c in commits:
            sender = _match_author(people, c.get("author", ""), c.get("author_name", ""))
            first_line = (c.get("message") or "").strip().splitlines()[0] if c.get("message") else ""
            message = re.sub(r"^\w+(\([^)]*\))?!?:\s*", "", first_line)  # strip "feat(api): " prefixes
            if not sender or not message:
                results.append({"author": c.get("author"), "status": "ignored",
                                "reason": f"{c.get('author') or 'the author'} isn't a member of \"{project.name}\"" if not sender
                                else "empty commit message"})
                continue
            out = process_reply(session, sender, pick_task_for(session, sender, c.get("task_id")), message, "github")
            results.append({"author": c.get("author"), "participant": sender.name, "status": "processed",
                            "repo": c.get("repo"), "commit_message": message, **out})
    return {"processed": sum(r["status"] == "processed" for r in results), "results": results}


# ---------- Routes ----------

@app.get("/")
def root():
    return {"status": "ok", "service": "AI Group Project Coordinator"}


@app.get("/api/state")
def get_state(project_id: Optional[int] = None, session: Session = Depends(get_session),
              auth: Optional[dict] = Depends(current_auth)):
    """The active project, or a specific one (members view the project they joined).

    Personal auth PINs are only visible to the Team Lead, and each member sees only their own."""
    projects = visible_projects(session, auth)
    project = next((pr for pr in projects if pr.id == project_id), None) if project_id else None
    project = project or current_project(session, auth, required=False)
    account_type = caller_account_type(session, auth)
    integrations = {"telegram": telegram_service.mode, "telegram_bot": telegram_service.bot_username,
                    "slack": slack_settings()["enabled"],
                    "autopilot": AUTOPILOT["enabled"], "ws_clients": len(manager.active)}
    if not project:  # a brand-new Team Lead: nothing yet, the dashboard shows "create your first project"
        return {"active_project": None, "projects": [], "participants": [], "tasks": [], "logs": [],
                "llm_layer": llm_engine.get_llm_status()["active_layer"], "account_type": account_type,
                "integrations": integrations, "task_requests": []}
    participants = project_participants(session, project.id)
    tasks = project_tasks(session, project.id)
    logs = session.exec(
        select(AgentLog)
        .where(AgentLog.project_id == project.id)
        .order_by(AgentLog.timestamp.desc(), AgentLog.id.desc())
        .limit(40)
    ).all()
    is_lead = bool(auth and auth.get("role") == "lead")
    my_pid = (auth or {}).get("pid")
    people = [{**p.model_dump(), "auth_pin": p.auth_pin if is_lead or p.id == my_pid else None} for p in participants]
    return {
        "active_project": project,
        "projects": projects,
        "participants": people,
        "tasks": task_dicts(tasks, participants),
        "logs": logs,
        "llm_layer": llm_engine.get_llm_status()["active_layer"],
        "account_type": account_type,
        "integrations": integrations,
        # Team Lead: every pending request; member: their own recent requests
        "task_requests": [request_dict(session, r) for r in session.exec(
            select(TaskRequest).where(TaskRequest.project_id == project.id,
                                      *([TaskRequest.requester_id == my_pid] if auth and auth.get("role") == "member"
                                        else [TaskRequest.status == "pending"]))
            .order_by(TaskRequest.id.desc()).limit(20)).all()],
    }


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectCreate, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    ensure_workspace_allowed(session, auth, body.workspace_type)
    project = Project(name=body.name, description=body.description, workspace_type=body.workspace_type,
                      github_repo_url=body.github_repo_url or None, invite_code=new_invite_code(session, body.name),
                      owner_user_id=(auth or {}).get("uid"))
    session.add(project)
    session.flush()
    if body.due_at:
        schedule_project(session, project, body.start_at, body.due_at)
    open_project(session, auth, project)
    session.commit()
    session.refresh(project)
    return project


@app.get("/api/projects/summary")
def projects_summary(session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    """Every project the caller can see, with the numbers a dashboard card needs (no AI calls: fast)."""
    current = current_project(session, auth, required=False)
    out = []
    for pr in visible_projects(session, auth):
        tasks = project_tasks(session, pr.id)
        people = project_participants(session, pr.id)
        done = sum(t.status == "Complete" for t in tasks)
        out.append({
            "id": pr.id, "name": pr.name, "workspace_type": pr.workspace_type, "github_repo_url": pr.github_repo_url,
            "invite_code": pr.invite_code, "due_at": pr.due_at, "is_current": bool(current and current.id == pr.id),
            "members": len(people), "verified_members": sum(bool(pp.telegram_chat_id) for pp in people),
            "tasks": len(tasks), "done": done, "blocked": sum(t.status == "Blocked" for t in tasks),
            "in_progress": sum(t.status == "In Progress" for t in tasks),
            "verified": sum(proof.is_verified(t) for t in tasks),
            "overdue": sum(t.status != "Complete" and deadlines.is_overdue(t.deadline) for t in tasks),
            "pct": round(100 * done / len(tasks)) if tasks else 0,
            "pending_requests": len(session.exec(select(TaskRequest).where(TaskRequest.project_id == pr.id,
                                                                          TaskRequest.status == "pending")).all()),
        })
    return sorted(out, key=lambda x: (not x["is_current"], -x["id"]))


@app.post("/api/projects/{project_id}/activate")
def activate_project(project_id: int, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    project = require_visible(session, auth, project_id)
    ensure_workspace_allowed(session, auth, project.workspace_type)
    open_project(session, auth, project)
    session.commit()
    session.refresh(project)
    return project


@app.post("/api/participants", status_code=201)
def add_participant(body: ParticipantCreate, session: Session = Depends(get_session),
                    auth: Optional[dict] = Depends(require_lead)):
    project = current_project(session, auth)
    participant = Participant(
        project_id=project.id,
        name=body.name,
        role=body.role,
        telegram_username=body.telegram_username,
    )
    session.add(participant)
    session.commit()
    session.refresh(participant)
    return participant


@app.post("/api/tasks", status_code=201)
def add_task(body: TaskCreate, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    project = current_project(session, auth)
    validate_participant(session, project.id, body.owner_id)
    validate_dependency(session, project.id, body.depends_on_task_id)
    task = Task(
        project_id=project.id,
        title=body.title,
        description=body.description,
        owner_id=body.owner_id,
        depends_on_task_id=body.depends_on_task_id,
        deadline=deadlines.normalize(body.deadline),
        task_type=body.task_type,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdate, session: Session = Depends(get_session),
                auth: Optional[dict] = Depends(require_lead)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_visible(session, auth, task.project_id)
    changes = body.model_dump(exclude_unset=True)
    if "title" in changes:
        changes["title"] = (changes["title"] or "").strip()
        if len(changes["title"]) < 3:
            raise HTTPException(400, "Give the task a title (at least 3 characters).")
    if "status" in changes and changes["status"] not in TASK_STATUSES:
        raise HTTPException(400, f"status must be one of {list(TASK_STATUSES)}")
    if "owner_id" in changes:
        validate_participant(session, task.project_id, changes["owner_id"])
    if "blocked_by_participant_id" in changes:
        validate_participant(session, task.project_id, changes["blocked_by_participant_id"])
    if "depends_on_task_id" in changes:
        validate_dependency(session, task.project_id, changes["depends_on_task_id"], task.id)

    before = {"title": task.title, "owner_id": task.owner_id}
    for key, value in changes.items():
        setattr(task, key, value)
    notifications: list = []
    edited = [k for k in ("title", "description", "owner_id") if k in changes]
    if "owner_id" in changes and changes["owner_id"] != before["owner_id"]:
        new_owner = session.get(Participant, task.owner_id) if task.owner_id else None
        old_owner = session.get(Participant, before["owner_id"]) if before["owner_id"] else None
        if new_owner:
            notify(session, task.project_id, new_owner, f"Hi {new_owner.name}, your Team Lead assigned you \"{task.title}\".",
                   f"Assigned \"{task.title}\" to {new_owner.name}", notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
        if old_owner:
            notify(session, task.project_id, old_owner, f"Hi {old_owner.name}, \"{task.title}\" was moved to "
                   f"{new_owner.name if new_owner else 'nobody yet'}. You don't need to work on it any more.",
                   f"Told {old_owner.name} the task moved", notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
    elif "title" in changes and changes["title"] != before["title"] and task.owner_id:
        owner = session.get(Participant, task.owner_id)
        if owner:
            notify(session, task.project_id, owner, f"Hi {owner.name}, your Team Lead renamed \"{before['title']}\" to \"{task.title}\".",
                   f"Renamed \"{before['title']}\" -> \"{task.title}\"", notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
    if edited:
        add_log(session, task.project_id, "AI_REASONING", f"[Team Lead edit] \"{task.title}\" ({', '.join(edited)})", {"task_id": task.id})
    # Clear stale blocker info once a task leaves the Blocked state
    if changes.get("status") and changes["status"] != "Blocked":
        if "blocker_reason" not in changes:
            task.blocker_reason = None
        if "blocked_by_participant_id" not in changes:
            task.blocked_by_participant_id = None
    task.updated_at = utcnow()
    session.commit()
    session.refresh(task)
    return {**task.model_dump(), "notifications": notifications}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, notify_owner: bool = True, session: Session = Depends(get_session),
                auth: Optional[dict] = Depends(require_lead)):
    """Team Lead discards a task. Tasks that waited on it now wait on whatever it waited on, so the chain stays intact."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_visible(session, auth, task.project_id)
    relinked = 0
    for t in session.exec(select(Task).where(Task.depends_on_task_id == task.id)).all():
        t.depends_on_task_id = task.depends_on_task_id if task.depends_on_task_id != t.id else None
        relinked += 1
    for r in session.exec(select(TaskRequest).where(TaskRequest.related_task_id == task.id)).all():
        r.related_task_id = None
    for r in session.exec(select(TaskRequest).where(TaskRequest.created_task_id == task.id)).all():
        r.created_task_id = None
    notifications: list = []
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    if owner and notify_owner and task.status != "Complete":
        notify(session, task.project_id, owner, f"Hi {owner.name}, your Team Lead removed \"{task.title}\". You don't need to work on it.",
               f"Told {owner.name} the task was removed", notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
    add_log(session, task.project_id, "AI_REASONING", f"[Team Lead] Deleted \"{task.title}\"",
            {"task_id": task.id, "relinked": relinked})
    session.flush()
    session.delete(task)
    session.commit()
    return {"deleted": task_id, "title": task.title, "relinked": relinked, "notifications": notifications}


@app.post("/api/reset-demo")
def reset_demo(session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    project = seed_default_project(session)
    return {"status": "reset", "active_project": project}


# ---------- AI coordinator ----------

def guard_member(session: Session, auth: Optional[dict], sender: Participant, task_id: Optional[int]) -> bool:
    """Signed-in members may only update their own tasks; the Team Lead manages everyone.

    Returns True when the caller is a member (so text-level ownership checks apply too)."""
    if not auth:
        if auth_required():
            raise HTTPException(401, "Sign in to send updates")
        return False
    if auth.get("role") == "lead":
        return False
    me = session.get(Participant, auth.get("pid")) if auth.get("pid") else None
    if not me or me.id != sender.id:
        raise HTTPException(403, f"Access Denied: you are signed in as {me.name if me else 'another member'}. "
                                 f"Only {sender.name} can send updates as {sender.name}.")
    task = session.get(Task, task_id) if task_id else None
    if task and task.owner_id != me.id:
        owner = session.get(Participant, task.owner_id) if task.owner_id else None
        raise HTTPException(403, f"Access Denied: You are authenticated as {me.name}. "
                                 f"Only {owner.name if owner else 'its owner'} can update tasks assigned to them.")
    return True


@app.post("/api/simulate-reply")
def simulate_reply(body: SimulateReply, session: Session = Depends(get_session),
                   auth: Optional[dict] = Depends(current_auth)):
    project = current_project(session, auth)
    sender = None
    if body.participant_id is not None:
        sender = session.get(Participant, body.participant_id)
    elif body.participant_name:
        sender = session.exec(select(Participant).where(
            Participant.project_id == project.id,
            func.lower(Participant.name) == body.participant_name.strip().lower())).first()
    elif body.telegram_chat_id:
        sender = session.exec(select(Participant).where(
            Participant.project_id == project.id, Participant.telegram_chat_id == body.telegram_chat_id)).first()
    elif body.telegram_username:
        sender = session.exec(select(Participant).where(
            Participant.project_id == project.id,
            func.lower(Participant.telegram_username) == body.telegram_username.lstrip("@").lower())).first()
    if not sender or (auth and sender.project_id not in {pr.id for pr in visible_projects(session, auth)}):
        raise HTTPException(404, "Sender not found in your project")
    if not body.message.strip():
        raise HTTPException(400, "message is empty")
    is_member = guard_member(session, auth, sender, body.task_id)
    out = run_member_update(session, sender, body.message, body.source, task_id=body.task_id, check_ownership=is_member)
    if out.get("denied"):
        raise HTTPException(403, out["denied"])
    return out


@app.post("/api/mobile/quick-update")
def mobile_quick_update(body: MobileQuickUpdate, session: Session = Depends(get_session),
                        auth: Optional[dict] = Depends(current_auth)):
    sender = session.get(Participant, body.participant_id)
    if not sender:
        raise HTTPException(404, "Participant not found")
    is_member = guard_member(session, auth, sender, body.task_id)
    text = body.text.strip()
    if not text and body.action_type == "SOS_BLOCKER":
        text = "SOS: I'm blocked and need help to continue."
    if not text:  # availability-only ping from the app
        if not body.availability:
            raise HTTPException(400, "Provide text or availability")
        sender.availability = llm_engine.availability_for_storage(body.availability)
        add_log(session, sender.project_id, "REPLY", f"{sender.name} via android: availability -> {sender.availability}")
        session.commit()
        return {"result": None, "actions": [{"type": "AVAILABILITY_UPDATED", "availability": sender.availability}],
                "notifications": [], "dashboard": build_dashboard(session, sender)}

    out = run_member_update(session, sender, text, "android", task_id=body.task_id, action_type=body.action_type,
                            availability=body.availability, check_ownership=is_member)
    if out.get("denied"):
        raise HTTPException(403, out["denied"])
    if out["result"].get("needs_task_choice"):
        out["dashboard"] = build_dashboard(session, sender)
        return out
    task = session.get(Task, out["task"]["id"]) if out.get("task") else None

    # SOS is the emergency button: alert every teammate (the one already asked directly gets just that message)
    if body.action_type == "SOS_BLOCKER":
        asked = (out["result"]["auto_relay_target_name"] or "").lower()
        sos = (f"SOS from {sender.name} on \"{task.title if task else 'their task'}\": "
               f"{out['result']['blocker_reason'] or text}. Can anyone help right now?")
        for p in project_participants(session, sender.project_id):
            if p.id != sender.id and p.name.lower() != asked:
                notify(session, sender.project_id, p, sos, f"SOS broadcast from {sender.name} to {p.name}", out["notifications"])
        out["actions"].append({"type": "SOS_BROADCAST"})
        session.commit()
    out["dashboard"] = build_dashboard(session, sender)
    return out


@app.get("/api/mobile/my-dashboard/{participant_id}")
def mobile_dashboard(participant_id: int, session: Session = Depends(get_session)):
    participant = session.get(Participant, participant_id)
    if not participant:
        raise HTTPException(404, "Participant not found")
    return build_dashboard(session, participant)


def new_member(session: Session, project_id: int, name: str, role: str) -> Participant:
    """A new teammate gets their own PIN for this project; Telegram links only after they /auth with it
    (a matching name in another project is not proof it's the same person)."""
    p = Participant(project_id=project_id, name=name.strip(), role=role.strip())
    session.add(p)
    return p


@app.post("/api/ai/generate-project", status_code=201)
def ai_generate_project(body: GenerateProject, session: Session = Depends(get_session),
                        auth: Optional[dict] = Depends(require_lead)):
    ensure_workspace_allowed(session, auth, body.workspace_type)
    plan, meta = llm_engine.generate_project_plan(body.project_name, body.description, body.teammates)
    project = Project(name=body.project_name, description=body.description, workspace_type=body.workspace_type,
                      invite_code=new_invite_code(session, body.project_name), owner_user_id=(auth or {}).get("uid"))
    session.add(project)
    session.flush()
    open_project(session, auth, project)

    people = {}
    for tm in plan["teammates"]:
        people[tm["name"]] = new_member(session, project.id, tm["name"], tm["role"])
    session.flush()

    created: list[Task] = []
    for t in plan["tasks"]:
        dep = created[t["depends_on"]] if t["depends_on"] is not None else None
        task = Task(project_id=project.id, title=t["title"], description=t["description"],
                    owner_id=people[t["owner"]].id, depends_on_task_id=dep.id if dep else None, deadline=t["deadline"])
        session.add(task)
        session.flush()
        created.append(task)
    finalize_new_project_deadlines(session, project, body.start_at, body.due_at)

    add_log(session, project.id, "AI_REASONING",
            f"[{meta['layer']}] Generated \"{project.name}\": {len(created)} tasks across {len(people)} teammates",
            {"plan": plan, "llm": meta})
    session.commit()
    participants = list(people.values())
    for p in participants:
        session.refresh(p)
    session.refresh(project)
    return {"project": project, "participants": participants,
            "tasks": task_dicts(project_tasks(session, project.id), participants), "llm": meta}


def welcome_text(project: Project, member: Participant, tasks: list[Task], by_id: dict) -> str:
    lines = [f"Hi {member.name}, you've been added to \"{project.name}\" as {member.role or 'a team member'}."]
    if tasks:
        lines.append("Your tasks:")
        for t in tasks:
            dep = by_id.get(t.depends_on_task_id)
            lines.append(f"- {t.title}" + (f" (due {deadlines.humanize(t.deadline)})" if t.deadline else "")
                         + (f" - starts after \"{dep.title}\"" if dep else ""))
    lines.append("Reply here any time with your progress or what you're blocked on, and I'll keep the team in sync.")
    return "\n".join(lines)


@app.post("/api/projects/from-github", status_code=201)
def project_from_github(body: FromGithub, session: Session = Depends(get_session),
                        auth: Optional[dict] = Depends(require_lead)):
    """Read a real GitHub repo, let the AI split it into dependent tasks by role, and brief each member on Telegram."""
    ensure_workspace_allowed(session, auth, body.workspace_type)
    members = [m if isinstance(m, TeamMember) else TeamMember(name=m) for m in body.team_members]
    members = [m for m in members if m.name.strip()]
    if not members:
        raise HTTPException(400, "Add at least one team member")
    if not github_repo.parse_repo_url(body.github_repo_url):
        raise HTTPException(400, "That doesn't look like a GitHub repository URL (expected https://github.com/owner/repo)")

    profile = github_repo.analyze_repo(body.github_repo_url, body.description)
    name = body.project_name.strip() or re.sub(r"[-_]+", " ", profile.get("name") or "GitHub project").title()
    description = body.description.strip() or profile.get("description") or ""
    member_dicts = [{"name": m.name.strip(), "role": m.role.strip()} for m in members]
    plan, meta = llm_engine.generate_repo_plan(profile, member_dicts, body.workspace_type, name, description)

    project = Project(name=name, description=description, workspace_type=body.workspace_type,
                      github_repo_url=profile.get("url") or body.github_repo_url,
                      invite_code=new_invite_code(session, name), owner_user_id=(auth or {}).get("uid"))
    session.add(project)
    session.flush()
    open_project(session, auth, project)
    people = {m["name"]: new_member(session, project.id, m["name"], m["role"]) for m in member_dicts}
    session.flush()

    created: list[Task] = []
    for t in plan:
        dep = created[t["depends_on"]] if t["depends_on"] is not None else None
        task = Task(project_id=project.id, title=t["title"], description=t["description"], owner_id=people[t["owner"]].id,
                    depends_on_task_id=dep.id if dep else None, deadline=t["deadline"],
                    progress_note="Assigned from the GitHub repo analysis")
        session.add(task)
        session.flush()
        created.append(task)
    finalize_new_project_deadlines(session, project, body.start_at, body.due_at)

    analysis = {k: profile.get(k) for k in ("url", "full_name", "fetched", "description", "language", "stack",
                                            "root_files", "root_dirs", "error", "resolved_from", "suggestions")}
    add_log(session, project.id, "AI_REASONING",
            f"[{meta['layer']}] Analyzed {profile.get('full_name') or body.github_repo_url} "
            f"({'live from GitHub' if profile.get('fetched') else 'repo not reachable - used URL and description'}): "
            f"{len(created)} tasks across {len(people)} members",
            {"repo": analysis, "plan": plan, "llm": meta})

    # Brief each member with their assignments (Telegram if linked, otherwise logged as simulated)
    by_id = {t.id: t for t in created}
    notifications: list = []
    for p in people.values():
        mine = [t for t in created if t.owner_id == p.id]
        notify(session, project.id, p, welcome_text(project, p, mine, by_id),
               f"Sent {p.name} their {len(mine)} task assignment{'s' if len(mine) != 1 else ''}", notifications,
               {"participant_id": p.id}, log_type="FOLLOW_UP")
    session.commit()
    participants = list(people.values())
    for p in participants:
        session.refresh(p)
    session.refresh(project)
    return {"project": project, "participants": participants,
            "tasks": task_dicts(project_tasks(session, project.id), participants),
            "repo": analysis, "llm": meta, "notifications": notifications}


def report_project(session: Session, auth: Optional[dict], project_id: Optional[int]) -> Project:
    """The project a report is for: the one asked for (if the caller may see it), else the caller's current one."""
    return require_visible(session, auth, project_id) if project_id else current_project(session, auth)


@app.get("/api/report")
def report(ai: bool = True, project_id: Optional[int] = None, session: Session = Depends(get_session),
           auth: Optional[dict] = Depends(current_auth)):
    project = report_project(session, auth, project_id)
    participants = project_participants(session, project.id)
    tasks = project_tasks(session, project.id)
    logs = session.exec(select(AgentLog).where(AgentLog.project_id == project.id)
                        .order_by(AgentLog.id.desc()).limit(100)).all()
    tdicts = task_dicts(tasks, participants)
    out = llm_engine.generate_project_report(
        project.model_dump(), [p.model_dump() for p in participants], tdicts,
        [lg.model_dump() for lg in logs], use_ai=ai)
    pdicts = [p.model_dump() for p in participants]
    out["milestone_plan"] = insights.milestones(tdicts)  # phases along the dependency chain, with due dates
    out["risks"] = insights.risks(tdicts, pdicts)  # ranked: critical / serious / warning
    levels = [proof.proof_level(t) for t in tasks]
    out["verification"] = {
        "complete": sum(lv != "none" for lv in levels),
        "verified": sum(lv in ("github", "deliverable", "lead") for lv in levels),
        "needs_proof": sum(lv == "self" for lv in levels),
        "total": len(tasks),
        "truly_done": bool(tasks) and all(lv in ("github", "deliverable", "lead") for lv in levels),
    }
    return out


@app.get("/api/report.md")
def report_markdown(project_id: Optional[int] = None, session: Session = Depends(get_session),
                    auth: Optional[dict] = Depends(require_lead)):
    """The progress report as a Markdown file: download it, paste it in a chat, or submit it as demo evidence."""
    project = report_project(session, auth, project_id)
    participants = project_participants(session, project.id)
    data = report(ai=True, project_id=project.id, session=session, auth=auth)
    md = insights.report_markdown(data, project.model_dump(), task_dicts(project_tasks(session, project.id), participants),
                                  [p.model_dump() for p in participants])
    name = re.sub(r"[^A-Za-z0-9]+", "-", project.name).strip("-").lower() or "project"
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}-progress-report.md"'})


@app.get("/api/report.pdf")
def report_pdf(project_id: Optional[int] = None, session: Session = Depends(get_session),
               auth: Optional[dict] = Depends(require_lead)):
    """The progress report as a PDF, for any Team Lead (College or Corporate) and any of their projects."""
    project = report_project(session, auth, project_id)
    participants = project_participants(session, project.id)
    data = report(ai=True, project_id=project.id, session=session, auth=auth)
    label = "Corporate sprint" if project.workspace_type == "company" else "College project"
    pdf = pdf_report.build_pdf(data, project.model_dump(), task_dicts(project_tasks(session, project.id), participants), label)
    name = re.sub(r"[^A-Za-z0-9]+", "-", project.name).strip("-").lower() or "project"
    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}-progress-report-{stamp}.pdf"'})


@app.get("/api/llm-status")
def llm_status(probe: bool = False):
    return llm_engine.get_llm_status(probe=probe)


# ---------- Telegram ----------

def _lan_ipv4_candidates() -> list[str]:
    """Local IPv4 addresses phones on the same Wi-Fi / hotspot can reach, best guess first."""
    found: list[str] = []
    try:  # The interface used for the default route (no packet is actually sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            found.append(s.getsockname()[0])
    except OSError:
        pass  # offline hotspot with no internet route
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass

    def private(ip: str) -> bool:
        a, b = (int(x) for x in ip.split(".")[:2])
        return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)

    unique = list(dict.fromkeys(ip for ip in found if not ip.startswith(("127.", "169.254."))))
    # Prefer typical home Wi-Fi / phone hotspot ranges
    return sorted(unique, key=lambda ip: (not private(ip), not ip.startswith("192.168."), unique.index(ip)))


@app.get("/api/network-info")
def network_info(frontend_port: int = 5173):
    candidates = _lan_ipv4_candidates()
    lan_ip = candidates[0] if candidates else None
    bot = telegram_service.bot_username
    return {
        "lan_ip": lan_ip,
        "candidates": candidates,
        "frontend_port": frontend_port,
        "mobile_app_url": f"http://{lan_ip}:{frontend_port}/?mode=mobile" if lan_ip else None,
        "telegram_bot_url": f"https://t.me/{bot}" if bot else None,
        "telegram_bot_username": bot,
        "telegram_mode": telegram_service.mode,
    }


@app.get("/api/telegram/status")
def telegram_status():
    return {**telegram_service.status(), "board_preview": status_board_html()}


# ---------- Auto-Pilot ----------

@app.post("/api/autopilot/toggle")
async def autopilot_toggle(body: AutopilotToggle):
    if body.blocker_sla_seconds:
        AUTOPILOT["blocker_sla_seconds"] = body.blocker_sla_seconds
    if body.followup_cooldown_seconds:
        AUTOPILOT["followup_cooldown_seconds"] = body.followup_cooldown_seconds
    if body.enabled:
        start_autopilot(body.interval_seconds, body.cadence)
    else:
        stop_autopilot()
    with Session(engine) as session:
        project = get_active_project(session)
        add_log(session, project.id, "AI_REASONING",
                f"[Auto-Pilot] {'ENABLED - ' + AUTOPILOT['cadence_label'] if body.enabled else 'disabled'}",
                {k: AUTOPILOT[k] for k in ("cadence", "interval_seconds", "blocker_sla_seconds", "followup_cooldown_seconds")})
        session.commit()
    return await asyncio.to_thread(autopilot_status)


@app.get("/api/autopilot/status")
def get_autopilot_status():
    return autopilot_status()


@app.post("/api/autopilot/run-now")
async def autopilot_run_now():
    actions = await asyncio.to_thread(run_autopilot_cycle)
    AUTOPILOT.update(cycles=AUTOPILOT["cycles"] + 1, last_actions=actions,
                     last_run_at=datetime.now(timezone.utc).isoformat())
    return {"actions": actions, "status": await asyncio.to_thread(autopilot_status)}


# ---------- Presets, import & integrations ----------

@app.post("/api/presets/load")
def load_preset(body: PresetLoad, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    ensure_workspace_allowed(session, auth, body.mode)
    if auth and not is_demo_user(session.get(User, auth.get("uid"))):
        raise HTTPException(403, "Demo presets are only for the demo accounts. Create a project from a GitHub repo instead.")
    project = seed_default_project(session) if body.mode == "college" else seed_enterprise_project(session)
    open_project(session, auth, project)
    session.commit()
    _last_ping.clear()
    _escalation_level.clear()
    participants = project_participants(session, project.id)
    return {"mode": body.mode, "active_project": project, "participants": participants,
            "tasks": task_dicts(project_tasks(session, project.id), participants)}


@app.post("/api/import-csv", status_code=201)
def import_csv(body: CsvImport, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    if body.project_name:
        ensure_workspace_allowed(session, auth, "company")
    reader = csv.DictReader(io.StringIO(body.csv_text.strip().lstrip("﻿")))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV is empty")
    header = {h.strip().lower(): h for h in reader.fieldnames if h}

    def col(row: dict, *names: str) -> str:
        for n in names:
            if n in header:
                return (row.get(header[n]) or "").strip()
        return ""

    if "title" not in header and "summary" not in header:
        raise HTTPException(400, "CSV needs a Title column (Title,Owner,Role,Status,Deadline)")

    if body.project_name:
        project = Project(name=body.project_name, description="Imported from CSV (Jira / Linear sprint)",
                          workspace_type="company", invite_code=new_invite_code(session, body.project_name))
        project.owner_user_id = (auth or {}).get("uid")
        session.add(project)
        session.flush()
        open_project(session, auth, project)
    else:
        project = current_project(session, auth)

    people = {p.name.lower(): p for p in project_participants(session, project.id)}
    existing = {t.title.lower(): t for t in project_tasks(session, project.id)}
    created, pending_deps, errors = [], [], []
    for i, row in enumerate(reader, start=2):
        title = col(row, "title", "summary")
        if not title:
            errors.append(f"row {i}: missing title")
            continue
        owner_name = col(row, "owner", "assignee")
        owner = None
        if owner_name:
            owner = people.get(owner_name.lower())
            if not owner:
                owner = Participant(project_id=project.id, name=owner_name, role=col(row, "role") or "Team Member")
                session.add(owner)
                session.flush()
                people[owner_name.lower()] = owner
        raw_status = col(row, "status").lower()
        status = CSV_STATUS_MAP.get(raw_status)
        if status is None:
            errors.append(f"row {i}: unknown status '{raw_status}', using Pending")
            status = "Pending"
        task = Task(project_id=project.id, title=title, description=col(row, "description"),
                    owner_id=owner.id if owner else None, status=status, deadline=deadlines.normalize(col(row, "deadline", "due", "due date")),
                    blocker_reason=col(row, "blocker", "blocker reason") or None if status == "Blocked" else None,
                    progress_note="Imported from CSV")
        session.add(task)
        session.flush()
        existing[title.lower()] = task
        created.append(task)
        dep = col(row, "depends on", "depends_on", "dependency")
        if dep:
            pending_deps.append((task, dep, i))
    for task, dep, i in pending_deps:
        upstream = existing.get(dep.lower())
        if upstream and upstream.id != task.id:
            task.depends_on_task_id = upstream.id
        else:
            errors.append(f"row {i}: dependency '{dep}' not found")

    add_log(session, project.id, "AI_REASONING",
            f"Imported {len(created)} task(s) from CSV into \"{project.name}\"", {"errors": errors})
    session.commit()
    participants = project_participants(session, project.id)
    return {"project": {"id": project.id, "name": project.name}, "imported": len(created), "errors": errors,
            "participants": participants, "tasks": task_dicts(project_tasks(session, project.id), participants)}


@app.post("/api/webhooks/github")
async def github_webhook(request: Request):
    raw = await request.body()
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
    if secret:  # real GitHub deliveries are HMAC-signed; leave the secret unset to allow simulated commits
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
            raise HTTPException(401, "Invalid GitHub signature")
    if request.headers.get("X-GitHub-Event") == "ping":
        return {"status": "pong"}
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Body must be JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Body must be a JSON object")
    commits = _commits_from_payload(payload)
    if not commits:
        raise HTTPException(400, "No commits found (send {author, commit_message, repo} or a GitHub push payload)")
    auth = current_auth(request.headers.get("authorization"))
    return await asyncio.to_thread(process_commits, commits, auth)


@app.post("/api/ping-task/{task_id}")
def ping_task(task_id: int, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    notifications: list = []
    # A blocked task is waiting on someone else: remind the person who can unblock it, not the blocked owner
    action = send_blocker_reminder(session, task, notifications, "manual ping") if task.status == "Blocked" else None
    action = action or send_followup(session, task, notifications, "manual ping")
    if not action:
        raise HTTPException(400, "Task has no owner to ping")
    session.commit()
    return {"action": action, "notifications": notifications}


@app.post("/api/ping-all-pending")
def ping_all_pending(session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    project = current_project(session, auth)
    notifications: list = []
    actions = [send_followup(session, t, notifications, "manual ping-all")
               for t in project_tasks(session, project.id) if t.status == "Pending"]
    session.commit()
    return {"pinged": len([a for a in actions if a]), "actions": [a for a in actions if a], "notifications": notifications}


@app.post("/api/coordinate-slots")
def coordinate_slots(body: CoordinateSlots = CoordinateSlots(), session: Session = Depends(get_session),
                     auth: Optional[dict] = Depends(current_auth)):
    project = current_project(session, auth)
    participants = project_participants(session, project.id)
    by_key = {**{str(p.id): p for p in participants}, **{p.name.lower(): p for p in participants}}
    updated = []
    for key, avail in body.availability.items():
        p = by_key.get(str(key).strip().lower())
        if p and avail:
            p.availability = llm_engine.availability_for_storage(avail)
            updated.append({"participant": p.name, "availability": p.availability})
    plan = llm_engine.coordinate_team_slots([p.model_dump() for p in participants],
                                            task_dicts(project_tasks(session, project.id), participants))
    slot = plan["recommended_slot"]
    add_log(session, project.id, "AI_REASONING",
            f"Slot coordinator: {plan['purpose']} at {slot['label']} with {', '.join(plan['attendees'])}" if slot
            else "Slot coordinator: no overlapping slot found", {"plan": plan, "updated": updated})
    notifications: list = []
    if body.notify and slot:
        for p in participants:
            if p.name in plan["attendees"]:
                notify(session, project.id, p,
                       f"{plan['purpose']} booked: {slot['label']} with {', '.join(plan['attendees'])}. "
                       f"{plan['reason']}", f"Sent sync invite to {p.name}", notifications, log_type="FOLLOW_UP")
    session.commit()
    return {**plan, "updated_availability": updated, "notifications": notifications}


# ---------- Sign-in, invites & roles ----------

def _session_out(user: User, role: str, project: Optional[Project], participant: Optional[Participant]) -> dict:
    account_type = account_type_of(user) if role == "lead" else None
    payload = {"uid": user.id, "role": role, "prj": project.id if project else None,
               "pid": participant.id if participant else None, "acct": account_type}
    return {
        "token": make_token(payload),
        "role": role,
        "account_type": account_type,
        "user": {"id": user.id, "name": user.name, "email": user.email, "role": role,
                 "org_type": user.org_type, "account_type": account_type},
        "project": project,
        "participant": participant,
    }


def _find_user(session: Session, name: str, email: Optional[str]) -> Optional[User]:
    if email:
        return session.exec(select(User).where(func.lower(User.email) == email)).first()
    return session.exec(select(User).where(func.lower(User.name) == name.lower()).order_by(User.id)).first()


def _join_project(session: Session, user: User, project: Project, email: Optional[str], member_role: str) -> Participant:
    people = project_participants(session, project.id)
    participant = (next((p for p in people if p.user_id == user.id), None)
                   or next((p for p in people if email and (p.email or "").lower() == email), None)
                   or next((p for p in people if p.name.lower() == user.name.lower()), None))
    if not participant:
        participant = new_member(session, project.id, user.name, member_role)
        session.flush()
        add_log(session, project.id, "AI_REASONING", f"{user.name} joined \"{project.name}\" with invite code {project.invite_code}")
    participant.user_id = user.id
    participant.email = participant.email or email
    return participant


def _project_by_code(session: Session, code_or_link: str) -> Project:
    code = normalize_invite(code_or_link)
    project = session.exec(select(Project).where(Project.invite_code == code)).first()
    if not project:
        raise HTTPException(404, f"Invite code {code} wasn't found. Check it with your Team Lead.")
    return project


@app.post("/api/auth/login")
def login(body: LoginBody, session: Session = Depends(get_session)):
    """Self-join for NEW teammates with an invite code and their name.

    Existing members sign in with their personal PIN (/api/auth/join-member); Team Leads use email + password."""
    if body.role == "lead":
        raise HTTPException(400, "Team Leads sign in with email and password")
    name, email = body.name.strip(), (body.email or "").strip().lower() or None
    if not name:
        raise HTTPException(400, "Enter your name")
    if not body.invite_code:
        raise HTTPException(400, "Enter your project's invite code and your personal PIN")
    project = _project_by_code(session, body.invite_code)
    if any(p.name.lower() == name.lower() for p in project_participants(session, project.id)):
        raise HTTPException(409, f"{name} is already on this team. Sign in with your personal PIN from the Team Lead.")
    user = User(name=name, email=email, role="member")
    session.add(user)
    session.flush()
    participant = _join_project(session, user, project, email, body.member_role)
    session.commit()
    for obj in (user, project, participant):
        session.refresh(obj)
    return _session_out(user, "member", project, participant)


@app.post("/api/auth/register-lead", status_code=201)
def register_lead(body: RegisterLead, session: Session = Depends(get_session)):
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Enter a valid email address")
    existing = session.exec(select(User).where(func.lower(User.email) == email)).first()
    if existing and existing.password_hash:
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    org_type = {"company_lead": "corporate", "college_lead": "college"}.get(body.account_type or "", body.org_type)
    user = existing or User(email=email, name=body.name.strip())
    user.name, user.role, user.org_type = body.name.strip(), "lead", org_type
    user.password_hash = hash_password(body.password)
    session.add(user)
    session.commit()
    session.refresh(user)
    # A new Team Lead starts with an empty workspace and creates their first project
    return _session_out(user, "lead", None, None)


@app.post("/api/auth/login-lead")
def login_lead(body: LoginLead, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(func.lower(User.email) == body.email.strip().lower())).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect")
    project = current_project(session, {"uid": user.id, "role": "lead"}, required=False)
    return _session_out(user, "lead", project, None)


@app.post("/api/auth/join-member")
def join_member(body: JoinMember, session: Session = Depends(get_session)):
    """Member sign-in: project invite code + the personal PIN the Team Lead gave them."""
    project = _project_by_code(session, body.invite_code)
    pin = body.auth_pin.strip().upper()
    participant = session.exec(select(Participant).where(Participant.project_id == project.id,
                                                         func.upper(Participant.auth_pin) == pin)).first()
    if not participant:
        raise HTTPException(401, "That PIN doesn't match a member of this project. Check it with your Team Lead.")
    user = session.get(User, participant.user_id) if participant.user_id else None
    if not user:
        user = User(name=participant.name, email=participant.email, role="member")
        session.add(user)
        session.flush()
        participant.user_id = user.id
    session.commit()
    session.refresh(user)
    session.refresh(participant)
    return _session_out(user, "member", project, participant)


@app.post("/api/auth/demo")
def demo_login(body: DemoLogin, session: Session = Depends(get_session)):
    """One-click sign-in for judging: a demo Team Lead, or a member of the active project."""
    project = get_active_project(session)
    if body.role == "lead":
        company = body.account_type == "company_lead"
        name, email = ("Demo Company Lead", "company-lead@coordina.demo") if company else ("Demo Team Lead", "lead@coordina.demo")
        user = _find_user(session, name, email) or User(name=name, email=email)
        user.role, user.org_type = "lead", "corporate" if company else "college"
        session.add(user)
        session.flush()
        code = ENTERPRISE_INVITE_CODE if company else DEFAULT_INVITE_CODE
        project = session.exec(select(Project).where(Project.invite_code == code)).first() \
            or (seed_enterprise_project(session) if company else seed_default_project(session))
        open_project(session, {"uid": user.id, "role": "lead"}, project)
        session.commit()
        session.refresh(user)
        session.refresh(project)
        return _session_out(user, "lead", project, None)
    people = project_participants(session, project.id)
    if not people:
        raise HTTPException(400, "This project has no members yet. Invite someone first.")
    participant = next((p for p in people if p.name.lower() == "kabir"), people[min(1, len(people) - 1)])
    user = session.get(User, participant.user_id) if participant.user_id else None
    if not user:
        user = _find_user(session, participant.name, None) or User(name=participant.name, role="member")
        session.add(user)
        session.flush()
        participant.user_id = user.id
    session.commit()
    session.refresh(user)
    session.refresh(participant)
    return _session_out(user, "member", project, participant)


@app.get("/api/auth/me")
def auth_me(session: Session = Depends(get_session), auth: Optional[dict] = Depends(current_auth)):
    if not auth:
        raise HTTPException(401, "Not signed in")
    user = session.get(User, auth.get("uid"))
    if not user:
        raise HTTPException(401, "Account no longer exists")
    project = session.get(Project, auth["prj"]) if auth.get("prj") else None
    participant = session.get(Participant, auth["pid"]) if auth.get("pid") else None
    account_type = account_type_of(user) if auth.get("role") == "lead" else None
    return {"role": auth.get("role"), "account_type": account_type,
            "user": {"id": user.id, "name": user.name, "email": user.email, "role": auth.get("role"),
                     "org_type": user.org_type, "account_type": account_type},
            "project": project, "participant": participant}


@app.get("/api/invites/{code}")
def invite_info(code: str, session: Session = Depends(get_session)):
    project = session.exec(select(Project).where(Project.invite_code == normalize_invite(code))).first()
    if not project:
        raise HTTPException(404, "Invite code not found")
    bot = telegram_service.bot_username
    return {
        "project": {"id": project.id, "name": project.name, "description": project.description,
                    "workspace_type": project.workspace_type, "github_repo_url": project.github_repo_url,
                    "invite_code": project.invite_code},
        "members": [{"id": p.id, "name": p.name, "role": p.role, "telegram_linked": bool(p.telegram_chat_id)}
                    for p in project_participants(session, project.id)],
        "telegram_bot_url": f"https://t.me/{bot}" if bot else None,
    }


@app.get("/api/projects/{project_id}/invite")
def project_invite(project_id: int, session: Session = Depends(get_session), auth: Optional[dict] = Depends(current_auth)):
    project = require_visible(session, auth, project_id)
    if not project.invite_code:
        project.invite_code = new_invite_code(session, project.name)
        session.commit()
    bot = telegram_service.bot_username
    return {
        "invite_code": project.invite_code,
        "invite_path": f"/?invite={project.invite_code}",
        "telegram_bot_url": f"https://t.me/{bot}" if bot else None,
        "telegram_join": f"/join {project.invite_code} <Your name>",
    }


@app.patch("/api/projects/{project_id}")
def update_project(project_id: int, body: ProjectSettings, session: Session = Depends(get_session),
                   auth: Optional[dict] = Depends(require_lead)):
    project = require_visible(session, auth, project_id)
    ensure_workspace_allowed(session, auth, project.workspace_type)
    ensure_workspace_allowed(session, auth, body.workspace_type)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    session.commit()
    session.refresh(project)
    return project


# ---------- Task requests (teammate -> Team Lead) ----------

def request_dict(session: Session, r: TaskRequest) -> dict:
    who = lambda pid: (session.get(Participant, pid).name if pid and session.get(Participant, pid) else None)  # noqa: E731
    related = session.get(Task, r.related_task_id) if r.related_task_id else None
    return {**r.model_dump(), "requester_name": who(r.requester_id), "assignee_name": who(r.assignee_id),
            "related_task_title": related.title if related else None}


def _timing_text(r: TaskRequest, related: Optional[Task]) -> str:
    if r.relation == "before" and related:
        return f"needed before \"{related.title}\""
    if r.relation == "after" and related:
        return f"to start after \"{related.title}\""
    return "no particular order"


def clean_task_title(text: str) -> str:
    """'- to make spreadsheet' -> 'Make spreadsheet'."""
    t = re.sub(r"^[\s\-–—:,.;]+", "", text or "")
    t = re.sub(r"^(?:please|pls|plz|kindly|can you|could you|to)\s+", "", t, flags=re.I).strip(" -–—:,.;")
    return (t[:1].upper() + t[1:])[:160] if t else ""


def create_task_request(session: Session, requester: Participant, body: TaskRequestCreate) -> TaskRequest:
    """Validate and store a request; the Team Lead decides on the dashboard."""
    people = {p.id: p for p in project_participants(session, requester.project_id)}
    assignee_id = body.assignee_id or requester.id
    if assignee_id not in people:
        raise HTTPException(400, "Pick a teammate from this project")
    related = session.get(Task, body.related_task_id) if body.related_task_id else None
    if body.relation != "none":
        if not related or related.project_id != requester.project_id:
            raise HTTPException(400, "Choose which of your tasks it must happen before or after")
        if related.owner_id != requester.id:
            raise HTTPException(403, "You can only place a request before or after one of your own tasks")
    if body.deadline.strip() and not deadlines.parse_deadline(deadlines.normalize(body.deadline)):
        raise HTTPException(400, "Couldn't read that date. Pick a date and time.")
    # "to make spreadsheet till 18 Oct 12pm" -> title "Make spreadsheet", deadline 18 Oct 12:00
    title, spotted = deadlines.extract_deadline(body.title)
    deadline = deadlines.normalize(body.deadline) if body.deadline.strip() else spotted
    r = TaskRequest(project_id=requester.project_id, requester_id=requester.id, assignee_id=assignee_id,
                    title=clean_task_title(title) or body.title.strip(), description=body.description.strip(), relation=body.relation,
                    related_task_id=related.id if related and body.relation != "none" else None,
                    deadline=deadline)
    session.add(r)
    session.flush()
    add_log(session, requester.project_id, "AI_REASONING",
            f"Task request from {requester.name}: \"{r.title}\" for {people[assignee_id].name} "
            f"({_timing_text(r, related)}) - waiting for the Team Lead",
            {"request_id": r.id})
    session.commit()
    session.refresh(r)
    return r


@app.post("/api/task-requests", status_code=201)
def submit_task_request(body: TaskRequestCreate, session: Session = Depends(get_session),
                        auth: Optional[dict] = Depends(current_auth)):
    """A teammate asks the Team Lead to add a task (for a teammate or themselves)."""
    if auth and auth.get("role") == "member":
        requester = session.get(Participant, auth.get("pid"))
    else:
        requester = session.get(Participant, body.requester_id) if body.requester_id else None
    if not requester:
        raise HTTPException(400, "Sign in as a team member to request a task")
    if auth:
        require_visible(session, auth, requester.project_id)
    return request_dict(session, create_task_request(session, requester, body))


@app.get("/api/task-requests")
def list_task_requests(status: Optional[str] = None, session: Session = Depends(get_session),
                       auth: Optional[dict] = Depends(current_auth)):
    project = current_project(session, auth, required=False)
    if not project:
        return []
    q = select(TaskRequest).where(TaskRequest.project_id == project.id)
    if auth and auth.get("role") == "member":
        q = q.where(TaskRequest.requester_id == auth.get("pid"))
    if status:
        q = q.where(TaskRequest.status == status)
    return [request_dict(session, r) for r in session.exec(q.order_by(TaskRequest.id.desc())).all()]


@app.post("/api/task-requests/{request_id}/decide")
def decide_task_request(request_id: int, body: TaskRequestDecision, session: Session = Depends(get_session),
                        auth: Optional[dict] = Depends(require_lead)):
    """Team Lead approves (the task is created in the right place in the order) or rejects a request."""
    r = session.get(TaskRequest, request_id)
    if not r:
        raise HTTPException(404, "Request not found")
    require_visible(session, auth, r.project_id)
    if r.status != "pending":
        raise HTTPException(400, f"This request was already {r.status}")
    requester = session.get(Participant, r.requester_id)
    related = session.get(Task, r.related_task_id) if r.related_task_id else None
    notifications: list = []
    r.decided_at = utcnow()
    r.decision_note = body.note.strip()
    if body.action == "reject":
        r.status = "rejected"
        notify(session, r.project_id, requester,
               f"Your Team Lead didn't add \"{r.title}\"" + (f": {r.decision_note}" if r.decision_note else "."),
               f"Team Lead rejected {requester.name}'s request \"{r.title}\"", notifications,
               {"request_id": r.id}, log_type="FOLLOW_UP")
        session.commit()
        return {"request": request_dict(session, r), "task": None, "notifications": notifications}

    people = {pp.id: pp for pp in project_participants(session, r.project_id)}
    assignee = people.get(body.assignee_id or r.assignee_id)
    if not assignee:
        raise HTTPException(400, "Pick who should do this task")
    deadline = deadlines.normalize(body.deadline) if body.deadline is not None and body.deadline.strip() else r.deadline
    task = Task(project_id=r.project_id, title=r.title, description=r.description, owner_id=assignee.id, deadline=deadline,
                progress_note=f"Requested by {requester.name}")
    session.add(task)
    session.flush()
    if related and r.relation == "before":
        # slot the new task in front of the related one, keeping whatever it already waited for
        task.depends_on_task_id, related.depends_on_task_id = related.depends_on_task_id, task.id
        if related.status == "Pending" or related.status == "In Progress":
            related.updated_at = utcnow()
    elif related and r.relation == "after":
        task.depends_on_task_id = related.id
    r.status, r.assignee_id, r.created_task_id = "approved", assignee.id, task.id
    timing = _timing_text(r, related)
    due = f" Due {deadlines.humanize(deadline)}." if deadline else ""
    notify(session, r.project_id, assignee,
           f"Hi {assignee.name}, your Team Lead added a task for you, requested by {requester.name}: "
           f"\"{task.title}\" ({timing}).{due}" + (f"\nDetails: {task.description}" if task.description else ""),
           f"New task for {assignee.name} from {requester.name}'s request: \"{task.title}\"", notifications,
           {"task_id": task.id, "request_id": r.id}, log_type="FOLLOW_UP")
    if requester.id != assignee.id:
        notify(session, r.project_id, requester,
               f"Your request was approved: {assignee.name} will do \"{task.title}\" ({timing}).{due}",
               f"Told {requester.name} their request was approved", notifications, {"request_id": r.id}, log_type="FOLLOW_UP")
    session.commit()
    session.refresh(task)
    return {"request": request_dict(session, r), "task": task.model_dump(), "notifications": notifications}


def handle_telegram_request(participant_id: int, assignee_id: int, text: str, relation: str,
                            related_task_id: Optional[int]) -> dict:
    """Hook for telegram_service: /request from a verified member."""
    with Session(engine) as session:
        requester = session.get(Participant, participant_id)
        title, _, details = text.partition(" - ")
        r = create_task_request(session, requester, TaskRequestCreate(
            title=title.strip()[:160] or text[:160], description=details.strip(), assignee_id=assignee_id,
            relation=relation, related_task_id=related_task_id))
        return request_dict(session, r)


# ---------- Deadlines (Team Lead) ----------

@app.post("/api/projects/{project_id}/deadlines")
def set_project_deadlines(project_id: int, body: ProjectDeadlines, session: Session = Depends(get_session),
                          auth: Optional[dict] = Depends(require_lead)):
    """Set the project's final deadline and let the coordinator spread task deadlines along the dependency chain."""
    project = require_visible(session, auth, project_id)
    changed = schedule_project(session, project, body.start_at, body.due_at, body.distribute)
    notifications: list = []
    if body.notify:
        for owner_id in {t.owner_id for t in changed if t.owner_id}:
            owner = session.get(Participant, owner_id)
            mine = [t for t in changed if t.owner_id == owner_id]
            listing = "\n".join(f"- {t.title}: due {deadlines.humanize(t.deadline)}" for t in mine)
            notify(session, project.id, owner, f"Hi {owner.name}, your Team Lead updated the timeline for \"{project.name}\" "
                   f"(final deadline {deadlines.humanize(project.due_at)}):\n{listing}",
                   f"Told {owner.name} their new deadline{'s' if len(mine) != 1 else ''}", notifications,
                   {"participant_id": owner.id}, log_type="FOLLOW_UP")
    add_log(session, project.id, "AI_REASONING",
            f"Timeline set: {deadlines.humanize(project.start_at)} -> {deadlines.humanize(project.due_at)}"
            + (f", deadlines spread over {len(changed)} task(s)" if body.distribute else ""),
            {"start_at": project.start_at, "due_at": project.due_at,
             "tasks": {t.id: t.deadline for t in changed}})
    session.commit()
    session.refresh(project)
    return {"project": project, "updated": [{"id": t.id, "title": t.title, "deadline": t.deadline} for t in changed],
            "notifications": notifications}


@app.patch("/api/tasks/{task_id}/deadline")
def set_task_deadline(task_id: int, body: TaskDeadline, session: Session = Depends(get_session),
                      auth: Optional[dict] = Depends(require_lead)):
    """Team Lead edits one task's deadline; the owner hears about it on Telegram."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_visible(session, auth, task.project_id)
    new = deadlines.normalize(body.deadline) if body.deadline.strip() else ""
    if body.deadline.strip() and not deadlines.parse_deadline(new):
        raise HTTPException(400, "Couldn't read that date. Pick a date and time.")
    old, task.deadline, task.updated_at = task.deadline, new, utcnow()
    notifications: list = []
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    if owner and new != old:
        notify(session, task.project_id, owner,
               f"Hi {owner.name}, your Team Lead set the deadline for \"{task.title}\" to {deadlines.humanize(new)}."
               if new else f"Hi {owner.name}, your Team Lead removed the deadline for \"{task.title}\".",
               f"Deadline for \"{task.title}\": {deadlines.humanize(old) or 'none'} -> {deadlines.humanize(new) or 'none'}",
               notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
    session.commit()
    session.refresh(task)
    return {"task": task.model_dump(), "notifications": notifications}


# ---------- Team Lead controls ----------

@app.post("/api/tasks/{task_id}/override")
def override_task(task_id: int, body: TaskOverride, session: Session = Depends(get_session),
                  auth: Optional[dict] = Depends(require_lead)):
    """Team Lead sets a task's status directly, bypassing the AI and dependency checks."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_visible(session, auth, task.project_id)
    previous = task.status
    task.status = body.status
    task.lead_approved = body.status == "Complete"  # the lead vouches for a completion they set
    if body.status != "Blocked":
        task.blocker_reason = None
        task.blocked_by_participant_id = None
    task.progress_note = (body.note or "").strip() or f"Marked {body.status} by the Team Lead"
    task.updated_at = utcnow()
    actions = [{"type": "TASK_UPDATED", "task_id": task.id, "from": previous, "to": task.status, "override": True}]
    notifications: list = []
    add_log(session, task.project_id, "AI_REASONING",
            f"[Team Lead override] \"{task.title}\" {previous} -> {task.status}", {"task_id": task.id, "note": body.note})
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    if owner:
        notify(session, task.project_id, owner, f"Your Team Lead marked \"{task.title}\" as {task.status}.",
               f"Told {owner.name} about the Team Lead update", notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
    if task.status == "Complete":
        notify_handoffs(session, task, "the Team Lead", notifications, actions)
    session.commit()
    session.refresh(task)
    return {"task": task.model_dump(), "actions": actions, "notifications": notifications}


@app.post("/api/tasks/{task_id}/review")
def review_task(task_id: int, body: TaskReview, session: Session = Depends(get_session),
                auth: Optional[dict] = Depends(require_lead)):
    """Team Lead reviews a completion: approve it (vouch for the work) or reopen it with a reason."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    require_visible(session, auth, task.project_id)
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    notifications: list = []
    if body.action == "approve":
        if task.status != "Complete":
            raise HTTPException(400, "Only a completed task can be approved")
        task.lead_approved = True
        text = f"Your Team Lead reviewed \"{task.title}\" and approved it. Great work!"
        summary = f"Team Lead approved \"{task.title}\""
    else:
        reason = body.reason.strip()
        if not reason:
            raise HTTPException(400, "Say what still needs doing, so the teammate knows")
        task.status, task.lead_approved = "In Progress", False
        task.progress_note = f"Reopened by the Team Lead: {reason}"
        text = f"Your Team Lead reopened \"{task.title}\": {reason}. Reply here when it's fixed."
        summary = f"Team Lead reopened \"{task.title}\": {reason}"
    task.updated_at = utcnow()
    if owner:
        notify(session, task.project_id, owner, text, summary, notifications, {"task_id": task.id}, log_type="FOLLOW_UP")
    else:
        add_log(session, task.project_id, "AI_REASONING", summary, {"task_id": task.id})
    session.commit()
    session.refresh(task)
    return {"task": {**task.model_dump(), "proof": proof.proof_level(task)}, "notifications": notifications}


@app.post("/api/take-updates")
def take_updates(body: TakeUpdates = TakeUpdates(), session: Session = Depends(get_session),
                 auth: Optional[dict] = Depends(require_lead)):
    """AI Reminder: ask everyone with open work for a status update (Telegram, plus Slack when enabled)."""
    project = require_visible(session, auth, body.project_id) if body.project_id else current_project(session, auth)
    open_tasks = [t for t in project_tasks(session, project.id) if t.status != "Complete"]
    notifications: list = []
    asked = []
    for p in project_participants(session, project.id):
        mine = [t for t in open_tasks if t.owner_id == p.id]
        if not mine:
            continue
        listing = "\n".join(f"- {t.title} ({t.status})" for t in mine)
        notify(session, project.id, p,
               f"Hi {p.name}, quick status check for \"{project.name}\":\n{listing}\n"
               f"Reply with what's done, what's next, or what you're blocked on.",
               f"Asked {p.name} for an update on {len(mine)} task{'s' if len(mine) != 1 else ''}", notifications,
               {"participant_id": p.id}, log_type="FOLLOW_UP")
        asked.append(p.name)
    add_log(session, project.id, "AI_REASONING", f"AI Reminder: requested updates from {len(asked)} member(s)", {"asked": asked})
    session.commit()
    return {"asked": asked, "notifications": notifications}


# ---------- Integrations ----------

@app.get("/api/integrations")
def get_integrations():
    slack = slack_settings()
    return {"telegram": {"mode": telegram_service.mode, "bot_username": telegram_service.bot_username,
                         "reason": telegram_service.reason},
            "slack": {"enabled": slack["enabled"], "configured": bool(slack["webhook_url"])}}


@app.post("/api/integrations")
def set_integrations(body: IntegrationSettings, session: Session = Depends(get_session),
                     auth: Optional[dict] = Depends(require_lead)):
    if body.slack_enabled and caller_account_type(session, auth) == "college_lead":
        raise HTTPException(403, "Slack integration requires a Company Team Lead account")
    if body.slack_enabled and not (body.slack_webhook_url or slack_settings()["webhook_url"]):
        raise HTTPException(400, "Paste a Slack incoming webhook URL to enable Slack")
    if body.slack_webhook_url and not body.slack_webhook_url.startswith("https://hooks.slack.com/"):
        raise HTTPException(400, "Slack webhook URLs start with https://hooks.slack.com/")
    save_slack_settings(body.slack_enabled, body.slack_webhook_url)
    return get_integrations()


# ---------- Verification: GitHub commits (code) and deliverable links (everything else) ----------

def _complete_unless_blocked_upstream(session: Session, task: Task) -> bool:
    """Mark Complete unless the task it depends on is Blocked (or strict lock and not Complete). Returns True if done."""
    project = session.get(Project, task.project_id)
    up = session.get(Task, task.depends_on_task_id) if task.depends_on_task_id else None
    if up and (up.status == "Blocked" or (project.strict_dependencies and up.status != "Complete")):
        task.status = "In Progress"
        return False
    task.status = "Complete"
    task.blocker_reason, task.blocked_by_participant_id = None, None
    return True


@app.post("/api/projects/{project_id}/sync-github")
def sync_github(project_id: int, session: Session = Depends(get_session), auth: Optional[dict] = Depends(require_lead)):
    """Match recent commits on the project's repo to tasks and record them as verified progress."""
    project = require_visible(session, auth, project_id)
    if not project.github_repo_url:
        raise HTTPException(400, "This project has no GitHub repository linked")
    try:
        commits = github_repo.recent_commits(project.github_repo_url)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))

    people = project_participants(session, project.id)
    tasks = [t for t in project_tasks(session, project.id) if t.task_type != "design_docs"]
    matched, unmatched, touched = [], [], set()
    actions: list = []
    notifications: list = []
    for c in commits:  # newest first: each task records its most recent matching commit
        first_line = c["message"].strip().splitlines()[0] if c["message"].strip() else ""
        sha7 = c["sha"][:7]
        author = _match_author(people, c["author_login"], c["author_name"])
        pool = [t for t in tasks if author and t.owner_id == author.id] or tasks
        scores = llm_engine.task_match_scores(first_line, [{"title": t.title, "description": t.description} for t in pool])
        best = max(scores, default=0)
        task = pool[scores.index(best)] if best >= 2 and scores.count(best) == 1 else None
        if not task:
            unmatched.append({"sha": sha7, "message": first_line, "author": author.name if author else c["author_login"] or c["author_name"]})
            continue
        if task.id in touched or (task.last_commit_info or "").startswith(sha7):
            continue
        touched.add(task.id)
        previous = task.status
        task.github_verified = True
        task.last_commit_info = f"{sha7} {first_line[:80]} · {c['author_login'] or c['author_name']} · {c['date'][:10]}"
        task.progress_note = f"Commit {sha7}: {first_line[:120]}"
        msg = llm_engine._norm_text(first_line)
        if llm_engine.DONE_RE.search(msg) and not llm_engine.BLOCKER_RE.search(msg):
            if _complete_unless_blocked_upstream(session, task):
                notify_handoffs(session, task, author.name if author else "a commit", notifications, actions)
        elif task.status == "Pending":
            task.status = "In Progress"
        task.updated_at = utcnow()
        matched.append({"task_id": task.id, "task": task.title, "sha": sha7, "message": first_line,
                        "author": author.name if author else c["author_login"], "from": previous, "to": task.status})
    add_log(session, project.id, "AI_REASONING",
            f"GitHub sync: {len(commits)} recent commit(s) on {github_repo.parse_repo_url(project.github_repo_url)[1]}, "
            f"{len(matched)} matched to tasks", {"matched": matched, "unmatched": unmatched[:15]})
    session.commit()
    return {"repo": project.github_repo_url, "commits": len(commits), "matched": matched, "unmatched": unmatched[:15],
            "notifications": notifications}


@app.post("/api/tasks/{task_id}/deliverable")
def submit_deliverable(task_id: int, body: Deliverable, session: Session = Depends(get_session),
                       auth: Optional[dict] = Depends(current_auth)):
    """Verify non-code work (slides, designs, research, test reports) with a link; completes the task."""
    task = session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    owner = session.get(Participant, task.owner_id) if task.owner_id else None
    if owner:
        guard_member(session, auth, owner, task.id)
    url = body.url.strip()
    if not re.match(r"https?://", url):
        raise HTTPException(400, "Paste a full link starting with http:// or https://")
    if proof.GITHUB_WORK_LINK.match(url):
        info, why = proof.verify_github_link((session.get(Project, task.project_id)).github_repo_url, url)
        if not info:
            raise HTTPException(400, f"Couldn't verify that link: {why}")
        task.github_verified, task.last_commit_info = True, proof.commit_summary(info)
    else:
        ok, why = proof.check_link(url)
        if ok is False:
            raise HTTPException(400, f"That link can't be used as proof: {why}. Check it opens and try again.")
    previous = task.status
    task.deliverable_url = url
    task.progress_note = (body.note or "").strip() or f"Deliverable submitted: {url}"
    actions: list = []
    notifications: list = []
    done = _complete_unless_blocked_upstream(session, task)
    if done:
        notify_handoffs(session, task, owner.name if owner else "the team", notifications, actions)
    task.updated_at = utcnow()
    add_log(session, task.project_id, "AI_REASONING",
            f"Deliverable for \"{task.title}\" ({owner.name if owner else 'unassigned'}): {url} -> {task.status}",
            {"task_id": task.id, "url": url, "from": previous})
    session.commit()
    session.refresh(task)
    return {"task": task.model_dump(), "completed": done, "actions": actions, "notifications": notifications}
