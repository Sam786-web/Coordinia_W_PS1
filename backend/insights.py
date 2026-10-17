"""Milestones and risks for the progress report.

Milestones: tasks grouped into phases along the dependency chain (phase 1 = no dependency, phase 2 = waits on
phase 1, ...). Each phase is due when its last task is due, and is Done / On track / At risk / Late.

Risks: a ranked list of what could sink the project, each with a severity (critical / serious / warning),
the affected task or person, and a concrete next step.
"""
from datetime import datetime, timedelta
from typing import Optional

import deadlines

def work_order(tasks: list) -> list:
    """Tasks in work order: a task comes right before the work that waits for it; otherwise creation order."""
    by_id = {t.id: t for t in tasks}
    out, placed, visiting = [], set(), set()

    def place(t):
        if t.id in placed or t.id in visiting:  # visiting guards against a dependency loop
            return
        visiting.add(t.id)
        up = by_id.get(t.depends_on_task_id)
        if up is not None:
            place(up)
        visiting.discard(t.id)
        placed.add(t.id)
        out.append(t)

    for t in tasks:
        place(t)
    return out


SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2}
PHASE_NAMES = ["Foundation", "Build", "Integrate", "Test & polish", "Ship"]


def _depth(tasks: list[dict]) -> dict[int, int]:
    by_id = {t["id"]: t for t in tasks}
    depth: dict[int, int] = {}

    def walk(t, seen=()):
        if t["id"] in depth:
            return depth[t["id"]]
        up = by_id.get(t.get("depends_on_task_id"))
        depth[t["id"]] = 0 if (not up or t["id"] in seen) else walk(up, seen + (t["id"],)) + 1
        return depth[t["id"]]

    for t in tasks:
        walk(t)
    return depth


def milestones(tasks: list[dict], now: Optional[datetime] = None) -> list[dict]:
    now = now or datetime.now()
    depth = _depth(tasks)
    phases: dict[int, list[dict]] = {}
    for t in tasks:
        phases.setdefault(depth[t["id"]], []).append(t)
    out = []
    for i, k in enumerate(sorted(phases)):
        group = phases[k]
        done = sum(t["status"] == "Complete" for t in group)
        dues = [d for d in (deadlines.parse_deadline(t.get("deadline")) for t in group) if d]
        due = max(dues) if dues else None
        blocked = [t for t in group if t["status"] == "Blocked"]
        late = due is not None and due < now and done < len(group)
        status = ("Done" if done == len(group) else "Late" if late
                  else "At risk" if blocked or any(deadlines.is_overdue(t.get("deadline"), now) and t["status"] != "Complete" for t in group)
                  else "On track")
        name = PHASE_NAMES[i] if i < len(PHASE_NAMES) else f"Phase {i + 1}"
        out.append({
            "index": i + 1, "name": name, "status": status, "done": done, "total": len(group),
            "pct": round(100 * done / len(group)) if group else 0,
            "due_at": deadlines.to_iso(due) if due else None,
            "tasks": [{"id": t["id"], "title": t["title"], "owner": t.get("owner_name"), "status": t["status"]} for t in group],
            "blocked": [t["title"] for t in blocked],
        })
    return out


