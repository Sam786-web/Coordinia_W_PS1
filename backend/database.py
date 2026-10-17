import os
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session, SQLModel, create_engine, select

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./coordinator.db")
if DATABASE_URL.startswith("sqlite:///./"):
    # Resolve relative SQLite paths against /backend, not the current working dir
    DATABASE_URL = "sqlite:///" + (BASE_DIR / DATABASE_URL[len("sqlite:///./"):]).as_posix()

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


# Columns added after the first release. SQLite's create_all never alters existing tables,
# so add any missing column in place and keep existing data.
_ADDED_COLUMNS = {
    "project": {
        "invite_code": "VARCHAR",
        "workspace_type": "VARCHAR NOT NULL DEFAULT 'college'",
        "github_repo_url": "VARCHAR",
        "strict_dependencies": "BOOLEAN NOT NULL DEFAULT 0",
        "owner_user_id": "INTEGER REFERENCES user(id)",
        "start_at": "VARCHAR",
        "due_at": "VARCHAR",
    },
    "participant": {
        "email": "VARCHAR",
        "user_id": "INTEGER REFERENCES user(id)",
        "auth_pin": "VARCHAR",
        "is_verified": "BOOLEAN NOT NULL DEFAULT 0",
        "telegram_linked_at": "DATETIME",
    },
    "task": {
        "task_type": "VARCHAR",
        "github_verified": "BOOLEAN NOT NULL DEFAULT 0",
        "last_commit_info": "VARCHAR",
        "deliverable_url": "VARCHAR",
        "lead_approved": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "user": {
        "password_hash": "VARCHAR",
        "org_type": "VARCHAR NOT NULL DEFAULT 'college'",
        "current_project_id": "INTEGER",
    },
}


def _migrate() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _backfill() -> None:
    """Give rows created before PINs / task types existed their values (the insert listeners only cover new rows)."""
    from models import Participant, Project, Task, User, classify_task_type, is_numeric_pin, new_auth_pin

    with Session(engine) as session:
        # Telegram links copied by name into other projects were never PIN-verified there: drop them
        verified_chats = {p.telegram_chat_id for p in session.exec(select(Participant).where(
            Participant.telegram_chat_id != None, Participant.is_verified == True)).all()}  # noqa: E711,E712
        for p in session.exec(select(Participant).where(Participant.telegram_chat_id != None,  # noqa: E711
                                                        Participant.is_verified == False)).all():  # noqa: E712
            if p.telegram_chat_id in verified_chats:
                p.telegram_chat_id = None
        # A Team Lead works in exactly one workspace, so their projects follow their account type
        for project in session.exec(select(Project).where(Project.owner_user_id != None)).all():  # noqa: E711
            owner = session.get(User, project.owner_user_id)
            if owner and owner.role == "lead":
                workspace = "company" if owner.org_type == "corporate" else "college"
                if project.workspace_type != workspace:
                    project.workspace_type = workspace
                    session.add(project)
        # Missing PINs, and the older letter PINs ("KA-4829"), become unique 6-digit numbers
        for p in session.exec(select(Participant)).all():
            if is_numeric_pin(p.auth_pin):
                continue
            p.auth_pin = new_auth_pin(session.connection(), p.name)
            session.add(p)
            session.flush()
        # Tasks created from teammate requests before titles were cleaned: "- send credentials", "to make X till 18 Oct 12pm"
        import re as _re
        import deadlines as _dl
        for t in session.exec(select(Task).where(Task.progress_note.like("Requested by %"))).all():
            title, spotted = _dl.extract_deadline(t.title)
            title = _re.sub(r"^[\s\-–—:,.;]+", "", title)
            title = _re.sub(r"^(?:please|pls|plz|kindly|to)\s+", "", title, flags=_re.I).strip(" -–—:,.;")
            title = title[:1].upper() + title[1:] if title else t.title
            if title != t.title or (spotted and not t.deadline):
                t.title = title
                t.deadline = t.deadline or spotted
                session.add(t)
        # Completions the Team Lead set before approvals were recorded count as approved
        for t in session.exec(select(Task).where(Task.status == "Complete", Task.lead_approved == False,  # noqa: E712
                                                 Task.progress_note.like("Marked Complete by the Team Lead%"))).all():
            t.lead_approved = True
            session.add(t)
        # Free-text deadlines ("Today 8:00 PM") become real dates, so they can be edited and followed up
        import deadlines
        for t in session.exec(select(Task).where(Task.deadline != "")).all():
            iso = deadlines.normalize(t.deadline)
            if iso != t.deadline:
                t.deadline = iso
                session.add(t)
        for t in session.exec(select(Task).where(Task.task_type == None)).all():  # noqa: E711
            t.task_type = classify_task_type(t.title, t.description)
            session.add(t)
        session.commit()


def init_db() -> None:
    import models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)
    _migrate()
    _backfill()


def get_session():
    with Session(engine) as session:
        yield session
