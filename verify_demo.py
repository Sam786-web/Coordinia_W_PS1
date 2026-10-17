#!/usr/bin/env python3
"""W-PS-1 rubric verifier for Coordina.

Runs the judged demo story against the live backend API over HTTP and prints a pass/fail table.

    python verify_demo.py                      # uses http://127.0.0.1:8000, starts a temporary backend if none is running
    python verify_demo.py --base-url http://192.168.1.20:8000
    python verify_demo.py --offline            # temporary backend uses the Offline Smart Engine (no LLM keys needed)

Standard library only. Resets the demo data, and leaves the Student Project preset active when done.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"

# ---------- terminal output ----------

if os.name == "nt":
    os.system("")  # enable ANSI colours in Windows consoles
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


GREEN, RED, DIM, BOLD, CYAN = "32", "31", "2", "1", "36"


# ---------- HTTP ----------

class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            raise AssertionError(f"{method} {path} -> HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def alive(self):
        try:
            self.get("/")
            return True
        except Exception:
            return False


# ---------- check framework ----------

class Check:
    def __init__(self, number, name):
        self.number, self.name = number, name
        self.asserts = []  # (ok, label, detail)
        self.error = None

    def expect(self, ok, label, detail=""):
        self.asserts.append((bool(ok), label, str(detail)))
        return ok

    @property
    def passed(self):
        return self.error is None and self.asserts and all(a[0] for a in self.asserts)


def task(state, title):
    return next(t for t in state["tasks"] if t["title"] == title)


def logs_since(state, last_id):
    return [lg for lg in state["logs"] if lg["id"] > last_id]


def max_log_id(state):
    return max((lg["id"] for lg in state["logs"]), default=0)


def reply(api, state, name, message):
    pid = next(p["id"] for p in state["participants"] if p["name"] == name)
    return api.post("/api/simulate-reply", {"participant_id": pid, "message": message, "source": "simulator"})


API_TASK = "Connect the payment API"
INTEGRATION_TASK = "End-to-End Payment Integration"
PITCH_TASK = "Finalize Pitch Deck Slides"


def run_checks(api):
    checks = []

    def section(number, name, fn):
        ch = Check(number, name)
        checks.append(ch)
        try:
            fn(ch)
        except Exception as e:  # a failed request or missing data fails this check only
            ch.error = f"{type(e).__name__}: {e}"

    # 1. Reset & seed
    def seed(ch):
        api.post("/api/reset-demo")
        s = api.get("/api/state")
        a, k, m = task(s, PITCH_TASK), task(s, API_TASK), task(s, INTEGRATION_TASK)
        ch.expect(a["owner_name"] == "Aanya" and a["status"] == "Complete", "Aanya's pitch deck is Complete", a["status"])
        ch.expect(k["owner_name"] == "Kabir" and k["status"] == "Pending", "Kabir's payment API is Pending", k["status"])
        ch.expect(m["owner_name"] == "Meera" and m["status"] == "In Progress", "Meera's integration is In Progress", m["status"])
        ch.expect(m["depends_on_task_id"] == k["id"], "Meera's task depends on Kabir's", f"depends_on={m['depends_on_task_id']}")

    # 2. Outbound follow-up
    def follow_up(ch):
        s = api.get("/api/state")
        before = max_log_id(s)
        k = task(s, API_TASK)
        out = api.post(f"/api/ping-task/{k['id']}")
        ch.expect(out.get("action", {}).get("to") == "Kabir", "Check-in addressed to Kabir", out.get("action"))
        n = (out.get("notifications") or [{}])[0]
        ch.expect(n.get("text"), "Outbound message text generated", (n.get("text") or "")[:70])
        new = logs_since(api.get("/api/state"), before)
        ch.expect(any(lg["log_type"] == "FOLLOW_UP" for lg in new), "FOLLOW_UP logged in AgentLog",
                  f"channel={n.get('channel')}, delivered={n.get('delivered')}")

    # 3. Partial progress vs blocker
    def partial(ch):
        s = api.get("/api/state")
        before = max_log_id(s)
        r = reply(api, s, "Kabir", "The API is connected, but I am waiting for test credentials from Meera")["result"]
        k = task(api.get("/api/state"), API_TASK)
        ch.expect(r["new_status"] == "Blocked" and k["status"] == "Blocked", "Kabir is Blocked (not Complete)", k["status"])
        note = (k["progress_note"] or "").lower()
        ch.expect("api" in note and "connect" in note, "progress_note captures the API connection", k["progress_note"])
        ch.expect("credential" in (k["blocker_reason"] or "").lower(), "blocker_reason captures test credentials", k["blocker_reason"])
        ch.expect(r["auto_relay_target_name"] == "Meera" and k["blocked_by_name"] == "Meera", "Blocker attributed to Meera", r["auto_relay_target_name"])
        new = logs_since(api.get("/api/state"), before)
        relay = [lg for lg in new if lg["log_type"] == "AUTO_RELAY"]
        ch.expect(relay, "Autonomous relay to Meera logged", relay[0]["summary"] if relay else "none")

    # 4. Contradiction guard
    def contradiction(ch):
        s = api.get("/api/state")
        r = reply(api, s, "Meera", "Integration is completely done!")["result"]
        m = task(api.get("/api/state"), INTEGRATION_TASK)
        ch.expect(r["contradiction_warning"], "contradiction_warning raised", (r["contradiction_warning"] or "")[:80])
        ch.expect(m["status"] != "Complete", "Meera's task is NOT marked Complete", m["status"])
        ch.expect(task(api.get("/api/state"), API_TASK)["status"] == "Blocked", "Kabir is still Blocked")

    # 5. Autonomous unblock
    def unblock(ch):
        s = api.get("/api/state")
        k_before = task(s, API_TASK)
        r = reply(api, s, "Meera", "I just sent the test credentials to Kabir")["result"]
        k = task(api.get("/api/state"), API_TASK)
        ch.expect(k_before["status"] == "Blocked", "Kabir was Blocked before the reply", k_before["status"])
        ch.expect(r["unblocked_downstream_task_id"] == k["id"], "Coordinator identified Kabir's task to unblock", r["unblocked_downstream_task_id"])
        ch.expect(k["status"] == "In Progress", "Kabir moved Blocked -> In Progress automatically", k["status"])
        ch.expect(k["blocker_reason"] is None, "Blocker cleared", k["blocker_reason"])

    # 6. True completion
    def completion(ch):
        s = api.get("/api/state")
        r = reply(api, s, "Kabir", "Got the test credentials and finished payment API testing, 100% done!")["result"]
        k = task(api.get("/api/state"), API_TASK)
        ch.expect(r["new_status"] == "Complete" and k["status"] == "Complete", "Kabir's task is Complete", k["status"])
        ch.expect(not r["contradiction_warning"], "No contradiction (no open upstream)", r["contradiction_warning"])
        m = task(api.get("/api/state"), INTEGRATION_TASK)
        ch.expect(not m["is_at_risk"], "Meera's task is no longer at risk", f"is_at_risk={m['is_at_risk']}")

    # 7. Enterprise preset + executive report
    def enterprise(ch):
        out = api.post("/api/presets/load", {"mode": "enterprise"})
        roles = {p["name"]: p["role"] for p in out["participants"]}
        ch.expect(out["active_project"]["name"] == "FinTech Production Release v2.4", "Enterprise sprint is active", out["active_project"]["name"])
        ch.expect(set(roles) == {"Rohan", "Priya", "Vikram", "Neha"}, "Four engineers with company roles", ", ".join(f"{k} ({v})" for k, v in roles.items()))
        t = {x["title"]: x for x in out["tasks"]}
        chain = (t["Run Load & Penetration Testing"]["depends_on_task_id"] == t["Stripe Production Webhook API"]["id"]
                 and t["Client UAT & Security Sign-Off"]["depends_on_task_id"] == t["Run Load & Penetration Testing"]["id"])
        ch.expect(chain, "Dependency chain Rohan -> Vikram -> Neha")
        rep = api.get("/api/report")
        keys = ["health_score", "critical_path_bottlenecks", "active_handoffs", "cascading_risks", "recommended_next_actions"]
        ch.expect(all(k in rep for k in keys), "Report has health, bottlenecks, handoffs, risks, actions",
                  f"health {rep.get('health_score')}/100 ({rep.get('health_label')}) via {rep.get('ai_layer')}")
        ch.expect(isinstance(rep.get("health_score"), int) and 0 <= rep["health_score"] <= 100, "Health score is 0-100", rep.get("health_score"))

    section(1, "Reset & Seed State", seed)
    section(2, "Outbound Task Follow-Up", follow_up)
    section(3, "Partial Progress vs. Blocker Rule", partial)
    section(4, "Dependency Contradiction Guard", contradiction)
    section(5, "Autonomous Unblock Handoff", unblock)
    section(6, "True Completion", completion)
    section(7, "Enterprise Mode & Executive Report", enterprise)
    return checks


def print_report(checks, base, layer, elapsed):
    width = 60
    print()
    print(c("  W-PS-1 Rubric Verification · Coordina", BOLD))
    print(c(f"  {base}  ·  AI layer: {layer}", DIM))
    print("  " + "─" * (width + 12))
    for ch in checks:
        status = c(" PASS ", "42;30") if ch.passed else c(" FAIL ", "41;37")
        if not COLOR:
            status = "[PASS]" if ch.passed else "[FAIL]"
        print(f"  {status}  {ch.number}. {c(ch.name, BOLD)}")
        for ok, label, detail in ch.asserts:
            mark = c("✓", GREEN) if ok else c("✗", RED)
            line = f"          {mark} {label}"
            if detail and (not ok or len(detail) < 70):
                line += c(f"  ({detail})", DIM)
            print(line)
        if ch.error:
            print(c(f"          ✗ {ch.error}", RED))
    print("  " + "─" * (width + 12))
    passed = sum(ch.passed for ch in checks)
    asserts = sum(len(ch.asserts) for ch in checks)
    ok_asserts = sum(a[0] for ch in checks for a in ch.asserts)
    summary = f"  {passed}/{len(checks)} rubric checks passed · {ok_asserts}/{asserts} assertions · {elapsed:.1f}s"
    print(c(summary, GREEN if passed == len(checks) else RED))
    print()


def start_temp_backend(port, offline):
    py = BACKEND / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    py = str(py) if py.exists() else sys.executable
    env = {**os.environ, "TELEGRAM_DISABLE_POLLING": "1"}
    if offline:
        env["LLM_FORCE_OFFLINE"] = "1"
    proc = subprocess.Popen([py, "-m", "uvicorn", "main:app", "--port", str(port)], cwd=BACKEND, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = Api(f"http://127.0.0.1:{port}")
    for _ in range(120):
        if proc.poll() is not None:
            raise SystemExit("The temporary backend exited during startup. Run it manually to see the error.")
        if api.alive():
            return proc, api
        time.sleep(0.5)
    proc.terminate()
    raise SystemExit("The temporary backend did not start within 60 seconds.")


def main():
    ap = argparse.ArgumentParser(description="Verify the W-PS-1 rubric against the Coordina backend.")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--offline", action="store_true", help="temporary backend uses the Offline Smart Engine")
    ap.add_argument("--port", type=int, default=8765, help="port for the temporary backend (default 8765)")
    args = ap.parse_args()

    api, proc = Api(args.base_url), None
    if not api.alive():
        print(c(f"  No backend at {args.base_url}; starting a temporary one on port {args.port}…", DIM))
        proc, api = start_temp_backend(args.port, args.offline)
    elif args.offline:
        print(c("  Note: --offline only applies to a temporary backend; using the running server's AI layer.", DIM))

    started = time.time()
    try:
        checks = run_checks(api)
        try:
            layer = api.get("/api/llm-status").get("active_layer", "?")
        except Exception:
            layer = "?"
        print_report(checks, api.base, layer, time.time() - started)
    finally:
        try:
            api.post("/api/presets/load", {"mode": "college"})  # leave the demo at its starting point
        except Exception:
            pass
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    sys.exit(0 if all(ch.passed for ch in checks) else 1)


if __name__ == "__main__":
    main()