def risks(tasks: list[dict], participants: list[dict], now: Optional[datetime] = None) -> list[dict]:
    """Ranked risks: overdue work, blockers (and what they hold up), deadlines at risk, overload, unproven 'done'."""
    now = now or datetime.now()
    by_id = {t["id"]: t for t in tasks}
    names = {p["id"]: p["name"] for p in participants}
    children: dict[int, list[int]] = {}
    for t in tasks:
        if t.get("depends_on_task_id") in by_id:
            children.setdefault(t["depends_on_task_id"], []).append(t["id"])

    def downstream(tid):
        seen, stack = [], list(children.get(tid, []))
        while stack:
            x = stack.pop()
            if x not in seen:
                seen.append(x)
                stack.extend(children.get(x, []))
        return seen

    out = []
    for t in tasks:
        if t["status"] == "Complete":
            if t.get("proof") == "self":
                out.append({"severity": "warning", "kind": "Unverified", "task": t["title"], "owner": t.get("owner_name"),
                            "detail": "Marked done without a commit, a working link or a lead review.",
                            "action": f"Ask {t.get('owner_name') or 'the owner'} for proof, or review and approve it."})
            continue
        held = downstream(t["id"])
        due = deadlines.parse_deadline(t.get("deadline"))
        if t["status"] == "Blocked":
            helper = names.get(t.get("blocked_by_participant_id"))
            out.append({"severity": "critical" if held else "serious", "kind": "Blocked", "task": t["title"], "owner": t.get("owner_name"),
                        "detail": (t.get("blocker_reason") or "Blocked") + (f" - holding up {len(held)} task(s)" if held else ""),
                        "action": f"{helper} can unblock this: ping them." if helper else "Find out who can unblock it."})
        if due and due < now:
            late = now - due
            out.append({"severity": "critical" if held or late > timedelta(hours=24) else "serious", "kind": "Overdue",
                        "task": t["title"], "owner": t.get("owner_name"),
                        "detail": f"Was due {deadlines.humanize(t['deadline'])}" + (f"; {len(held)} task(s) wait on it" if held else ""),
                        "action": "Agree a new date, or move the deadline from the task's Update dialog."})
        elif due and due - now < timedelta(hours=24) and t["status"] == "Pending":
            out.append({"severity": "serious", "kind": "Deadline at risk", "task": t["title"], "owner": t.get("owner_name"),
                        "detail": f"Due {deadlines.humanize(t['deadline'])} and not started yet.",
                        "action": f"Check in with {t.get('owner_name') or 'the owner'} today."})
        up = by_id.get(t.get("depends_on_task_id"))
        if up and up["status"] == "Blocked" and t["status"] != "Blocked":
            out.append({"severity": "warning", "kind": "Waiting on a blocker", "task": t["title"], "owner": t.get("owner_name"),
                        "detail": f"Can't move until \"{up['title']}\" is unblocked.",
                        "action": "Clear the upstream blocker first."})

    open_by = {p["id"]: sum(1 for t in tasks if t.get("owner_id") == p["id"] and t["status"] != "Complete") for p in participants}
    loads = [n for n in open_by.values()]
    avg = sum(loads) / len(loads) if loads else 0
    for pid, n in open_by.items():
        if n >= 3 and avg and n >= 2 * avg:
            out.append({"severity": "warning", "kind": "Overloaded", "task": None, "owner": names.get(pid),
                        "detail": f"{n} open tasks, about {round(n / avg, 1)}x the team average.",
                        "action": "Move a task to someone with fewer."})
    out.sort(key=lambda r: SEVERITY_ORDER[r["severity"]])
    return out


def report_markdown(report: dict, project: dict, tasks: list[dict], participants: list[dict]) -> str:
    """The progress report as a Markdown document (to download, paste in chat or submit as evidence)."""
    lines = [f"# Progress report: {project['name']}", "",
             f"Generated {datetime.now().strftime('%a %d %b %Y, %I:%M %p')} by Coordina", "",
             f"**Health:** {report['health_score']}/100 ({report['health_label']})  ", ""]
    v = report.get("verification") or {}
    m = report.get("milestones") or {}
    lines += [f"**Tasks:** {m.get('complete', 0)}/{m.get('total', 0)} complete, {m.get('in_progress', 0)} in progress, "
              f"{m.get('blocked', 0)} blocked, {m.get('pending', 0)} not started"
              + (f" · {v['verified']}/{v['total']} verified with proof" if v else ""), "",
              "## Summary", "", report.get("executive_summary", ""), ""]
    if report.get("milestone_plan"):
        lines += ["## Milestones", "", "| Phase | Status | Progress | Due |", "|---|---|---|---|"]
        for ph in report["milestone_plan"]:
            lines.append(f"| {ph['index']}. {ph['name']} | {ph['status']} | {ph['done']}/{ph['total']} ({ph['pct']}%) | "
                         f"{deadlines.humanize(ph['due_at']) if ph['due_at'] else '-'} |")
        lines.append("")
    if report.get("risks"):
        lines += ["## Risks", ""]
        for r in report["risks"]:
            what = f"\"{r['task']}\"" if r.get("task") else r.get("owner") or ""
            lines.append(f"- **{r['severity'].upper()} · {r['kind']}** {what}"
                         + (f" ({r['owner']})" if r.get("task") and r.get("owner") else "") + f": {r['detail']} *Next:* {r['action']}")
        lines.append("")
    if report.get("active_handoffs"):
        lines += ["## Handoffs", ""]
        for h in report["active_handoffs"]:
            what = h.get("need") if h.get("type") == "BLOCKER" else h.get("to_task")
            lines.append(f"- {h.get('from_owner') or '?'} -> {h.get('to_owner') or '?'}: {what} ({h.get('state')})")
        lines.append("")
    lines += ["## Tasks", "", "| # | Task | Owner | Status | Due | Proof |", "|---|---|---|---|---|---|"]
    proof_label = {"github": "GitHub", "deliverable": "Link/file", "lead": "Lead approved", "self": "Needs proof", "none": "-"}
    for i, t in enumerate(tasks, 1):
        lines.append(f"| {i} | {t['title']} | {t.get('owner_name') or '-'} | {t['status']} | "
                     f"{deadlines.humanize(t.get('deadline')) or '-'} | {proof_label.get(t.get('proof'), '-')} |")
    lines += ["", "## Recommended next actions", ""] + [f"{i}. {a}" for i, a in enumerate(report.get("recommended_next_actions", []), 1)]
    return "\n".join(lines) + "\n"
