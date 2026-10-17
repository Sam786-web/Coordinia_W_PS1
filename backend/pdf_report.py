"""The progress report as a proper PDF: health, summary, milestones, risks, handoffs, every task, next actions."""
from datetime import datetime
from pathlib import Path
from typing import Optional

from fpdf import FPDF
from fpdf.fonts import FontFace

import deadlines

INK = (17, 24, 39)
MUTED = (107, 114, 128)
LINE = (228, 228, 231)
CORAL = (224, 99, 72)
PANEL = (248, 250, 252)
SEVERITY = {"critical": ("CRITICAL", (208, 59, 59)), "serious": ("SERIOUS", (217, 89, 38)), "warning": ("WATCH", (201, 133, 0))}
PHASE = {"Done": (25, 158, 112), "On track": (98, 80, 214), "At risk": (217, 89, 38), "Late": (208, 59, 59)}
PROOF = {"github": "GitHub", "deliverable": "Link / file", "lead": "Lead approved", "self": "Needs proof", "none": "-"}
FONT_DIR = Path("C:/Windows/Fonts")


class _Doc(FPDF):
    def __init__(self, project_name: str):
        super().__init__(format="A4")
        self.project_name = project_name
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(True, margin=18)
        self.family = "Helvetica"
        regular, bold = FONT_DIR / "arial.ttf", FONT_DIR / "arialbd.ttf"
        if regular.exists() and bold.exists():  # a Unicode font when available (names, rupee sign, accents)
            self.add_font("Body", "", str(regular))
            self.add_font("Body", "B", str(bold))
            self.family = "Body"

    def text_safe(self, s) -> str:
        s = "" if s is None else str(s)
        return s if self.family == "Body" else s.encode("latin-1", "replace").decode("latin-1")

    def footer(self):
        self.set_y(-12)
        self.set_font(self.family, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, self.text_safe(f"Coordina progress report · {self.project_name}"), align="L")
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R")


def _heading(pdf: _Doc, text: str) -> None:
    if pdf.get_y() > pdf.h - 55:  # never leave a heading alone at the bottom of a page
        pdf.add_page()
    pdf.ln(4)
    pdf.set_font(pdf.family, "B", 12)
    pdf.set_text_color(*INK)
    pdf.cell(0, 7, pdf.text_safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*CORAL)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 14, pdf.get_y())
    pdf.ln(3)


def _para(pdf: _Doc, text: str, size: float = 9.5, color=INK) -> None:
    pdf.set_font(pdf.family, "", size)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, 5, pdf.text_safe(text), new_x="LMARGIN", new_y="NEXT")


def _kpis(pdf: _Doc, items: list[tuple[str, str]]) -> None:
    gap = 3
    w = (pdf.w - pdf.l_margin - pdf.r_margin - gap * (len(items) - 1)) / len(items)
    y = pdf.get_y()
    for i, (value, label) in enumerate(items):
        x = pdf.l_margin + i * (w + gap)
        pdf.set_fill_color(*PANEL)
        pdf.set_draw_color(*LINE)
        pdf.rect(x, y, w, 18, style="DF", round_corners=True, corner_radius=2.5)
        pdf.set_xy(x + 4, y + 2.5)
        pdf.set_font(pdf.family, "B", 15)
        pdf.set_text_color(*INK)
        pdf.cell(w - 8, 7, pdf.text_safe(value))
        pdf.set_xy(x + 4, y + 10.5)
        pdf.set_font(pdf.family, "", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 8, 5, pdf.text_safe(label))
    pdf.set_y(y + 22)


def _table(pdf: _Doc, headers: list[str], rows: list[list[str]], widths: tuple, colors: Optional[dict] = None) -> None:
    pdf.set_font(pdf.family, "", 8.5)
    pdf.set_text_color(*INK)
    pdf.set_draw_color(*LINE)
    head = FontFace(emphasis="BOLD", color=MUTED, fill_color=PANEL)
    with pdf.table(col_widths=widths, headings_style=head, line_height=5.2, text_align="LEFT",
                   borders_layout="HORIZONTAL_LINES", padding=1.6) as table:
        row = table.row()
        for h in headers:
            row.cell(pdf.text_safe(h))
        for r in rows:
            row = table.row()
            for j, value in enumerate(r):
                color = (colors or {}).get((j, value))
                row.cell(pdf.text_safe(value), style=FontFace(color=color, emphasis="BOLD") if color else None)


