"""Automated self-test for the AI coordinator brain.

Runs against a throwaway SQLite DB. Always tests the Offline Smart Engine; also tests the live LLM
layers when OPENCODE_API_KEY or BACKUP_API_KEY is configured.

    python self_test.py            # offline + live (if keys)
    python self_test.py --offline  # offline only
"""
import os
import sys
import tempfile
from pathlib import Path

_tmp_db = Path(tempfile.gettempdir()) / "coordinator_selftest.db"
_tmp_db.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + _tmp_db.as_posix()
os.environ["TELEGRAM_DISABLE_POLLING"] = "1"  # never hijack the real bot's polling during tests

from fastapi.testclient import TestClient  # noqa: E402

import llm_engine  # noqa: E402
from database import engine  # noqa: E402
from main import app  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))
    return ok


def task_by_title(c: TestClient, title: str) -> dict:
    return next(t for t in c.get("/api/state").json()["tasks"] if t["title"] == title)


def reply(c: TestClient, name: str, message: str) -> dict:
    r = c.post("/api/simulate-reply", json={"participant_name": name, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


def run_core_scenarios(c: TestClient, mode: str, strict_text: bool) -> None:
    print(f"\n=== Core scenarios [{mode}] ===")
    c.post("/api/reset-demo")
    kabir_task = task_by_title(c, "Connect the payment API")
    meera_task = task_by_title(c, "End-to-End Payment Integration")

    # 1) Partial progress + blocker -> Blocked, relay to Meera
    out = reply(c, "Kabir", "The API is connected, but I am waiting for test credentials from Meera")
    r = out["result"]
    print(f"     layer={out['llm']['layer']} model={out['llm']['model']}")
    check(f"[{mode}] 1. Kabir's Task {kabir_task['id']} marked Blocked (not Complete)", r["new_status"] == "Blocked", r["new_status"])
    check(f"[{mode}] 1. is_blocked = true", r["is_blocked"] is True)
    if strict_text:
        check(f"[{mode}] 1. progress_note == 'API is connected'", r["progress_note"] == "API is connected", r["progress_note"])
        check(f"[{mode}] 1. blocker_reason == 'Waiting for test credentials'",
              r["blocker_reason"] == "Waiting for test credentials", r["blocker_reason"])
    else:
        check(f"[{mode}] 1. progress_note mentions the API", "api" in (r["progress_note"] or "").lower(), r["progress_note"])
        check(f"[{mode}] 1. blocker_reason mentions credentials", "credential" in (r["blocker_reason"] or "").lower(), r["blocker_reason"])
    check(f"[{mode}] 1. auto_relay_target_name == Meera", r["auto_relay_target_name"] == "Meera", r["auto_relay_target_name"])
    check(f"[{mode}] 1. auto_relay_message generated", bool(r["auto_relay_message"]), (r["auto_relay_message"] or "")[:90])
    db = task_by_title(c, "Connect the payment API")
    check(f"[{mode}] 1. DB: Kabir's task Blocked, blocked_by Meera", db["status"] == "Blocked" and db["blocked_by_name"] == "Meera",
          f"{db['status']} / {db['blocked_by_name']}")
    check(f"[{mode}] 1. DB: Meera's task now is_at_risk", task_by_title(c, "End-to-End Payment Integration")["is_at_risk"] is True)

    # 2) Illusion of progress: Meera claims done while upstream is Blocked
    out = reply(c, "Meera", "Done! The end-to-end payment integration is complete.")
    r = out["result"]
    check(f"[{mode}] 2. Meera's completion refused", r["new_status"] != "Complete", r["new_status"])
    check(f"[{mode}] 2. contradiction_warning set", bool(r["contradiction_warning"]), (r["contradiction_warning"] or "")[:110])
    check(f"[{mode}] 2. DB: Meera's task not Complete", task_by_title(c, "End-to-End Payment Integration")["status"] != "Complete")
    check(f"[{mode}] 2. Kabir still Blocked", task_by_title(c, "Connect the payment API")["status"] == "Blocked")

    # 3) Auto-unblock: Meera shares the credentials
    out = reply(c, "Meera", "I have shared the test credentials with Kabir")
    r = out["result"]
    check(f"[{mode}] 3. unblocked_downstream_task_id == Kabir's task",
          r["unblocked_downstream_task_id"] == kabir_task["id"], str(r["unblocked_downstream_task_id"]))
    check(f"[{mode}] 3. unblocked_notification_message generated", bool(r["unblocked_notification_message"]),
          (r["unblocked_notification_message"] or "")[:90])
    db = task_by_title(c, "Connect the payment API")
    check(f"[{mode}] 3. DB: Kabir's task Blocked -> In Progress", db["status"] == "In Progress", db["status"])
    check(f"[{mode}] 3. DB: blocker cleared", db["blocker_reason"] is None and db["blocked_by_participant_id"] is None)
    check(f"[{mode}] 3. Meera's own task unchanged (In Progress)",
          task_by_title(c, "End-to-End Payment Integration")["status"] == "In Progress")
    log_types = {lg["log_type"] for lg in c.get("/api/state").json()["logs"]}
    check(f"[{mode}] 3. AgentLog has REPLY/AI_REASONING/BLOCKER_ALERT/AUTO_RELAY",
          {"REPLY", "AI_REASONING", "BLOCKER_ALERT", "AUTO_RELAY"} <= log_types, ", ".join(sorted(log_types)))


def run_extras(c: TestClient) -> None:
    print("\n=== Extra capabilities [offline] ===")
    c.post("/api/reset-demo")
    r = reply(c, "Kabir", "Bhai code ho gaya bas test credentials baaki hai")["result"]
    check("Hinglish partial progress -> Blocked", r["new_status"] == "Blocked", f"{r['progress_note']} | {r['blocker_reason']}")

    c.post("/api/reset-demo")
    r = reply(c, "Kabir", "Yeah basically done lol just crashing on login")["result"]
    check("Sarcasm/vague -> Blocked with lower confidence", r["new_status"] == "Blocked" and r["confidence_score"] < 0.8,
          f"{r['blocker_reason']} (conf {r['confidence_score']})")

    r = reply(c, "Aanya", "Free at 5 PM if anyone needs help with slides")["result"]
    check("Availability detected", r["detected_availability"] == "Free at 5:00 PM", str(r["detected_availability"]))

    c.post("/api/reset-demo")
    state = c.get("/api/state").json()
    pids = {p["name"]: p["id"] for p in state["participants"]}
    out = c.post("/api/mobile/quick-update", json={"participant_id": pids["Kabir"], "action_type": "SOS_BLOCKER",
                                                   "text": "Payment sandbox keeps returning 401", "availability": "Free in 15 mins"}).json()
    check("Mobile SOS -> Blocked + broadcast", out["result"]["new_status"] == "Blocked"
          and any(a["type"] == "SOS_BROADCAST" for a in out["actions"]), str([a["type"] for a in out["actions"]]))
    check("Mobile availability stored as absolute time", out["dashboard"]["participant"]["availability"].startswith("Free at"),
          out["dashboard"]["participant"]["availability"])

    d = c.get(f"/api/mobile/my-dashboard/{pids['Meera']}").json()
    check("Mobile dashboard: Meera's upstream shows Blocked", d["my_tasks"][0]["upstream"]["status"] == "Blocked")
    slot = d["team_sync"]["recommended_slot"]
    check("Slot coordinator recommends a 15-min slot", bool(slot), f"{d['team_sync']['purpose']}: {slot and slot['label']} "
          f"with {d['team_sync']['attendees']}")

    rep = c.get("/api/report?ai=false").json()
    check("Report has health/bottlenecks/handoffs/cascading/actions",
          all(k in rep for k in ("health_score", "critical_path_bottlenecks", "active_handoffs", "cascading_risks",
                                 "recommended_next_actions")) and rep["cascading_risks"],
          f"health {rep['health_score']} ({rep['health_label']}), {len(rep['cascading_risks'])} cascading risk(s)")

    g = c.post("/api/ai/generate-project", json={"project_name": "Campus Lost & Found App",
                                                  "description": "Students report and claim lost items",
                                                  "teammates": ["Riya", "Arjun", "Sara"]})
    gj = g.json()
    check("AI generate-project creates 4-5 dependent tasks", g.status_code == 201 and 4 <= len(gj["tasks"]) <= 6
          and any(t["depends_on_task_id"] for t in gj["tasks"]), f"{len(gj['tasks'])} tasks via {gj['llm'].get('source')}")
    c.post("/api/projects/1/activate")

    for raw, expect in [
        ('<think>hmm</think>```json\n{"a": 1, "b": [1,2,],}\n```', {"a": 1, "b": [1, 2]}),
        ("Sure! Here you go: {'a': True, 'b': None}", {"a": True, "b": None}),
        ("reasoning...</think>{\"ok\": true}", {"ok": True}),
    ]:
        try:
            got = llm_engine.repair_json(raw)
        except Exception as exc:
            got = repr(exc)
        check(f"JSON repair: {raw[:30]!r}", got == expect, str(got))

    st = c.get("/api/llm-status").json()
    check("LLM status endpoint", "active_layer" in st, st["active_layer"])


def run_step3(c: TestClient) -> None:
    import main as m
    print("\n=== Step 3: presets, real-time, Auto-Pilot, enterprise integrations [offline] ===")
    r = c.post("/api/presets/load", json={"mode": "college"}).json()
    check("Preset 'college' loads Aanya/Kabir/Meera", [p["name"] for p in r["participants"]] == ["Aanya", "Kabir", "Meera"]
          and r["active_project"]["name"] == "College Capstone Payment System")

    r = c.post("/api/presets/load", json={"mode": "enterprise"}).json()
    t = {x["title"]: x for x in r["tasks"]}
    roles = {p["name"]: p["role"] for p in r["participants"]}
    check("Preset 'enterprise' loads 4 engineers with company roles",
          roles == {"Rohan": "Senior Backend Eng", "Priya": "DevOps & Cloud Sec", "Vikram": "QA Automation Lead",
                    "Neha": "Engineering Manager"}, str(roles))
    check("Enterprise statuses & dependency chain",
          t["Stripe Production Webhook API"]["status"] == "Pending"
          and t["Provision AWS KMS & Prod Keys"]["status"] == "In Progress"
          and t["Run Load & Penetration Testing"]["depends_on_task_id"] == t["Stripe Production Webhook API"]["id"]
          and t["Client UAT & Security Sign-Off"]["depends_on_task_id"] == t["Run Load & Penetration Testing"]["id"])
    check("Enterprise preset is the active project", c.get("/api/state").json()["active_project"]["name"]
          == "FinTech Production Release v2.4")
    again = c.post("/api/presets/load", json={"mode": "enterprise"}).json()
    check("Reloading a preset is idempotent (no duplicate project)", again["active_project"]["id"] == r["active_project"]["id"]
          and len(again["tasks"]) == 4)

    with c.websocket_connect("/ws/live") as ws:
        hello = ws.receive_json()
        c.post("/api/ping-all-pending")
        evt = ws.receive_json()
        check("WebSocket /ws/live pushes STATE_UPDATED on change", hello["event"] == "CONNECTED"
              and evt["event"] == "STATE_UPDATED" and "timestamp" in evt, evt.get("reason", ""))

    gh = c.post("/api/webhooks/github", json={"author": "rohan-dev", "repo": "fintech/payments",
                                              "commit_message": "Connected payment webhook, blocked on AWS KMS keys from Priya"}).json()
    res = gh["results"][0]
    check("GitHub commit -> Rohan's task Blocked + relay to Priya",
          res["status"] == "processed" and res["result"]["new_status"] == "Blocked"
          and res["result"]["auto_relay_target_name"] == "Priya",
          f"{res['result']['progress_note']} | {res['result']['blocker_reason']}")
    ignored = c.post("/api/webhooks/github", json={"author": "stranger", "commit_message": "fix typo"}).json()
    check("GitHub commit from unknown author is ignored", ignored["processed"] == 0)

    m.AUTOPILOT.update(blocker_sla_seconds=0, followup_cooldown_seconds=0)
    a1 = c.post("/api/autopilot/run-now").json()["actions"]
    a2 = c.post("/api/autopilot/run-now").json()["actions"]
    check("Auto-Pilot SLA escalation to blocker owner (Priya)",
          any(a["type"] == "SLA_ESCALATION" and a["to"] == "Priya" for a in a1), str([(a["type"], a["to"]) for a in a1]))
    check("Auto-Pilot 2nd strike escalates to manager (Neha)",
          any(a["type"] == "MANAGER_ESCALATION" and a["to"] == "Neha" for a in a2), str([(a["type"], a["to"]) for a in a2]))
    st = c.get("/api/autopilot/status").json()
    check("Auto-Pilot status reports blocker SLA wait time", st["blocker_sla"] and st["blocker_sla"][0]["sla_breached"],
          f"{st['blocker_sla'][0]['title']} waited {st['blocker_sla'][0]['wait']}")
    m.AUTOPILOT.update(blocker_sla_seconds=90, followup_cooldown_seconds=90)

    on = c.post("/api/autopilot/toggle", json={"enabled": True, "interval_seconds": 30}).json()
    off = c.post("/api/autopilot/toggle", json={"enabled": False}).json()
    check("Auto-Pilot toggle on/off", on["enabled"] and on["running"] and on["interval_seconds"] == 30
          and not off["enabled"] and not off["running"])

    c.post("/api/presets/load", json={"mode": "college"})
    ping = c.post("/api/ping-all-pending").json()
    check("ping-all-pending pings Kabir's Pending task", ping["pinged"] == 1 and ping["actions"][0]["to"] == "Kabir")
    kabir_task = task_by_title(c, "Connect the payment API")
    check("ping-task/{id}", c.post(f"/api/ping-task/{kabir_task['id']}").status_code == 200)
    logs = c.get("/api/state").json()["logs"]
    check("Pings logged as FOLLOW_UP", any(lg["log_type"] == "FOLLOW_UP" for lg in logs))

    slots = c.post("/api/coordinate-slots", json={"availability": {"Kabir": "Free at 5 PM", "Meera": "Free in 30 mins"}}).json()
    check("coordinate-slots updates availability and recommends a slot",
          len(slots["updated_availability"]) == 2 and "recommended_slot" in slots,
          slots["recommended_slot"] and slots["recommended_slot"]["label"])

    csv_text = ("Title,Owner,Role,Status,Deadline,Depends On\n"
                "Design auth API,Asha,Backend Dev,In Review,Today 6:00 PM,\n"
                "Build login screen,Dev,Frontend Dev,To Do,Tomorrow 11:00 AM,Design auth API\n"
                "Release to staging,Asha,Backend Dev,Done,,\n")
    imp = c.post("/api/import-csv", json={"csv_text": csv_text, "project_name": "Sprint 42 (Jira import)"}).json()
    st = {x["title"]: x for x in imp["tasks"]}
    check("CSV import maps Jira statuses, owners and dependencies",
          imp["imported"] == 3 and st["Design auth API"]["status"] == "In Progress"
          and st["Build login screen"]["status"] == "Pending" and st["Release to staging"]["status"] == "Complete"
          and st["Build login screen"]["depends_on_task_id"] == st["Design auth API"]["id"] and len(imp["participants"]) == 2,
          f"errors={imp['errors']}")

    tg = c.get("/api/telegram/status").json()
    check("Telegram runs in Simulator Mode without a token (no crash)", tg["mode"] == "simulator", tg["reason"])
    c.post("/api/presets/load", json={"mode": "college"})


def main() -> int:
    offline_only = "--offline" in sys.argv
    with TestClient(app) as c:
        os.environ["LLM_FORCE_OFFLINE"] = "1"
        run_core_scenarios(c, "Offline Smart Engine", strict_text=True)
        run_extras(c)
        run_step3(c)
        os.environ.pop("LLM_FORCE_OFFLINE", None)

        cfg = llm_engine._config()
        if offline_only:
            print("\n(live LLM layers skipped: --offline)")
        elif cfg["opencode_key"] or cfg["backup_key"]:
            run_core_scenarios(c, "Live LLM layers", strict_text=False)
        else:
            print("\n(live LLM layers skipped: no OPENCODE_API_KEY / BACKUP_API_KEY in .env)")

    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} checks passed")
    engine.dispose()  # release the SQLite file lock (Windows) before deleting
    _tmp_db.unlink(missing_ok=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
