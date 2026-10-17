#!/usr/bin/env python3
"""Start Coordina with one command: FastAPI backend + Vite frontend, reachable from phones on the same network.

    python start_all.py            # start both, Ctrl+C stops both
    python start_all.py --open     # also open the dashboard in your browser
    python start_all.py --check    # start, confirm both respond, print the URLs, then stop (smoke test)

Standard library only. Works on Windows, macOS and Linux.
"""
import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"

if IS_WIN:
    os.system("")  # enable ANSI colours
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def lan_ip():
    """IPv4 address phones on the same Wi-Fi / hotspot can reach (prefers 192.168.x.x)."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # picks the default-route interface; no packet is sent
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    ips = [ip for ip in dict.fromkeys(found) if not ip.startswith(("127.", "169.254."))]
    ips.sort(key=lambda ip: (not ip.startswith("192.168."), not ip.startswith(("10.", "172."))))
    return ips[0] if ips else "127.0.0.1"


def backend_python():
    py = BACKEND / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
    if py.exists():
        return str(py)
    print(c("  backend/venv not found - using the current Python. Run: pip install -r backend/requirements.txt", "33"))
    return sys.executable


def npm():
    exe = shutil.which("npm.cmd" if IS_WIN else "npm") or shutil.which("npm")
    if not exe:
        sys.exit("npm was not found. Install Node.js 18+ from https://nodejs.org and try again.")
    return exe


def port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def responds(url):
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except Exception:
        return False


def stream(proc, label, color):
    """Prefix each output line with the service name."""
    tag = c(f"[{label}]", color)
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            print(f"{tag} {line}")


def spawn(cmd, cwd, label, color, env=None):
    kwargs = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env or os.environ.copy())
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    threading.Thread(target=stream, args=(proc, label, color), daemon=True).start()
    return proc


def stop(proc):
    if proc.poll() is not None:
        return
    if IS_WIN:  # npm.cmd -> node -> vite: kill the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()


def banner(ip, api_port, web_port):
    rows = [
        ("💻", "Desktop Mission Control:", f"http://localhost:{web_port}"),
        ("📱", "Android Phones Companion App:", f"http://{ip}:{web_port}/?mode=mobile"),
        ("🤖", "API & Swagger Docs:", f"http://localhost:{api_port}/docs"),
    ]
    label_w = max(len(r[1]) for r in rows)
    lines = [f" {icon}  {label.ljust(label_w)}  {url}" for icon, label, url in rows]
    width = max(len(l) for l in lines) + 3  # emoji render two columns wide
    top = "╭" + "─" * width + "╮"
    bottom = "╰" + "─" * width + "╯"
    title = " Coordina is running".ljust(width)
    print()
    print(c(top, "36"))
    print(c("│", "36") + c(title, "1") + c("│", "36"))
    print(c("│", "36") + " " * width + c("│", "36"))
    for icon, label, url in rows:
        text = f" {icon}  {label.ljust(label_w)}  "
        pad = width - len(text) - len(url) - 1
        print(c("│", "36") + text + c(url, "1;32") + " " * max(pad, 1) + c("│", "36"))
    print(c("│", "36") + " " * width + c("│", "36"))
    hint = " Phones must be on the same Wi-Fi/hotspot. Ctrl+C stops both.".ljust(width)
    print(c("│", "36") + c(hint, "2") + c("│", "36"))
    print(c(bottom, "36"))
    print()


def main():
    ap = argparse.ArgumentParser(description="Start the Coordina backend and frontend together.")
    ap.add_argument("--api-port", type=int, default=8000)
    ap.add_argument("--web-port", type=int, default=5173)
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser once ready")
    ap.add_argument("--check", action="store_true", help="start, verify both respond, then stop")
    args = ap.parse_args()

    for port, name in ((args.api_port, "backend"), (args.web_port, "frontend")):
        if port_busy(port):
            sys.exit(f"Port {port} is already in use - is Coordina ({name}) already running? Stop it or pass --{'api' if name == 'backend' else 'web'}-port.")

    if not (FRONTEND / "node_modules").exists():
        print(c("  Installing frontend dependencies (first run only)…", "2"))
        subprocess.run([npm(), "install", "--no-audit", "--no-fund"], cwd=FRONTEND, check=True)

    ip = lan_ip()
    env = os.environ.copy()
    if args.check:
        env["TELEGRAM_DISABLE_POLLING"] = "1"  # don't steal the bot's polling during a smoke test
    api = spawn([backend_python(), "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(args.api_port)],
                BACKEND, "api", "35", env)
    web = spawn([npm(), "run", "dev", "--", "--host", "0.0.0.0", "--port", str(args.web_port), "--strictPort"],
                FRONTEND, "web", "34", {**os.environ, "COORDINA_API_PORT": str(args.api_port)})
    procs = [api, web]

    def shutdown(*_):
        print(c("\n  Stopping Coordina…", "2"))
        for p in procs:
            stop(p)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    deadline = time.time() + 90
    ready = {"api": False, "web": False}
    while time.time() < deadline and not all(ready.values()):
        for p, name in ((api, "backend"), (web, "frontend")):
            if p.poll() is not None:
                print(c(f"\n  The {name} exited (code {p.returncode}). See the log lines above.", "31"))
                for q in procs:
                    stop(q)
                sys.exit(1)
        ready["api"] = ready["api"] or responds(f"http://127.0.0.1:{args.api_port}/")
        ready["web"] = ready["web"] or responds(f"http://127.0.0.1:{args.web_port}/api/state")  # page + API proxy
        time.sleep(0.5)
    if not all(ready.values()):
        print(c("\n  Timed out waiting for: " + ", ".join(k for k, v in ready.items() if not v), "31"))
        for p in procs:
            stop(p)
        sys.exit(1)

    banner(ip, args.api_port, args.web_port)
    if args.check:
        print(c("  --check: both servers responded. Shutting down.", "32"))
        for p in procs:
            stop(p)
        return
    if args.open:
        webbrowser.open(f"http://localhost:{args.web_port}")
    while all(p.poll() is None for p in procs):
        time.sleep(1)
    print(c("  One of the servers stopped - shutting down the other.", "33"))
    for p in procs:
        stop(p)
    sys.exit(1)


if __name__ == "__main__":
    main()