def build_pdf(report: dict, project: dict, tasks: list[dict], workspace_label: str = "") -> bytes:
    pdf = _Doc(project["name"])
    pdf.alias_nb_pages()
    pdf.add_page()

    # ---- title block ----
    pdf.set_fill_color(*CORAL)
    pdf.rect(pdf.l_margin, pdf.get_y(), 3, 20, style="F")
    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font(pdf.family, "B", 9)
    pdf.set_text_color(*CORAL)
    pdf.cell(0, 5, pdf.text_safe(f"COORDINA · PROGRESS REPORT{' · ' + workspace_label.upper() if workspace_label else ''}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font(pdf.family, "B", 19)
    pdf.set_text_color(*INK)
    pdf.cell(0, 9, pdf.text_safe(project["name"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 6)
    pdf.set_font(pdf.family, "", 9)
    pdf.set_text_color(*MUTED)
    span = ""
    if project.get("due_at"):
        span = f" · Final deadline {deadlines.humanize(project['due_at'])}"
    pdf.cell(0, 5, pdf.text_safe(f"Generated {datetime.now().strftime('%a %d %b %Y, %I:%M %p')}{span}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ---- KPIs ----
    m = report.get("milestones") or {}
    v = report.get("verification") or {}
    _kpis(pdf, [
        (f"{report.get('health_score', 0)}/100", f"Health · {report.get('health_label', '')}"),
        (f"{m.get('complete', 0)}/{m.get('total', 0)}", "Tasks complete"),
        (f"{v.get('verified', 0)}/{v.get('total', m.get('total', 0))}", "Verified with proof"),
        (str(m.get("blocked", 0)), "Blocked now"),
    ])

    _heading(pdf, "Summary")
    _para(pdf, report.get("executive_summary") or "")

    # ---- milestones ----
    plan = report.get("milestone_plan") or []
    if plan:
        _heading(pdf, "Milestones")
        rows = [[f"{ph['index']}. {ph['name']}", ph["status"], f"{ph['done']}/{ph['total']}  ({ph['pct']}%)",
                 deadlines.humanize(ph["due_at"]) if ph.get("due_at") else "-",
                 ", ".join(t["title"] for t in ph["tasks"][:3]) + (" ..." if len(ph["tasks"]) > 3 else "")] for ph in plan]
        _table(pdf, ["Phase", "Status", "Progress", "Due", "Tasks"], rows, (30, 18, 22, 32, 70),
               colors={(1, k): c for k, c in PHASE.items()})

    # ---- risks ----
    _heading(pdf, "Risks")
    risks = report.get("risks") or []
    if not risks:
        _para(pdf, "No risks right now: nothing overdue, blocked or unproven.", color=MUTED)
    for r in risks:
        label, color = SEVERITY.get(r["severity"], SEVERITY["warning"])
        pdf.set_font(pdf.family, "B", 8)
        pdf.set_text_color(*color)
        pdf.cell(20, 5, label)
        pdf.set_text_color(*INK)
        pdf.set_font(pdf.family, "B", 9.5)
        what = f"{r['kind']}" + (f' · "{r["task"]}"' if r.get("task") else "") + (f" · {r['owner']}" if r.get("owner") else "")
        pdf.multi_cell(0, 5, pdf.text_safe(what), new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(pdf.l_margin + 20)
        _para_x = pdf.w - pdf.r_margin - pdf.l_margin - 20
        pdf.set_font(pdf.family, "", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(_para_x, 4.6, pdf.text_safe(f"{r['detail']}  Next: {r['action']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)

    # ---- handoffs ----
    # handoffs between different people (a person's own next task isn't a handoff)
    handoffs = [h for h in (report.get("active_handoffs") or []) if h.get("from_owner") != h.get("to_owner")]
    if handoffs:
        _heading(pdf, "Handoffs")
        rows = [[h.get("from_owner") or "-", h.get("to_owner") or "-",
                 (h.get("need") if h.get("type") == "BLOCKER" else h.get("to_task")) or "-", h.get("state") or "-"] for h in handoffs]
        _table(pdf, ["From", "To", "What", "State"], rows, (28, 28, 96, 20))

    # ---- tasks ----
    _heading(pdf, "All tasks")
    status_color = {"Complete": (25, 158, 112), "Blocked": (208, 59, 59), "In Progress": (201, 133, 0)}
    rows = [[str(i), t["title"], t.get("owner_name") or "-", t["status"], deadlines.humanize(t.get("deadline")) or "-",
             PROOF.get(t.get("proof"), "-")] for i, t in enumerate(tasks, 1)]
    _table(pdf, ["#", "Task", "Owner", "Status", "Due", "Proof"], rows, (8, 66, 24, 22, 32, 20),
           colors={(3, k): c for k, c in status_color.items()})
    blocked = [t for t in tasks if t["status"] == "Blocked" and t.get("blocker_reason")]
    if blocked:
        pdf.ln(2)
        for t in blocked:
            _para(pdf, f"Blocked · {t['title']}: {t['blocker_reason']}" + (f" (needs {t['blocked_by_name']})" if t.get("blocked_by_name") else ""),
                  size=8.5, color=(208, 59, 59))

    # ---- next actions ----
    actions = report.get("recommended_next_actions") or []
    if actions:
        _heading(pdf, "Recommended next actions")
        for i, a in enumerate(actions, 1):
            _para(pdf, f"{i}.  {a}")

    return bytes(pdf.output())
