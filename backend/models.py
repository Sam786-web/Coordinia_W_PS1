import re
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import event
from sqlmodel import Field, SQLModel, select

TASK_STATUSES = ("Pending", "In Progress", "Blocked", "Complete")
LOG_TYPES = ("FOLLOW_UP", "REPLY", "AI_REASONING", "BLOCKER_ALERT", "AUTO_RELAY")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


WORKSPACE_TYPES = ("college", "company")
USER_ROLES = ("lead", "member")


class User(SQLModel, table=True):
    """A person who signs in to the dashboard or mobile app. Leads own projects; members join via invite."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: Optional[str] = Field(default=None, index=True)
    role: str = "member"  # one of USER_ROLES
    created_at: datetime = Field(default_factory=utcnow)
    password_hash: Optional[str] = None  # Team Leads who registered with a password
    org_type: str = "college"  # "college" | "corporate"
    current_project_id: Optional[int] = None  # the project this Team Lead is looking at (each lead has their own)


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: str = ""
    is_active: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    invite_code: Optional[str] = Field(default=None, index=True)
    workspace_type: str = "college"  # one of WORKSPACE_TYPES
    github_repo_url: Optional[str] = None
    strict_dependencies: bool = False  # True: completion is refused while ANY upstream task is unfinished
    owner_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    # Project timeline set by the Team Lead (local ISO datetimes); task deadlines are spread between them
    start_at: Optional[str] = None
    due_at: Optional[str] = None


class Participant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    role: str = ""
    telegram_username: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    availability: str = "Available"
    email: Optional[str] = None
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    auth_pin: Optional[str] = Field(default=None, index=True)  # unique 6-digit personal PIN, e.g. "482913" (set on insert)
    is_verified: bool = False  # True once a Telegram account proved this PIN
    telegram_linked_at: Optional[datetime] = None  # when /auth last picked this member: the chat's active project


TASK_TYPES = ("github_code", "design_docs", "general")


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    title: str
    description: str = ""
    owner_id: Optional[int] = Field(default=None, foreign_key="participant.id")
    status: str = "Pending"  # one of TASK_STATUSES
    progress_note: str = ""
    blocker_reason: Optional[str] = None
    blocked_by_participant_id: Optional[int] = Field(default=None, foreign_key="participant.id")
    depends_on_task_id: Optional[int] = Field(default=None, foreign_key="task.id")
    deadline: str = ""
    updated_at: datetime = Field(default_factory=utcnow)
    task_type: Optional[str] = None  # one of TASK_TYPES (classified from the title on insert)
    github_verified: bool = False
    last_commit_info: Optional[str] = None  # "abc1234 Fix login form · mital2006 · 2026-10-16"
    deliverable_url: Optional[str] = None  # proof for non-code work (slides, designs, docs)
    lead_approved: bool = False  # the Team Lead reviewed a completion and vouched for it


class TaskRequest(SQLModel, table=True):
    """A teammate asks the Team Lead to add a task (usually for someone else), placed before / after one of theirs."""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    requester_id: int = Field(foreign_key="participant.id")
    assignee_id: Optional[int] = Field(default=None, foreign_key="participant.id")
    title: str
    description: str = ""
    relation: str = "none"  # "before": must be done before related_task | "after": starts after it | "none"
    related_task_id: Optional[int] = Field(default=None, foreign_key="task.id")
    deadline: str = ""
    status: str = "pending"  # pending | approved | rejected
    decision_note: str = ""
    created_task_id: Optional[int] = Field(default=None, foreign_key="task.id")
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: Optional[datetime] = None


class AgentLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    timestamp: datetime = Field(default_factory=utcnow)
    log_type: str  # one of LOG_TYPES
    summary: str
    details_json: str = "{}"


# ---------- Automatic fields on insert (every code path gets them: seeds, API, CSV, Telegram) ----------

PIN_DIGITS = 6


def is_numeric_pin(pin: Optional[str]) -> bool:
    return bool(pin) and pin.isdigit() and len(pin) == PIN_DIGITS


def new_auth_pin(connection, name: str = "") -> str:
    """Unique 6-digit personal PIN such as '482913', unique across every project so it alone identifies a member.

    `connection` is a Connection or Session; `name` is kept for call-site compatibility."""
    while True:
        pin = str(secrets.randbelow(900_000) + 100_000)
        if not connection.execute(select(Participant.auth_pin).where(Participant.auth_pin == pin)).first():
            return pin


@event.listens_for(Participant, "before_insert")
def _assign_auth_pin(mapper, connection, target):
    if not target.auth_pin:
        target.auth_pin = new_auth_pin(connection, target.name)


_DOC_WORDS = re.compile(r"\b(slides?|deck|pitch|presentation|design|figma|mock-?ups?|wireframes?|ui/ux|ux|docs?|"
                        r"documentation|report|research|survey|readme|poster|video|demo script|content|uat|sign-?off)\b", re.I)
_CODE_WORDS = re.compile(r"\b(api|code|build|implement|integrat\w*|backend|frontend|database|schema|endpoint|webhook|"
                         r"deploy\w*|fix|bug|refactor|feature|module|server|app|script|model|auth\w*|login|persist\w*|"
                         r"wire|connect|test(?:s|ing)? automation|ci|pipeline|kms|keys)\b", re.I)


def classify_task_type(title: str, description: str = "") -> str:
    text = f"{title} {description}"
    if _DOC_WORDS.search(title or ""):
        return "design_docs"
    if _CODE_WORDS.search(text):
        return "github_code"
    if _DOC_WORDS.search(text):
        return "design_docs"
    return "general"


@event.listens_for(Task, "before_insert")
def _assign_task_type(mapper, connection, target):
    if not target.task_type:
        target.task_type = classify_task_type(target.title, target.description)
