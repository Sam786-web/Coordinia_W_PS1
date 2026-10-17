import json
from typing import Optional

from sqlmodel import Session, delete, select

from database import engine, init_db
import deadlines
from models import AgentLog, Participant, Project, Task, utcnow

DEFAULT_PROJECT_ID = 1
DEFAULT_PROJECT_NAME = "College Capstone Payment System"
DEFAULT_PROJECT_DESCRIPTION = (
    "Final-year capstone: a student payment system with a pitch deck, "
    "payment API connection and end-to-end integration."
)
DEFAULT_INVITE_CODE = "CAPSTONE-2026"
ENTERPRISE_INVITE_CODE = "FINTECH-2026"
ENTERPRISE_PROJECT_NAME = "FinTech Production Release v2.4"
ENTERPRISE_PROJECT_DESCRIPTION = (
    "Industry sprint: ship the v2.4 payments release to production - Stripe webhooks, "
    "AWS KMS key management, load & penetration testing, and client UAT sign-off."
)


def _wipe_project(session: Session, project_id: int) -> dict[str, tuple]:
    """Delete a project's logs/tasks/participants. Returns identity by name (Telegram link, PIN, verification)
    so phones stay linked and handed-out PINs keep working after a demo reset."""
    links = {
        p.name.lower(): {"telegram_username": p.telegram_username, "telegram_chat_id": p.telegram_chat_id,
                         "auth_pin": p.auth_pin, "is_verified": p.is_verified}
        for p in session.exec(select(Participant).where(Participant.project_id == project_id)).all()
    }
    # Tasks first: they reference participants and other tasks
    session.exec(delete(AgentLog).where(AgentLog.project_id == project_id))
    session.exec(delete(Task).where(Task.project_id == project_id))
    session.exec(delete(Participant).where(Participant.project_id == project_id))
    return links


def _make_people(session: Session, project_id: int, people: list[tuple[str, str]], links: dict) -> list[Participant]:
    out = []
    for name, role in people:
        out.append(Participant(project_id=project_id, name=name, role=role, **links.get(name.lower(), {})))
    session.add_all(out)
    session.flush()
    return out


def _activate(session: Session, project_id: int) -> None:
    for p in session.exec(select(Project)).all():
        p.is_active = p.id == project_id


def seed_default_project(session: Session) -> Project:
    """(Re)create the default demo project in its initial state and make it active."""
    project = session.get(Project, DEFAULT_PROJECT_ID)
    links: dict = {}
    if project:
        links = _wipe_project(session, DEFAULT_PROJECT_ID)
        project.name = DEFAULT_PROJECT_NAME
        project.description = DEFAULT_PROJECT_DESCRIPTION
    else:
        project = Project(
            id=DEFAULT_PROJECT_ID,
            name=DEFAULT_PROJECT_NAME,
            description=DEFAULT_PROJECT_DESCRIPTION,
        )
        session.add(project)
    project.workspace_type = "college"
    project.invite_code = DEFAULT_INVITE_CODE
    project.strict_dependencies = False
    session.flush()
    _activate(session, DEFAULT_PROJECT_ID)

    aanya, kabir, meera = _make_people(session, project.id, [
        ("Aanya", "Pitch & Presentation Lead"),
        ("Kabir", "Backend Developer"),
        ("Meera", "Integration Engineer"),
    ], links)

    now = utcnow()
    pitch = Task(
        project_id=project.id,
        title="Finalize Pitch Deck Slides",
        description="Polish the final pitch deck for the capstone review panel.",
        owner_id=aanya.id,
        status="Complete",
        progress_note="All slides finalized and shared with the team.",
        deadline=deadlines.normalize("Today 6:00 PM"),
        updated_at=now,
    )
    payment_api = Task(
        project_id=project.id,
        title="Connect the payment API",
        description="Integrate the payment gateway sandbox API and expose a charge endpoint.",
        owner_id=kabir.id,
        status="Pending",
        progress_note="Not started yet.",
        deadline=deadlines.normalize("Today 8:00 PM"),
        updated_at=now,
    )
    session.add_all([pitch, payment_api])
    session.flush()

    session.add(
        Task(
            project_id=project.id,
            title="End-to-End Payment Integration",
            description="Wire the checkout UI to the payment API and test the full flow.",
            owner_id=meera.id,
            status="In Progress",
            progress_note="Checkout UI ready; waiting on the payment API to test end-to-end.",
            depends_on_task_id=payment_api.id,
            deadline=deadlines.normalize("Tomorrow 10:00 AM"),
            updated_at=now,
        )
    )
    session.add(
        AgentLog(
            project_id=project.id,
            log_type="AI_REASONING",
            summary="Coordinator initialized: monitoring 3 tasks across 3 teammates.",
            details_json=json.dumps({
                "participants": ["Aanya", "Kabir", "Meera"],
                "watch": "Meera's integration depends on Kabir's payment API task.",
            }),
            timestamp=now,
        )
    )
    session.commit()
    session.refresh(project)
    return project


def seed_enterprise_project(session: Session) -> Project:
    """(Re)create the enterprise FinTech sprint preset and make it active."""
    project: Optional[Project] = session.exec(select(Project).where(Project.name == ENTERPRISE_PROJECT_NAME)).first()
    links: dict = {}
    if project:
        links = _wipe_project(session, project.id)
        project.description = ENTERPRISE_PROJECT_DESCRIPTION
    else:
        project = Project(name=ENTERPRISE_PROJECT_NAME, description=ENTERPRISE_PROJECT_DESCRIPTION)
        session.add(project)
    project.workspace_type = "company"
    project.invite_code = ENTERPRISE_INVITE_CODE
    project.strict_dependencies = False
    session.flush()
    _activate(session, project.id)

    rohan, priya, vikram, neha = _make_people(session, project.id, [
        ("Rohan", "Senior Backend Eng"),
        ("Priya", "DevOps & Cloud Sec"),
        ("Vikram", "QA Automation Lead"),
        ("Neha", "Engineering Manager"),
    ], links)

    now = utcnow()
    webhook = Task(
        project_id=project.id, title="Stripe Production Webhook API", owner_id=rohan.id, status="Pending",
        description="Implement and verify signed Stripe webhooks (payment_intent.*) against production.",
        progress_note="Not started yet.", deadline=deadlines.normalize("Today 5:00 PM"), updated_at=now,
    )
    kms = Task(
        project_id=project.id, title="Provision AWS KMS & Prod Keys", owner_id=priya.id, status="In Progress",
        description="Create the KMS key ring, rotate production secrets and grant the payments service access.",
        progress_note="KMS key ring created; rotating production secrets.", deadline=deadlines.normalize("Today 4:00 PM"), updated_at=now,
    )
    session.add_all([webhook, kms])
    session.flush()
    load_test = Task(
        project_id=project.id, title="Run Load & Penetration Testing", owner_id=vikram.id, status="Pending",
        description="k6 load test at 2x peak traffic plus OWASP penetration test of the payment endpoints.",
        progress_note="Test plan ready; waiting on the webhook API.", depends_on_task_id=webhook.id,
        deadline=deadlines.normalize("Tomorrow 11:00 AM"), updated_at=now,
    )
    session.add(load_test)
    session.flush()
    session.add(Task(
        project_id=project.id, title="Client UAT & Security Sign-Off", owner_id=neha.id, status="Pending",
        description="Run client user-acceptance testing and collect security sign-off for the release.",
        progress_note="UAT scheduled with the client.", depends_on_task_id=load_test.id,
        deadline=deadlines.normalize("Tomorrow 4:00 PM"), updated_at=now,
    ))
    session.add(AgentLog(
        project_id=project.id, log_type="AI_REASONING", timestamp=now,
        summary="Enterprise sprint loaded: monitoring 4 tasks across 4 engineers.",
        details_json=json.dumps({
            "critical_path": ["Stripe Production Webhook API", "Run Load & Penetration Testing",
                              "Client UAT & Security Sign-Off"],
            "watch": "Rohan's webhook likely needs Priya's production KMS keys.",
        }),
    ))
    session.commit()
    session.refresh(project)
    return project


def main() -> None:
    init_db()
    with Session(engine) as session:
        project = seed_default_project(session)
        print(f"Seeded project #{project.id}: {project.name}")


if __name__ == "__main__":
    main()
