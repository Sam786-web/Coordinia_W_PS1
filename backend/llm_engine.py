"""AI Coordinator Brain.

Zero-fail engine, tried in order:
  1. Primary cloud LLM with model rotation (e.g. Groq free tier; any OpenAI-compatible endpoint)
  2. Backup cloud LLM (any OpenAI-compatible endpoint: Groq, Gemini, ...)
  3. Local LLM on this laptop via Ollama (works with no internet; short jobs only, since CPUs are slow)
  4. Offline deterministic rule engine (no keys / no Wi-Fi)
Every LLM answer goes through bulletproof JSON repair for messy model output.

Deterministic guardrails (partial progress -> Blocked, contradiction refusal, blocker relay,
auto-unblock) are enforced on top of every LLM answer, so behaviour is identical on every layer.
"""
import ast
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

load_dotenv(Path(__file__).resolve().parent / ".env")

LAYER_OPENCODE = "Primary Cloud LLM"
LAYER_BACKUP = "Backup Cloud LLM"
LAYER_LOCAL = "Local LLM (Ollama)"
LAYER_OFFLINE = "Offline Smart Engine"

STATUSES = ("Pending", "In Progress", "Blocked", "Complete")
RESULT_KEYS = (
    "task_id", "new_status", "progress_note", "is_blocked", "blocker_reason",
    "blocked_by_teammate_name", "contradiction_warning", "confidence_score",
    "detected_availability", "reply_to_sender", "auto_relay_target_name",
    "auto_relay_message", "unblocked_downstream_task_id", "unblocked_notification_message",
)


# =====================================================================
# Configuration & layer state
# =====================================================================

def _key(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if value.lower().startswith("your-") else value


def _config() -> dict:
    """Read config on every call so .env / env changes apply without a restart."""
    primary = os.getenv("OPENCODE_MODEL", "minimax-m2.5-free").strip()
    fallbacks = os.getenv("OPENCODE_FALLBACK_MODELS", "mimo-v2-flash-free,big-pickle,gpt-5-nano")
    models = [primary] + [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != primary]
    return {
        "force_offline": os.getenv("LLM_FORCE_OFFLINE", "").strip().lower() in ("1", "true", "yes"),
        "timeout": float(os.getenv("LLM_TIMEOUT_SECONDS", "6")),
        "opencode_url": os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/v1").strip(),
        "opencode_key": _key("OPENCODE_API_KEY"),
        "opencode_models": models,
        "backup_url": os.getenv("BACKUP_BASE_URL", "https://api.groq.com/openai/v1").strip(),
        "backup_key": _key("BACKUP_API_KEY"),
        "backup_model": os.getenv("BACKUP_MODEL", "llama-3.3-70b-versatile").strip(),
        # Local Ollama: native API (no /v1); empty OLLAMA_MODEL turns the layer off
        "ollama_url": re.sub(r"/v1/?$", "", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")),
        "ollama_model": os.getenv("OLLAMA_MODEL", "").strip(),
        "ollama_timeout": float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "25")),
        "ollama_max_tokens": int(os.getenv("OLLAMA_MAX_TOKENS", "400")),
    }


_lock = threading.Lock()
_cooldowns: dict[str, float] = {}
_clients: dict[tuple, OpenAI] = {}
_state: dict[str, Any] = {
    "active_layer": None,
    "last_model": None,
    "last_errors": [],
    "last_call_at": None,
    "last_latency_ms": None,
    "calls": {LAYER_OPENCODE: 0, LAYER_BACKUP: 0, LAYER_LOCAL: 0, LAYER_OFFLINE: 0},
    "ollama_warm": False,
}


def _cooling(key: str) -> bool:
    return _cooldowns.get(key, 0) > time.time()


def _cool(key: str, seconds: float) -> None:
    _cooldowns[key] = time.time() + seconds


def _client(base_url: str, api_key: str, timeout: float) -> OpenAI:
    k = (base_url, api_key, timeout)
    if k not in _clients:
        _clients[k] = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
    return _clients[k]


def _record(layer: str, model: Optional[str], errors: list, started: float) -> dict:
    latency = int((time.time() - started) * 1000)
    with _lock:
        _state.update(active_layer=layer, last_model=model, last_errors=errors[-6:],
                      last_call_at=datetime.now().isoformat(timespec="seconds"), last_latency_ms=latency)
        _state["calls"][layer] += 1
    return {"layer": layer, "model": model, "latency_ms": latency, "errors": errors[-6:]}


def _err(where: str, exc: Exception) -> str:
    return f"{where}: {type(exc).__name__}: {str(exc)[:160]}"


# =====================================================================
# Layer 3: bulletproof JSON repair
# =====================================================================

class JSONRepairError(ValueError):
    pass


_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)
_THINK_DANGLING_CLOSE = re.compile(r"^.*?</(?:think|thinking|reasoning)>", re.S | re.I)
_THINK_TAG = re.compile(r"</?(?:think|thinking|reasoning)>", re.I)
_FENCE = re.compile(r"```(?:json|JSON|javascript|js)?\s*(.*?)```", re.S)


def repair_json(text: str) -> Any:
    """Parse JSON from messy LLM output: thinking tags, code fences, prose, trailing commas, smart quotes."""
    if not text or not text.strip():
        raise JSONRepairError("empty output")
    s = _THINK_BLOCK.sub("", text)
    s = _THINK_DANGLING_CLOSE.sub("", s)
    s = _THINK_TAG.sub("", s)
    fence = _FENCE.search(s)
    if fence:
        s = fence.group(1)
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        raise JSONRepairError("no JSON object found")
    start = min(starts)
    end = max(s.rfind("}"), s.rfind("]"))
    if end <= start:
        raise JSONRepairError("unterminated JSON")
    s = re.sub(r",\s*([}\]])", r"\1", s[start:end + 1])
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    try:  # Python-literal style output: single quotes, True/False/None
        py = re.sub(r"\btrue\b", "True", s)
        py = re.sub(r"\bfalse\b", "False", py)
        py = re.sub(r"\bnull\b", "None", py)
        return ast.literal_eval(py)
    except Exception as exc:
        raise JSONRepairError(f"unparseable JSON: {exc}") from exc


# =====================================================================
# Layers 1 + 2: LLM call with rotation, circuit breaker and instant failover
# =====================================================================

def _complete_json(cfg: dict, base_url: str, key: str, model: str, system: str, user: str, max_tokens: int) -> Any:
    client = _client(base_url, key, cfg["timeout"])
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "") if resp.choices else ""
    return repair_json(content)


def _ollama_json(cfg: dict, system: str, user: str, max_tokens: int) -> Any:
    """Ollama's native chat API: forced JSON output, and keep the model loaded between calls."""
    resp = httpx.post(f"{cfg['ollama_url']}/api/chat", timeout=cfg["ollama_timeout"], json={
        "model": cfg["ollama_model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": "json",
        "keep_alive": "2h",
        "options": {"temperature": 0.2, "num_predict": min(max_tokens, cfg["ollama_max_tokens"])},
    })
    resp.raise_for_status()
    return repair_json(resp.json().get("message", {}).get("content", ""))


def warm_up_local_llm() -> None:
    """Load the Ollama model in the background at startup, so the first real request isn't a 30-60 s cold start."""
    cfg = _config()
    if not cfg["ollama_model"]:
        return

    def run():
        try:
            httpx.post(f"{cfg['ollama_url']}/api/generate", timeout=180,
                       json={"model": cfg["ollama_model"], "keep_alive": "2h"}).raise_for_status()
            _state["ollama_warm"] = True
        except Exception as exc:  # Ollama not installed / not running: the layer is skipped until it is
            _state["ollama_warm"] = False
            _state["ollama_error"] = _err("ollama warm-up", exc)

    threading.Thread(target=run, name="ollama-warmup", daemon=True).start()


def call_llm_json(system: str, user: str, max_tokens: int = 1500, local_ok: bool = True,
                  local_prompt: Optional[tuple[str, str]] = None) -> tuple[Optional[Any], dict]:
    """Returns (parsed_json | None, meta). None means callers must use the offline engine (layer 4).

    `local_ok=False` skips the laptop's Ollama model: long generations (whole project plans) would take
    minutes on a CPU, and the offline planner answers instantly. `local_prompt` is an optional shorter
    (system, user) pair for the local model: CPU speed is dominated by how much it has to write."""
    cfg = _config()
    started = time.time()
    errors: list[str] = []
    if cfg["force_offline"]:
        return None, _record(LAYER_OFFLINE, None, ["LLM_FORCE_OFFLINE is set"], started)

    # Layer 1: primary cloud LLM with model rotation
    if not cfg["opencode_key"]:
        errors.append("opencode: OPENCODE_API_KEY not set")
    elif _cooling("opencode"):
        errors.append("opencode: unreachable recently, skipping (circuit open)")
    else:
        for model in cfg["opencode_models"]:
            if _cooling(f"opencode:{model}"):
                errors.append(f"opencode/{model}: cooling down after rate limit/timeout")
                continue
            try:
                data = _complete_json(cfg, cfg["opencode_url"], cfg["opencode_key"], model, system, user, max_tokens)
                return data, _record(LAYER_OPENCODE, model, errors, started)
            except (RateLimitError, APITimeoutError) as exc:  # rotate to the next free model
                errors.append(_err(f"opencode/{model}", exc))
                _cool(f"opencode:{model}", 60)
            except APIConnectionError as exc:  # network down -> jump straight to backup, no more waiting
                errors.append(_err(f"opencode/{model}", exc))
                _cool("opencode", 30)
                break
            except Exception as exc:  # bad model id, 5xx, unparseable output -> try next model
                errors.append(_err(f"opencode/{model}", exc))

    # Layer 2: instant backup cloud LLM
    if not cfg["backup_key"]:
        errors.append("backup: BACKUP_API_KEY not set")
    elif _cooling("backup"):
        errors.append("backup: unreachable recently, skipping (circuit open)")
    else:
        try:
            data = _complete_json(cfg, cfg["backup_url"], cfg["backup_key"], cfg["backup_model"], system, user, max_tokens)
            return data, _record(LAYER_BACKUP, cfg["backup_model"], errors, started)
        except APIConnectionError as exc:
            errors.append(_err("backup", exc))
            _cool("backup", 30)
        except Exception as exc:
            errors.append(_err("backup", exc))

    # Layer 3: local LLM on this laptop (Ollama) - keeps the AI working with no internet
    if not cfg["ollama_model"]:
        errors.append("local: OLLAMA_MODEL not set")
    elif not local_ok:
        errors.append("local: skipped for long generations (offline planner is instant)")
    elif _cooling("ollama"):
        errors.append("local: Ollama unavailable recently, skipping")
    else:
        try:
            l_system, l_user = local_prompt or (system, user)
            data = _ollama_json(cfg, l_system, l_user, max_tokens)
            _state["ollama_warm"] = True
            return data, _record(LAYER_LOCAL, cfg["ollama_model"], errors, started)
        except httpx.ConnectError as exc:  # Ollama isn't running
            errors.append(_err("local", exc))
            _cool("ollama", 60)
        except httpx.TimeoutException as exc:  # still loading, or too slow for this request
            errors.append(_err("local", exc))
            _cool("ollama", 15)
        except Exception as exc:
            errors.append(_err("local", exc))

    # Layer 4: offline engine
    return None, _record(LAYER_OFFLINE, None, errors, started)


def get_llm_status(probe: bool = False) -> dict:
    cfg = _config()
    if cfg["force_offline"]:
        configured = LAYER_OFFLINE
    elif cfg["opencode_key"] and not _cooling("opencode"):
        configured = LAYER_OPENCODE
    elif cfg["backup_key"] and not _cooling("backup"):
        configured = LAYER_BACKUP
    elif cfg["ollama_model"] and not _cooling("ollama"):
        configured = LAYER_LOCAL
    else:
        configured = LAYER_OFFLINE
    status = {
        "active_layer": _state["active_layer"] or configured,
        "configured_layer": configured,
        "last_model": _state["last_model"],
        "last_latency_ms": _state["last_latency_ms"],
        "last_call_at": _state["last_call_at"],
        "last_errors": _state["last_errors"],
        "calls": dict(_state["calls"]),
        "opencode": {
            "configured": bool(cfg["opencode_key"]),
            "base_url": cfg["opencode_url"],
            "models": cfg["opencode_models"],
            "circuit_open": _cooling("opencode"),
            "cooling_models": [m for m in cfg["opencode_models"] if _cooling(f"opencode:{m}")],
        },
        "backup": {
            "configured": bool(cfg["backup_key"]),
            "base_url": cfg["backup_url"],
            "model": cfg["backup_model"],
            "circuit_open": _cooling("backup"),
        },
        "local": {
            "configured": bool(cfg["ollama_model"]),
            "base_url": cfg["ollama_url"],
            "model": cfg["ollama_model"],
            "warm": _state["ollama_warm"],
            "circuit_open": _cooling("ollama"),
            "timeout_seconds": cfg["ollama_timeout"],
            "error": _state.get("ollama_error"),
        },
        "force_offline": cfg["force_offline"],
        "timeout_seconds": cfg["timeout"],
    }
    if probe:
        data, meta = call_llm_json(
            "Reply with JSON only.", 'Return exactly {"ok": true}', max_tokens=300)
        status["probe"] = {"ok": isinstance(data, dict) and data.get("ok") is True, **meta}
        status["active_layer"] = meta["layer"]
    return status


# =====================================================================
# Text helpers (shared by the offline engine and the guardrails)
# =====================================================================

DONE_RE = re.compile(
    r"\b(done|complete[d]?|finish(?:ed)?|wrapped up|shipped|deployed|merged|pushed|implemented|integrated|"
    r"connected|works now|working now|ready|sorted|fixed|ho\s*(?:gaya|gya|gyi|gayi|chuka|chuki)|"
    r"kar\s*(?:diya|di|dia|liya|li)|khatam)\b", re.I)
NEGATED_DONE_RE = re.compile(
    r"\b(?:not|isn'?t|aren'?t|wasn'?t|haven'?t|hasn'?t|didn'?t|never|nahi|nhi)\s+(?:yet\s+)?(?:been\s+)?"
    r"(?:done|complete[d]?|finished|ready|connected|working|hua|hui|ho)\b|\bnot yet\b|\babhi\s+(?:tak\s+)?nahi\b", re.I)
BLOCKER_RE = re.compile(
    r"\b(waiting|wait for|wait on|blocked|blocker|stuck|need(?:s|ed)?|require[sd]?|missing|don'?t have|"
    r"do not have|baa?ki|chahiye|crash(?:es|ing|ed)?|errors?|fail(?:s|ing|ed)?|broken|bug|issue|"
    r"not working|doesn'?t work|nahi chal|can'?t|cannot|unable|atk(?:a|i)|phas(?:a|gaya)|depends on|"
    r"no access|access denied|401|403|500)\b", re.I)
PROGRESS_RE = re.compile(
    r"\b(working on|started|starting|in progress|halfway|half way|almost|nearly|\d{1,3}\s?%|kar raha|"
    r"kar rahi|chal raha|ongoing|wip|drafting|building|coding|testing|debugging|implementing)\b", re.I)
RESOLUTION_RE = re.compile(
    r"\b(shared|sent|send(?:ing)? (?:it|them|over)|bhej\s*(?:diya|diye|di|dia)|de\s*(?:diya|diye|di)|gave|"
    r"given|provided|forwarded|emailed|dm'?d|uploaded|pasted|here (?:is|are)|here'?s|resolved|unblocked|"
    r"granted|access given|these are(?: the)?|this is the|here you go|use this)\b", re.I)
QUESTION_RE = re.compile(r"^(what|whats|what's|how|why|where|when|who|which|can|could|should|do|does|is|are|kya|kaise|kab|kaun)", re.I)
SECRET_RE = re.compile(r"\b(?:sk|pk|rzp|key|token|api[_ ]?key|secret|password)[_\-:=\s]+[A-Za-z0-9_\-]{6,}", re.I)
SARCASM_RE = re.compile(
    r"\b(lol|lmao|haha+|basically|kinda|sort of|sorta|i guess|technically|supposedly|should be|ish)\b|\U0001F643|\U0001F605", re.I)
WAITING_RE = re.compile(r"\b(waiting|wait for|wait on|depends on|need|pending|baa?ki)\b", re.I)
CONTRAST_RE = re.compile(
    r"(?:,\s*|\s+)\b(but|however|though|although|except|lekin|par|magar|bas|bus|just|only issue is|only problem is)\b[\s,:]*",
    re.I)

FILLER_PREFIX_RE = re.compile(
    r"^(?:(?:hey|hi|hello|ok(?:ay)?|so|yeah|yea|yes|yep|well|basically|honestly|actually|bhai|bro|yaar|arre|"
    r"arey|haan|lol|um+|uh+|hmm+|like|update|status|the)\b[\s,:!.\-]*)+", re.I)
FILLER_SUFFIX_RE = re.compile(r"(?:[\s,]*\b(?:lol|lmao|haha+|yaar|bro|bhai|ok)\b)+[\s.!]*$", re.I)
PRONOUN_PREFIX_RE = re.compile(r"^(?:i\s+am|i'm|im|am|i\s+was|still|currently|abhi|right now)\s+", re.I)

HINGLISH = [
    (r"\bho\s*(?:gaya|gya|gyi|gayi|chuka|chuki)(?:\s+(?:hai|he|h))?\b", "done"),
    (r"\bkar\s*(?:diya|di|dia|liya|li)(?:\s+(?:hai|he|h))?\b", "done"),
    (r"\bkhatam\b", "finished"),
    (r"\bbaa?ki\s*(?:hai|he|h)?\b", "still pending"),
    (r"\bpending\s+(?:hai|he|h)\b", "still pending"),
    (r"\b(ready|done|complete|live|working)\s+(?:hai|he|h)\b", r"\1"),
    (r"\bchahiye\b", "needed"),
    (r"\bnahi\s*(?:mila|mile|mili)\b", "not received"),
    (r"\bchal\s*raha\s*(?:hai|he|h)?\b", "in progress"),
    (r"\bkar\s*(?:raha|rahi)\s*(?:hu|hoon|hun|hai|he|h)?\b", "working on it"),
    (r"\bbhej\s*(?:diya|diye|di|dia)\b", "sent"),
    (r"\batk(?:a|i)\s*(?:hua|hui|hai|he|h)?\b", "stuck"),
    (r"\bkal\b", "tomorrow"),
]


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("‘", "'")).strip()


def _translate(s: str) -> str:
    for pattern, repl in HINGLISH:
        s = re.sub(pattern, repl, s, flags=re.I)
    return s


def _cap(s: str) -> str:
    s = s.strip(" ,.;:!-")
    return s[:1].upper() + s[1:] if s else s


def _clean_note(s: str) -> str:
    s = _translate(_norm_text(s))
    for _ in range(3):
        s = FILLER_PREFIX_RE.sub("", s)
    s = FILLER_SUFFIX_RE.sub("", s)
    return _cap(s)


def _strip_names(s: str, names: list[str]) -> str:
    for name in names:
        s = re.sub(rf"\s*\b(?:from|by|on|with|to|for)\s+{re.escape(name)}\b(?:'s)?", "", s, flags=re.I)
        s = re.sub(rf"\s*\b{re.escape(name)}\s+(?:se|ke paas|ki taraf se)\b", "", s, flags=re.I)
    return s


def _clean_blocker(s: str, names: list[str]) -> str:
    s = _strip_names(_translate(_norm_text(s)), names)
    for _ in range(3):
        s = FILLER_PREFIX_RE.sub("", s)
        s = PRONOUN_PREFIX_RE.sub("", s)
    s = FILLER_SUFFIX_RE.sub("", s)
    return _cap(s)


def _split_progress_blocker(message: str) -> tuple[Optional[str], Optional[str]]:
    """Split 'X is done, but waiting for Y' into ('X is done', 'waiting for Y')."""
    for m in CONTRAST_RE.finditer(message):
        before, after = message[:m.start()], message[m.end():]
        if (DONE_RE.search(before) or PROGRESS_RE.search(before)) and BLOCKER_RE.search(after):
            return before, after
        if m.group(1).lower() in ("bas", "bus", "just") and before.strip() and after.strip() \
                and (DONE_RE.search(before) or PROGRESS_RE.search(before)):
            return before, after  # "code ho gaya bas test credentials baaki hai"
    for m in re.finditer(r"\s*(?:[,;]|\s-\s)\s*", message):  # commit style: "Connected webhook, blocked on KMS keys"
        before, after = message[:m.start()], message[m.end():]
        if (DONE_RE.search(before) or PROGRESS_RE.search(before)) and not BLOCKER_RE.search(before) \
                and BLOCKER_RE.search(after):
            return before, after
    return None, None


def _extract_need(blocker_reason: str) -> Optional[str]:
    if not blocker_reason:
        return None
    m = re.search(r"(?:waiting (?:for|on)|blocked (?:on|by)|need(?:s|ed)?|require[sd]?|missing)\s+(?:the\s+)?(.+)",
                  blocker_reason, re.I)
    if m:
        return m.group(1).strip(" .")
    m = re.search(r"^(?:the\s+)?(.+?)\s+(?:still\s+)?(?:pending|needed|not received)\b", blocker_reason, re.I)
    return m.group(1).strip(" .") if m else None


def _mentioned(message: str, people: list[dict]) -> list[dict]:
    hits = []
    for p in people:
        m = re.search(rf"\b{re.escape(p['name'])}\b", message, re.I)
        if m:
            hits.append((m.start(), p))
    return [p for _, p in sorted(hits, key=lambda h: h[0])]


# =====================================================================
# Availability parsing & smart slot coordinator
# =====================================================================

_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"


def _to_24h(h: int, m: int, ampm: Optional[str]) -> tuple[int, int]:
    ampm = (ampm or "").replace(".", "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    elif not ampm and 1 <= h <= 7:  # "free at 5" during a hackathon means 5 PM
        h += 12
    return h % 24, m


def _fmt_hm(h: int, m: int) -> str:
    return datetime(2000, 1, 1, h, m).strftime("%I:%M %p").lstrip("0")


def detect_availability(text: str) -> Optional[str]:
    t = _norm_text(text).lower()
    m = re.search(r"\b(?:free|available|khali|online)\b[^.\n]{0,20}?\bin\s+(\d+)\s*(min|mins|minutes|m|hr|hrs|hour|hours)\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        return f"Free in {n} hour{'s' if n != 1 else ''}" if unit.startswith("h") else f"Free in {n} mins"
    m = re.search(rf"\b(?:free|available)\b[^.\n]{{0,15}}?{_TIME}\s*(?:-|to|–)\s*{_TIME}", t)
    if m and (m.group(3) or m.group(6)):
        a = _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3) or m.group(6))
        b = _to_24h(int(m.group(4)), int(m.group(5) or 0), m.group(6))
        return f"Free {_fmt_hm(*a)}-{_fmt_hm(*b)}"
    m = re.search(rf"\b(?:free|available|khali|online)\b[^.\n]{{0,15}}?\b(?:at|after|from|by|around|se)\s+{_TIME}", t) \
        or re.search(rf"{_TIME}\s*(?:baje|onwards)\b[^.\n]{{0,12}}\b(?:free|available|khali)", t)
    if m:
        return f"Free at {_fmt_hm(*_to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3)))}"
    m = re.search(rf"\b(?:busy|in class|in a meeting|class|meeting|lab)\b[^.\n]{{0,15}}?\b(?:until|till|til|upto|up to)\s+{_TIME}", t)
    if m:
        return f"Busy until {_fmt_hm(*_to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3)))}"
    if re.search(r"\b(?:free|available)\s+(?:now|right now)\b|\babhi free\b|\bi'?m free\b", t):
        return "Available now"
    if re.search(r"\b(offline|sleeping|asleep|unavailable|not available)\b", t):
        return "Busy"
    return None


def _parse_hm(s: str) -> Optional[tuple[int, int]]:
    m = re.search(_TIME, s, re.I)
    return _to_24h(int(m.group(1)), int(m.group(2) or 0), m.group(3)) if m else None


def parse_availability_window(avail: str, now: datetime) -> Optional[tuple[datetime, datetime]]:
    a = (avail or "Available").strip().lower()
    span = timedelta(hours=3)

    def at(hm: tuple[int, int]) -> datetime:
        dt = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        return dt if dt >= now - timedelta(minutes=30) else now

    if a in ("available", "available now", "free", "free now", "online"):
        return now, now + span
    m = re.match(r"free in (\d+) (mins|hour|hours)", a)
    if m:
        delta = timedelta(hours=int(m.group(1))) if m.group(2).startswith("h") else timedelta(minutes=int(m.group(1)))
        return now + delta, now + delta + span
    m = re.match(r"free (.+?)-(.+)", a)
    if m and _parse_hm(m.group(1)) and _parse_hm(m.group(2)):
        start, end = at(_parse_hm(m.group(1))), at(_parse_hm(m.group(2)))
        return (start, end) if end > start else (start, start + span)
    m = re.match(r"(?:free at|busy until|free after|available at|available after) (.+)", a)
    if m and _parse_hm(m.group(1)):
        start = at(_parse_hm(m.group(1)))
        return start, start + span
    detected = detect_availability(avail) if avail else None
    if detected and detected.lower() != a:
        return parse_availability_window(detected, now)
    return None  # busy / unknown


def availability_for_storage(avail: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """Turn relative availability ("Free in 15 mins") into an absolute one ("Free at 5:15 PM") before saving."""
    if not avail:
        return None
    now = now or datetime.now()
    m = re.match(r"free in (\d+) (mins|hour|hours)", avail.strip().lower())
    if m:
        w = parse_availability_window(avail, now)
        return f"Free at {_fmt_hm(w[0].hour, w[0].minute)}"
    return avail.strip()[:60]


def _round_up_15(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (-dt.minute) % 15
    return dt + timedelta(minutes=extra)


def coordinate_team_slots(participants: list[dict], tasks: list[dict], now: Optional[datetime] = None) -> dict:
    """Recommend the best overlapping 15-minute sync slot, prioritising teammates involved in blockers."""
    now = now or datetime.now()
    windows, availability = {}, []
    for p in participants:
        w = parse_availability_window(p.get("availability") or "Available", now)
        if w:
            windows[p["id"]] = w
        availability.append({
            "participant_id": p["id"], "name": p["name"], "availability": p.get("availability") or "Available",
            "is_available": bool(w),
            "free_from": _fmt_hm(w[0].hour, w[0].minute) if w else None,
            "free_until": _fmt_hm(w[1].hour, w[1].minute) if w else None,
        })

    by_id = {t["id"]: t for t in tasks}
    focus, blocked_titles = set(), []
    for t in tasks:
        if t["status"] != "Blocked":
            continue
        blocked_titles.append(t["title"])
        for pid in (t.get("owner_id"), t.get("blocked_by_participant_id")):
            if pid:
                focus.add(pid)
        up = by_id.get(t.get("depends_on_task_id"))
        if up and up.get("owner_id"):
            focus.add(up["owner_id"])
        for d in tasks:
            if d.get("depends_on_task_id") == t["id"] and d.get("owner_id"):
                focus.add(d["owner_id"])
    purpose = "Unblock sync" if focus else "Team sync / presentation rehearsal"
    if not focus:
        focus = {p["id"] for p in participants}

    names = {p["id"]: p["name"] for p in participants}
    attending = [pid for pid in focus if pid in windows]
    missing = [names[pid] for pid in focus if pid not in windows and pid in names]
    if not attending:
        return {"purpose": purpose, "recommended_slot": None, "attendees": [], "missing": missing,
                "full_overlap": False, "blocked_tasks": blocked_titles, "availability": availability,
                "reason": "Nobody involved is currently available; ask teammates to share a free time."}

    start = _round_up_15(max(windows[pid][0] for pid in attending))
    end_limit = min(windows[pid][1] for pid in attending)
    full_overlap = start + timedelta(minutes=15) <= end_limit and not missing
    slot_end = start + timedelta(minutes=15)
    attendee_names = sorted(names[pid] for pid in attending)
    reason = (f"Earliest 15-minute window where {', '.join(attendee_names)} are all free"
              + (f" to resolve: {', '.join(blocked_titles)}." if blocked_titles else "."))
    if missing:
        reason += f" {', '.join(missing)} not available - share notes with them afterwards."
    return {
        "purpose": purpose,
        "recommended_slot": {
            "start": start.isoformat(timespec="minutes"), "end": slot_end.isoformat(timespec="minutes"),
            "label": f"{_fmt_hm(start.hour, start.minute)} - {_fmt_hm(slot_end.hour, slot_end.minute)}",
            "starts_in_minutes": max(0, int((start - now).total_seconds() // 60)),
        },
        "attendees": attendee_names, "missing": missing, "full_overlap": full_overlap,
        "blocked_tasks": blocked_titles, "availability": availability, "reason": reason,
    }


# =====================================================================
# (a-d) Smart reply interpreter
# =====================================================================

class _Ctx:
    def __init__(self, message, sender, task, participants, tasks, action_type, strict=False):
        self.strict = strict
        self.message = _norm_text(message)
        self.sender = sender
        self.task = task
        self.participants = participants
        self.tasks = tasks
        self.action_type = action_type
        self.by_id = {t["id"]: t for t in tasks}
        self.pname = {p["id"]: p["name"] for p in participants}
        self.others = [p for p in participants if p["id"] != sender["id"]]
        self.upstream = self.by_id.get(task.get("depends_on_task_id")) if task else None

    def valid_name(self, name: Optional[str]) -> Optional[str]:
        if not name or not isinstance(name, str):
            return None
        for p in self.others:
            if p["name"].lower() == name.strip().lower():
                return p["name"]
        return None

    def title(self) -> str:
        return self.task["title"] if self.task else "your task"


def _relay_message(ctx: _Ctx, target: str, blocker_reason: str) -> str:
    need = _extract_need(blocker_reason)
    ask = (f"Could you please send the {need} to {ctx.sender['name']} ASAP?" if need
           else f"Could you help {ctx.sender['name']} get unblocked ASAP?")
    return (f"Hi {target}, {ctx.sender['name']} is blocked on \"{ctx.title()}\": {blocker_reason}. "
            f"{ask} The team's timeline depends on it - thank you!")


def _unblock_message(ctx: _Ctx, blocked: dict, reason: str) -> str:
    owner = ctx.pname.get(blocked.get("owner_id"), "there")
    return (f"Good news {owner}: {ctx.sender['name']} just {reason}. "
            f"Your task \"{blocked['title']}\" is unblocked and back In Progress - you're clear to continue.")


def infer_helper(ctx: _Ctx, need: str) -> Optional[str]:
    """Who can unblock a blocker that names nobody?

    1. the owner of the unfinished task this one depends on (the work it's waiting for)
    2. the one teammate whose role and tasks clearly match what's missing ("credentials" -> the backend dev)"""
    up = ctx.upstream
    if up and up["status"] != "Complete":
        owner = ctx.pname.get(up.get("owner_id"))
        if owner and owner != ctx.sender["name"]:
            return owner
    if not ctx.others or not need:
        return None
    profiles = []
    for p in ctx.others:
        titles = " ".join(f"{t.get('title', '')} {t.get('description', '') or ''}" for t in ctx.tasks if t.get("owner_id") == p["id"])
        profiles.append({"title": f"{p.get('role') or ''} {titles}", "description": ""})
    scores = task_match_scores(need, profiles)
    best = max(scores, default=0)
    return ctx.others[scores.index(best)]["name"] if best >= 1 and scores.count(best) == 1 else None


def _find_unblock_target(ctx: _Ctx, has_resolution: bool, own_status: str) -> tuple[Optional[dict], Optional[str]]:
    """Which teammate's Blocked task does this message resolve?"""
    sid = ctx.sender["id"]
    blocked = [t for t in ctx.tasks if t["status"] == "Blocked" and t.get("owner_id") != sid]
    if has_resolution and blocked:
        mentioned_ids = {p["id"] for p in _mentioned(ctx.message, ctx.others)}
        for t in blocked:  # 1. blocked explicitly on the sender
            if t.get("blocked_by_participant_id") == sid and (not mentioned_ids or t.get("owner_id") in mentioned_ids):
                return t, f"resolved your blocker ({t.get('blocker_reason') or 'blocker'})"
        for t in blocked:  # 2. owner named in the message ("shared creds with Kabir")
            if t.get("owner_id") in mentioned_ids:
                return t, f"reported: \"{ctx.message}\""
        words = {w for w in re.findall(r"[a-z]{4,}", ctx.message.lower())} - {"with", "have", "just", "sent", "shared", "from", "that", "this", "them"}
        for t in blocked:  # 3. blocker topic overlaps ("credentials")
            if words & set(re.findall(r"[a-z]{4,}", (t.get("blocker_reason") or "").lower())):
                return t, f"reported: \"{ctx.message}\""
    if own_status == "Complete" and ctx.task:  # 4. sender finished the upstream task
        for t in blocked:
            if t.get("depends_on_task_id") == ctx.task["id"]:
                return t, f"completed \"{ctx.task['title']}\", which your task depends on"
    return None, None


def _offline_analyze(ctx: _Ctx) -> tuple[dict, dict]:
    msg, task = ctx.message, ctx.task
    names = [p["name"] for p in ctx.participants]
    current = task["status"] if task else "Pending"
    existing_note = (task or {}).get("progress_note") or ""

    has_done = bool(DONE_RE.search(msg)) and not NEGATED_DONE_RE.search(msg)
    has_block = bool(BLOCKER_RE.search(msg)) or ctx.action_type == "SOS_BLOCKER"
    has_progress = bool(PROGRESS_RE.search(msg)) or bool(NEGATED_DONE_RE.search(msg))
    has_resolution = bool(RESOLUTION_RE.search(msg) or SECRET_RE.search(msg))
    sarcastic = bool(SARCASM_RE.search(msg))
    progress_part, blocker_part = _split_progress_blocker(msg)
    if blocker_part and not has_block:  # Hinglish split: "... bas test credentials baaki hai"
        has_block = True

    # A pure "I sent X to Kabir" message is about someone else's task, not a blocker on ours
    if has_resolution and has_block and not blocker_part and not BLOCKER_RE.search(re.sub(r"\bneed(?:s|ed)?\b", "", msg)):
        has_block = False

    if has_block:
        status = "Blocked"
    elif has_done:
        status = "Complete"
    elif has_progress:
        status = "In Progress"
    else:
        status = current

    blocker_reason = blocked_by = None
    if status == "Blocked":
        if blocker_part:
            progress_note = _clean_note(_strip_names(progress_part, names)) or existing_note
            blocker_reason = _clean_blocker(blocker_part, names)
            scope = blocker_part
        else:
            progress_note = existing_note or "Blocked"
            blocker_reason = _clean_blocker(msg, names)
            scope = msg
        # Prefer whoever is named after the blocker words: "Sent the keys to Kabir, but I'm stuck waiting
        # for DB access from Priya" is blocked by Priya, not Kabir
        hit = BLOCKER_RE.search(scope)
        people = (_mentioned(scope[hit.start():], ctx.others) if hit else []) \
            or _mentioned(scope, ctx.others) or _mentioned(msg, ctx.others)
        if people:
            blocked_by = people[0]["name"]
        else:  # nobody named ("Missing test credentials"): work out who can unblock it
            blocked_by = infer_helper(ctx, blocker_reason or msg)
        if blocked_by == ctx.sender["name"]:
            blocked_by = None
    elif status in ("Complete", "In Progress") and (has_done or has_progress):
        progress_note = _clean_note(msg) or existing_note
    else:
        progress_note = existing_note

    unblock, unblock_reason = _find_unblock_target(ctx, has_resolution, status)
    signals = {"done": has_done, "blocker": has_block, "progress": has_progress,
               "resolution": has_resolution, "sarcastic": sarcastic, "split": bool(blocker_part)}

    confidence = 0.55
    if has_block or has_done or has_progress or unblock:
        confidence += 0.2
    if blocker_part:
        confidence += 0.1
    if sarcastic:
        confidence -= 0.2
    if not (has_block or has_done or has_progress or unblock):
        confidence = 0.35

    result = {
        "task_id": task["id"] if task else None,
        "new_status": status,
        "progress_note": progress_note,
        "is_blocked": status == "Blocked",
        "blocker_reason": blocker_reason,
        "blocked_by_teammate_name": blocked_by,
        "contradiction_warning": None,
        "confidence_score": round(max(0.2, min(0.95, confidence)), 2),
        "detected_availability": detect_availability(msg),
        "reply_to_sender": "",
        "auto_relay_target_name": blocked_by if status == "Blocked" else None,
        "auto_relay_message": _relay_message(ctx, blocked_by, blocker_reason) if status == "Blocked" and blocked_by else None,
        "unblocked_downstream_task_id": unblock["id"] if unblock else None,
        "unblocked_notification_message": _unblock_message(ctx, unblock, unblock_reason) if unblock else None,
    }
    return result, signals


def _default_reply(ctx: _Ctx, r: dict, signals: dict) -> str:
    name, title = ctx.sender["name"], ctx.title()
    unblocked = ctx.by_id.get(r.get("unblocked_downstream_task_id"))
    unblocked_owner = ctx.pname.get(unblocked.get("owner_id")) if unblocked else None
    if r["contradiction_warning"]:
        up = ctx.upstream
        return (f"Thanks {name}! I can't mark \"{title}\" Complete yet - it depends on "
                f"{ctx.pname.get(up.get('owner_id'), 'a teammate')}'s \"{up['title']}\", which is still {up['status']}. "
                f"I've kept your task {r['new_status']}; once that's done, please re-run the full end-to-end test.")
    if r["new_status"] == "Blocked":
        done = f"logged your progress (\"{r['progress_note']}\") and " if signals.get("split") and r["progress_note"] else ""
        relay = (f" I've pinged {r['auto_relay_target_name']} to help unblock you." if r["auto_relay_target_name"]
                 else " I'll flag this to the team.")
        return f"Got it {name} - {done}marked \"{title}\" Blocked: {r['blocker_reason']}.{relay}"
    if unblocked and r["new_status"] == (ctx.task or {}).get("status"):
        return f"Thanks {name}. I've unblocked {unblocked_owner}'s task \"{unblocked['title']}\" and let them know."
    if r["new_status"] == "Complete":
        extra = f" I've told {unblocked_owner} they're unblocked." if unblocked else ""
        return f"Nice work, {name}. \"{title}\" is marked Complete.{extra}"
    if signals.get("progress") or signals.get("done"):
        return f"Thanks {name}, updated \"{title}\" ({r['new_status']}): {r['progress_note']}."
    if r["detected_availability"]:
        return f"Thanks {name}, noted your availability: {r['detected_availability']}."
    return (f"Thanks {name}! I couldn't tell the exact status of \"{title}\" - "
            f"is it done, in progress, or blocked on something?")


def _finalize(raw: dict, offline: dict, signals: dict, ctx: _Ctx, from_llm: bool) -> dict:
    """Validate an answer and enforce the guardrails. Applied to LLM and offline answers alike."""
    r = {k: raw.get(k, offline.get(k)) for k in RESULT_KEYS}
    r["task_id"] = ctx.task["id"] if ctx.task else None
    status = r.get("new_status")
    status = next((s for s in STATUSES if isinstance(status, str) and s.lower() == status.strip().lower()), offline["new_status"])

    # (f) A Blocked answer needs a blocker in THIS message. A plain "done" / "in progress" isn't one, even if the
    # task was blocked before: LLMs tend to carry an old blocker forward ("Awaiting final verification") forever.
    # Nor is a question ("What are credentials?"), a one-word ack ("Okay") or an answer the model itself doubts.
    question = ctx.message.strip().endswith("?") or bool(QUESTION_RE.match(ctx.message.strip()))
    try:
        unsure = float(raw.get("confidence_score")) < 0.6
    except (TypeError, ValueError):
        unsure = False
    if status == "Blocked" and from_llm and not signals["blocker"] and (
            signals["done"] or signals["progress"] or question or len(ctx.message.split()) <= 3 or unsure):
        current = ctx.task["status"] if ctx.task else "Pending"
        status = (offline["new_status"] if offline["new_status"] != "Blocked"
                  else "Complete" if signals["done"] else "In Progress" if signals["progress"] or current == "Blocked" else current)
        r["blocker_reason"] = r["blocked_by_teammate_name"] = r["auto_relay_target_name"] = r["auto_relay_message"] = None
        r["progress_note"] = offline["progress_note"] or r.get("progress_note")
        r["reply_to_sender"] = None  # the LLM's reply talked about the blocker: regenerate it

    # (g) Sharing something with a blocked teammate ("123456 - these are the credentials") resolves THEIR task;
    # it doesn't finish the sender's own task unless they actually say so.
    helping = signals["resolution"] or offline["unblocked_downstream_task_id"] or raw.get("unblocked_downstream_task_id")
    if from_llm and status == "Complete" and not signals["done"] and helping:
        status = ctx.task["status"] if ctx.task and ctx.task["status"] != "Blocked" else offline["new_status"]
        r["reply_to_sender"] = None

    # (e) Helping a teammate is not a blocker: "I have shared the credentials with Kabir" resolves Kabir's
    # blocker. Small models sometimes read it as the sender being Blocked (and relay a fake blocker to Kabir).
    if status == "Blocked" and signals["resolution"] and not signals["blocker"]:
        status = offline["new_status"] if offline["new_status"] != "Blocked" else (
            ctx.task["status"] if ctx.task and ctx.task["status"] != "Blocked" else "In Progress")
        r["blocker_reason"] = r["blocked_by_teammate_name"] = r["auto_relay_target_name"] = r["auto_relay_message"] = None

    # (b) Partial progress + missing requirement is NEVER Complete, and not just "In Progress" either:
    # "Finished the UI, waiting on Kabir's API" must surface as Blocked so the coordinator can relay it
    if signals["blocker"] and offline["new_status"] == "Blocked" and status in ("Complete", "In Progress"):
        status = "Blocked"
        r["progress_note"] = offline["progress_note"]
        r["blocker_reason"] = r.get("blocker_reason") or offline["blocker_reason"]
    if status == "Blocked":
        r["blocker_reason"] = (r.get("blocker_reason") or offline["blocker_reason"] or ctx.message)
        r["blocked_by_teammate_name"] = (ctx.valid_name(r.get("blocked_by_teammate_name")) or offline["blocked_by_teammate_name"]
                                         or infer_helper(ctx, r["blocker_reason"]))
    else:
        r["blocker_reason"] = r["blocked_by_teammate_name"] = None
    r["new_status"] = status
    r["is_blocked"] = status == "Blocked"
    if not isinstance(r.get("progress_note"), str) or not r["progress_note"].strip():
        r["progress_note"] = offline["progress_note"] or ""

    # (d) Cross-teammate relay
    target = ctx.valid_name(r.get("auto_relay_target_name")) if r["is_blocked"] else None
    # Only relay when THIS message reports the blocker: a question or "ok" on an already-blocked task
    # shouldn't re-send the same request (Auto-Pilot and the Ping button handle reminders)
    if r["is_blocked"] and not target and (signals["blocker"] or from_llm):
        target = r["blocked_by_teammate_name"]
    r["auto_relay_target_name"] = target
    if target and not (isinstance(r.get("auto_relay_message"), str) and r["auto_relay_message"].strip()):
        r["auto_relay_message"] = _relay_message(ctx, target, r["blocker_reason"])
    if not target:
        r["auto_relay_message"] = None

    # (d) Auto-unblock: only a real Blocked task owned by someone else
    try:
        uid = int(r.get("unblocked_downstream_task_id")) if r.get("unblocked_downstream_task_id") is not None else None
    except (TypeError, ValueError):
        uid = None
    t = ctx.by_id.get(uid)
    if not (t and t["status"] == "Blocked" and t.get("owner_id") != ctx.sender["id"]):
        uid = offline["unblocked_downstream_task_id"]
        r["unblocked_notification_message"] = offline["unblocked_notification_message"]
    r["unblocked_downstream_task_id"] = uid
    if uid and not (isinstance(r.get("unblocked_notification_message"), str) and r["unblocked_notification_message"].strip()):
        r["unblocked_notification_message"] = _unblock_message(ctx, ctx.by_id[uid], f"reported: \"{ctx.message}\"")
    if not uid:
        r["unblocked_notification_message"] = None

    # (c) Contradiction / illusion-of-progress detector (always deterministic)
    up = ctx.upstream
    contradiction_applies = bool(up and (up["status"] == "Blocked" or (ctx.strict and up["status"] != "Complete")))
    # An LLM that refused a clear "done" on its own, where the rule doesn't apply, is overruled (no false blocks)
    if (from_llm and raw.get("contradiction_warning") and not contradiction_applies and status != "Complete"
            and signals["done"] and not signals["blocker"]):
        status = r["new_status"] = "Complete"
        r["reply_to_sender"] = None
    r["contradiction_warning"] = None
    # A clear "done" counts as a completion claim even when the LLM already downgraded it on its own:
    # the sender must still be told why it didn't count.
    claims_done = status == "Complete" or (signals["done"] and not signals["blocker"])
    # Only an explicitly Blocked upstream refuses completion, unless the project enables strict dependency lock
    if claims_done and contradiction_applies:
        up_owner = ctx.pname.get(up.get("owner_id"), "a teammate")
        r["new_status"] = "In Progress"
        r["is_blocked"] = False
        r["contradiction_warning"] = (
            f"{ctx.sender['name']} reported \"{ctx.title()}\" as done, but it depends on \"{up['title']}\" "
            f"({up_owner}) which is still {up['status']}"
            + (f" ({up['blocker_reason']})" if up.get("blocker_reason") else "")
            + ". Completion refused until the upstream task is Complete.")
        r["progress_note"] = f"{r['progress_note']} (unverified: upstream \"{up['title']}\" still {up['status']})".strip()
        r["reply_to_sender"] = None  # regenerate below with the explanation
        if uid and ctx.by_id[uid].get("depends_on_task_id") == ctx.task["id"]:  # completion refused -> no unblock
            r["unblocked_downstream_task_id"] = r["unblocked_notification_message"] = None

    try:
        r["confidence_score"] = round(max(0.0, min(1.0, float(r.get("confidence_score")))), 2)
    except (TypeError, ValueError):
        r["confidence_score"] = offline["confidence_score"]
    av = r.get("detected_availability")
    r["detected_availability"] = av.strip() if isinstance(av, str) and av.strip() and av.lower() != "null" else offline["detected_availability"]
    if not (isinstance(r.get("reply_to_sender"), str) and r["reply_to_sender"].strip()):
        r["reply_to_sender"] = _default_reply(ctx, r, signals)
    if not from_llm and signals.get("sarcastic") and r["new_status"] != "Complete":
        r["reply_to_sender"] += " (Sounds like it's not 100% there yet - I've kept the board accurate.)"
    # People never see database ids: "the integration (task 77)" -> "the integration", "task 78" -> its title
    for key in ("reply_to_sender", "auto_relay_message", "unblocked_notification_message", "contradiction_warning",
                "progress_note", "blocker_reason"):
        r[key] = _humanize_task_refs(r.get(key), ctx)
    return r


_TASK_REF_PAREN = re.compile(r"\s*\(\s*(?:task(?:\s*id)?\s*)?#?\s*(\d+)\s*\)", re.I)
_TASK_REF = re.compile(r"\btask(?:\s*id)?\s*#?\s*(\d+)\b", re.I)


def _humanize_task_refs(text, ctx) -> Optional[str]:
    """Replace task ids an LLM wrote into a message with the task's title (or drop a redundant '(task 77)')."""
    if not isinstance(text, str) or not text:
        return text

    def title(m):
        t = ctx.by_id.get(int(m.group(1)))
        return f"\"{t['title']}\"" if t else m.group(0)

    text = _TASK_REF_PAREN.sub(lambda m: "" if ctx.by_id.get(int(m.group(1))) else m.group(0), text)
    return _TASK_REF.sub(title, text)


ANALYZE_SYSTEM = """You are the AI Coordinator for a student group project. You read one teammate's reply
(English, Hinglish/student slang, sarcasm, or a voice-note transcript) and update the project tracker.
Respond with ONLY one JSON object (no prose, no markdown) with exactly these keys:
{"task_id": int, "new_status": "Pending"|"In Progress"|"Blocked"|"Complete", "progress_note": str,
 "is_blocked": bool, "blocker_reason": str|null, "blocked_by_teammate_name": str|null,
 "contradiction_warning": str|null, "confidence_score": float 0-1, "detected_availability": str|null,
 "reply_to_sender": str, "auto_relay_target_name": str|null, "auto_relay_message": str|null,
 "unblocked_downstream_task_id": int|null, "unblocked_notification_message": str|null}
Rules:
1. Partial progress plus ANY missing requirement, waiting, crash or error => new_status "Blocked", is_blocked true,
   progress_note = only what IS done (e.g. "API is connected"), blocker_reason = what is missing
   (e.g. "Waiting for test credentials"). Never "Complete" in that case.
2. If the task's upstream_task is Blocked (or strict_dependencies is true and upstream is not Complete) and the
   teammate claims done, do NOT mark Complete: use "In Progress", set contradiction_warning naming the upstream task
   and its owner, and explain kindly in reply_to_sender. Otherwise a clear "done" means Complete.
3. If the blocker is caused by or needs another teammate, set blocked_by_teammate_name and auto_relay_target_name to
   that teammate's exact name and write a short, urgent, friendly auto_relay_message addressed to them.
4. If the message resolves ANOTHER teammate's Blocked task (e.g. "sent the test credentials to Kabir", or pasted keys),
   set unblocked_downstream_task_id to that task's id and write unblocked_notification_message to its owner.
   Helping someone does not change the sender's own task status.
5. Sarcastic or vague replies ("basically done lol just crashing on login") lower confidence_score; a described
   problem is a blocker.
6. detected_availability examples: "Free at 5:00 PM", "Free in 15 mins", "Busy until 7:00 PM", or null.
7. reply_to_sender: 1-2 friendly sentences to the sender. Use only teammate names from the context.
8. In every message you write, refer to a task by its title in quotes (e.g. "End-to-end integration"), never by
   its id or number - people never see the ids. Ids belong only in task_id / unblocked_downstream_task_id."""


LOCAL_ANALYZE_SYSTEM = (
    "You read one status message from a teammate about their task. Reply with ONLY this JSON: "
    '{"new_status": "Pending" | "In Progress" | "Blocked" | "Complete", "blocker_reason": string or null, '
    '"blocked_by_teammate_name": string or null, "progress_note": string}. '
    "Rules: waiting on someone or something, or stuck, means Blocked, even if part of the work is done. "
    "Finished, done, merged or shipped means Complete. Started or working on it means In Progress. "
    "blocked_by_teammate_name must be one of the given teammates or null. Keep progress_note under 12 words."
)


def analyze_participant_reply(message: str, sender: dict, task: Optional[dict], participants: list[dict],
                              tasks: list[dict], action_type: Optional[str] = None,
                              strict_dependencies: bool = False) -> tuple[dict, dict]:
    """Interpret a teammate's reply. Returns (result, llm_meta). Never raises on LLM failure."""
    ctx = _Ctx(message, sender, task, participants, tasks, action_type, strict=strict_dependencies)
    offline, signals = _offline_analyze(ctx)

    compact_tasks = [{k: t.get(k) for k in ("id", "title", "owner_name", "status", "depends_on_task_id",
                                            "blocker_reason", "blocked_by_participant_id")} for t in tasks]
    user = json.dumps({
        "sender": {"id": sender["id"], "name": sender["name"], "role": sender.get("role")},
        "task": {k: task.get(k) for k in ("id", "title", "status", "progress_note", "depends_on_task_id")} if task else None,
        "upstream_task": {k: ctx.upstream.get(k) for k in ("id", "title", "owner_name", "status", "blocker_reason")} if ctx.upstream else None,
        "team": [{"id": p["id"], "name": p["name"], "role": p.get("role")} for p in participants],
        "tasks": compact_tasks,
        "sender_tasks": [{k: t.get(k) for k in ("id", "title", "status")} for t in tasks if t.get("owner_id") == sender["id"]],
        "channel": action_type or "chat",
        "strict_dependencies": strict_dependencies,
        "message": ctx.message,
    }, ensure_ascii=False)
    local_user = json.dumps({
        "sender": sender["name"],
        "task": task.get("title") if task else None,
        "waiting_on_task": {k: ctx.upstream.get(k) for k in ("title", "owner_name", "status")} if ctx.upstream else None,
        "teammates": [p["name"] for p in participants if p["id"] != sender["id"]],
        "message": ctx.message,
    }, ensure_ascii=False)
    raw, meta = call_llm_json(ANALYZE_SYSTEM, user, local_prompt=(LOCAL_ANALYZE_SYSTEM, local_user))
    if isinstance(raw, dict):
        # Missing fields (the local model returns only a few) are filled from the offline analysis
        result = _finalize(raw, offline, signals, ctx, from_llm=True)
    else:
        if raw is not None:
            meta["errors"] = meta.get("errors", []) + ["LLM returned non-object JSON; used offline engine"]
        result = _finalize(offline, offline, signals, ctx, from_llm=False)
    meta["signals"] = signals
    return result, meta


# =====================================================================
# (f) Executive risk, milestone & handoff report
# =====================================================================

_report_cache: dict[str, tuple[float, dict]] = {}


def _downstream(task_id: int, dependents: dict[int, list[int]]) -> list[int]:
    seen, stack = [], list(dependents.get(task_id, []))
    while stack:
        tid = stack.pop(0)
        if tid not in seen:
            seen.append(tid)
            stack.extend(dependents.get(tid, []))
    return seen


def generate_project_report(project: dict, participants: list[dict], tasks: list[dict], logs: list[dict],
                            use_ai: bool = True) -> dict:
    pname = {p["id"]: p["name"] for p in participants}
    by_id = {t["id"]: t for t in tasks}
    dependents: dict[int, list[int]] = {}
    for t in tasks:
        if t.get("depends_on_task_id") in by_id:
            dependents.setdefault(t["depends_on_task_id"], []).append(t["id"])

    total = len(tasks) or 1
    complete = [t for t in tasks if t["status"] == "Complete"]
    blocked = [t for t in tasks if t["status"] == "Blocked"]

    cascading = []
    for b in blocked:
        for tid in _downstream(b["id"], dependents):
            d = by_id[tid]
            if d["status"] != "Complete":
                cascading.append({
                    "at_risk_task_id": d["id"], "at_risk_task": d["title"], "owner": pname.get(d.get("owner_id")),
                    "caused_by_task_id": b["id"], "caused_by_task": b["title"], "caused_by_owner": pname.get(b.get("owner_id")),
                    "blocker_reason": b.get("blocker_reason"),
                })

    bottlenecks = []
    for t in tasks:
        down = _downstream(t["id"], dependents)
        if t["status"] != "Complete" and (down or t["status"] == "Blocked"):
            bottlenecks.append({
                "task_id": t["id"], "title": t["title"], "owner": pname.get(t.get("owner_id")), "status": t["status"],
                "downstream_count": len(down), "blocks_task_ids": down,
                "reason": (f"Blocked: {t.get('blocker_reason') or 'unspecified'}" if t["status"] == "Blocked"
                           else f"{len(down)} downstream task(s) wait on this"),
            })
    bottlenecks.sort(key=lambda b: (b["status"] != "Blocked", -b["downstream_count"]))

    handoffs = []
    for t in tasks:
        up = by_id.get(t.get("depends_on_task_id"))
        if up:
            handoffs.append({
                "type": "DEPENDENCY", "from_task_id": up["id"], "from_task": up["title"], "from_owner": pname.get(up.get("owner_id")),
                "to_task_id": t["id"], "to_task": t["title"], "to_owner": pname.get(t.get("owner_id")),
                "state": "Delivered" if up["status"] == "Complete" else ("Stalled" if up["status"] == "Blocked" else "Waiting"),
            })
        if t["status"] == "Blocked" and t.get("blocked_by_participant_id"):
            handoffs.append({
                "type": "BLOCKER", "from_owner": pname.get(t["blocked_by_participant_id"]), "to_owner": pname.get(t.get("owner_id")),
                "to_task_id": t["id"], "to_task": t["title"], "need": t.get("blocker_reason"), "state": "Needed now",
            })

    health = 50 + 50 * len(complete) / total - 15 * len(blocked) - 8 * len(cascading)
    health = int(max(0, min(100, round(health))))

    actions = []
    for b in blocked:
        helper = pname.get(b.get("blocked_by_participant_id"))
        actions.append(f"Unblock \"{b['title']}\" ({pname.get(b.get('owner_id'))}): {b.get('blocker_reason') or 'blocker'}"
                       + (f" - {helper} must act now." if helper else "."))
    for c in cascading:
        actions.append(f"Warn {c['owner']}: \"{c['at_risk_task']}\" is at risk until \"{c['caused_by_task']}\" is unblocked.")
    for t in tasks:
        if t["status"] == "Pending" and not t.get("depends_on_task_id"):
            actions.append(f"Kick off \"{t['title']}\" ({pname.get(t.get('owner_id'), 'unassigned')}) - it has no dependencies.")
    if not actions:
        actions.append("All clear - schedule a 15-minute demo rehearsal and freeze features.")

    log_counts: dict[str, int] = {}
    for lg in logs:
        log_counts[lg["log_type"]] = log_counts.get(lg["log_type"], 0) + 1

    report = {
        "project": {"id": project["id"], "name": project["name"]},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "health_score": health,
        "health_label": "On Track" if health >= 75 else ("At Risk" if health >= 45 else "Critical"),
        "milestones": {"total": len(tasks), "complete": len(complete), "blocked": len(blocked),
                       "in_progress": sum(t["status"] == "In Progress" for t in tasks),
                       "pending": sum(t["status"] == "Pending" for t in tasks),
                       "completion_pct": round(100 * len(complete) / total)},
        "critical_path_bottlenecks": bottlenecks,
        "active_handoffs": handoffs,
        "cascading_risks": cascading,
        "recommended_next_actions": actions[:6],
        "executive_summary": (f"{len(complete)}/{len(tasks)} tasks complete, {len(blocked)} blocked, "
                              f"{len(cascading)} downstream task(s) at risk. Health {health}/100."),
        "agent_activity": log_counts,
        "ai_layer": LAYER_OFFLINE,
    }
    if not use_ai:
        return report

    sig = hashlib.md5(json.dumps([[t["id"], t["status"], t.get("blocker_reason")] for t in tasks]).encode()).hexdigest()
    cached = _report_cache.get(sig)
    if cached and time.time() - cached[0] < 60:
        report.update(cached[1])
        return report

    facts = {k: report[k] for k in ("health_score", "milestones", "critical_path_bottlenecks", "cascading_risks", "active_handoffs")}
    raw, meta = call_llm_json(
        "You are an executive project analyst for a student hackathon team. Reply with ONLY JSON: "
        '{"executive_summary": str (2 sentences), "recommended_next_actions": [str, ... max 5, concrete, name owners]}. '
        "Refer to tasks by their title in quotes, never by an id or number - readers never see ids.",
        json.dumps({"project": project["name"], "facts": facts}, ensure_ascii=False), max_tokens=900)
    extra = {"ai_layer": meta["layer"]}
    ids = SimpleNamespace(by_id={t["id"]: t for t in tasks})  # "(task 83)" -> dropped, "task 85" -> its title
    if isinstance(raw, dict):
        if isinstance(raw.get("executive_summary"), str) and raw["executive_summary"].strip():
            extra["executive_summary"] = _humanize_task_refs(raw["executive_summary"].strip(), ids)
        acts = [_humanize_task_refs(a.strip(), ids) for a in raw.get("recommended_next_actions") or [] if isinstance(a, str) and a.strip()]
        if acts:
            extra["recommended_next_actions"] = acts[:6]
    _report_cache[sig] = (time.time(), extra)
    report.update(extra)
    return report


# =====================================================================
# AI project generator
# =====================================================================

_FALLBACK_ROLES = ["Project Lead & Architect", "Backend Developer", "UI/UX & Frontend Developer",
                   "Integration Engineer", "QA & Pitch Lead"]


def _template_plan(name: str, description: str, teammates: list[str]) -> dict:
    topic = description.strip().rstrip(".") or name
    # Ordered so any prefix is a coherent plan; dependencies only point backwards
    blueprint = [
        ("Define scope, user stories & architecture", f"Agree on MVP scope and system design for {name}: {topic}.", None, "Day 1 - 11:00 AM"),
        ("Design UI screens & clickable prototype", "Design the main user flows in Figma and agree on the look.", 0, "Day 1 - 3:00 PM"),
        ("Design the database schema", "Model the core entities and relationships.", 0, "Day 1 - 1:00 PM"),
        ("Build core backend APIs", "Implement the endpoints the screens need, with validation.", 2, "Day 1 - 6:00 PM"),
        ("Build the frontend screens", "Turn the designs into responsive, working screens.", 1, "Day 1 - 7:00 PM"),
        ("Integrate frontend with backend APIs", "Wire the UI to the live API and handle errors and loading states.", 3, "Day 2 - 10:00 AM"),
        ("Write automated tests", "Cover the critical flows with tests and fix what they catch.", 5, "Day 2 - 12:00 PM"),
        ("End-to-end testing & bug fixes", "Run through every feature on real devices and fix issues.", 5, "Day 2 - 1:00 PM"),
        ("Deploy the app", "Deploy, configure environment variables, and smoke-test production.", 7, "Day 2 - 2:00 PM"),
        ("Prepare pitch deck & demo script", "Slides, a 3-minute demo script, and a rehearsal.", 0, "Day 2 - 3:00 PM"),
    ]
    count = min(len(blueprint), max(4, 2 * len(teammates)))  # about two tasks per teammate
    chosen = blueprint[:count - 1] + [blueprint[-1]] if count < len(blueprint) else blueprint
    index = {b[0]: i for i, b in enumerate(chosen)}
    tasks = []
    for i, (ti, de, dep, dl) in enumerate(chosen):
        dep_title = blueprint[dep][0] if dep is not None else None
        tasks.append({"title": ti, "description": de, "owner": teammates[i % len(teammates)],
                      "depends_on": index.get(dep_title) if dep_title in index and index[dep_title] < i else None,
                      "deadline": dl})
    return {
        "teammates": [{"name": t, "role": _FALLBACK_ROLES[i % len(_FALLBACK_ROLES)]} for i, t in enumerate(teammates)],
        "tasks": tasks,
    }


def generate_project_plan(project_name: str, description: str, teammates: list[str]) -> tuple[dict, dict]:
    """Returns ({"teammates": [{name, role}], "tasks": [{title, description, owner, depends_on, deadline}]}, meta)."""
    teammates = [t.strip() for t in teammates if t and t.strip()]
    fallback = _template_plan(project_name, description, teammates)
    raw, meta = call_llm_json(
        "You plan student hackathon projects. Reply with ONLY JSON: "
        '{"teammates": [{"name": str, "role": str}], "tasks": [{"title": str, "description": str, "owner": str, '
        '"depends_on": int|null (0-based index of an EARLIER task), "deadline": str like "Day 1 - 6:00 PM"}]}. '
        "Give each teammate about TWO concrete tasks (so progress is tracked per task, not per person), at most 10 in "
        "total, using only the given names. Only add depends_on when the work truly cannot start earlier.",
        json.dumps({"project_name": project_name, "description": description, "teammates": teammates}, ensure_ascii=False),
        local_ok=False)
    if not isinstance(raw, dict):
        meta["source"] = "offline template"
        return fallback, meta

    lookup = {t.lower(): t for t in teammates}
    roles = {}
    for tm in raw.get("teammates") or []:
        if isinstance(tm, dict) and str(tm.get("name", "")).lower() in lookup and tm.get("role"):
            roles[lookup[str(tm["name"]).lower()]] = str(tm["role"])[:80]
    tasks = []
    for i, t in enumerate((raw.get("tasks") or [])[:10]):
        if not isinstance(t, dict) or not str(t.get("title", "")).strip():
            continue
        dep = t.get("depends_on")
        dep = dep if isinstance(dep, int) and 0 <= dep < len(tasks) else None
        tasks.append({
            "title": str(t["title"]).strip()[:120],
            "description": str(t.get("description") or "").strip()[:400],
            "owner": lookup.get(str(t.get("owner", "")).lower(), teammates[len(tasks) % len(teammates)]),
            "depends_on": dep,
            "deadline": str(t.get("deadline") or "").strip()[:40],
        })
    if len(tasks) < 3:
        meta["source"] = "offline template (LLM plan too small)"
        return fallback, meta
    meta["source"] = "llm"
    return {
        "teammates": [{"name": t, "role": roles.get(t, _FALLBACK_ROLES[i % len(_FALLBACK_ROLES)])} for i, t in enumerate(teammates)],
        "tasks": tasks,
    }, meta


# =====================================================================
# GitHub repo -> AI task division
# =====================================================================

# Task kind -> role keywords that make someone a good owner
ROLE_KEYWORDS = {
    "lead": ("lead", "manager", "architect", "pm", "product", "scrum", "captain", "head"),
    "frontend": ("frontend", "front-end", "ui", "ux", "design", "web", "react", "android", "mobile", "flutter", "app"),
    "backend": ("backend", "back-end", "api", "server", "database", "db", "full", "stack", "node", "python", "java"),
    "ml": ("ml", "ai", "data", "vision", "model", "research", "scientist"),
    "qa": ("qa", "test", "quality", "automation", "security"),
    "devops": ("devops", "cloud", "ops", "infra", "deploy", "sre", "release"),
    "docs": ("pitch", "presentation", "doc", "writer", "content", "lead", "manager", "marketing"),
}


def _readme_features(readme: str) -> list[str]:
    """Pull feature names from a README 'Features' section (bold items or bullet headings)."""
    if not readme:
        return []
    section = re.split(r"\n#+\s*[^\n]*features?[^\n]*\n", readme, maxsplit=1, flags=re.I)
    text = section[1] if len(section) > 1 else readme
    text = re.split(r"\n#{1,3}\s", text, maxsplit=1)[0]  # stop at the next heading
    # Top-level list items only (no indentation), so nested bold words aren't mistaken for features
    found = (re.findall(r"^(?:[-*]|\d+\.)\s+\*\*([^*\n]{3,60})\*\*", text, re.M)
             or re.findall(r"^(?:[-*]|\d+\.)\s+([A-Z][^\n:.]{3,50})", text, re.M)
             or re.findall(r"^#{3,4}\s+(.{3,60})$", text, re.M))
    clean = []
    for f in found:
        f = re.sub(r"[:\s]+$", "", f.strip())
        if f and f.lower() not in {x.lower() for x in clean}:
            clean.append(f)
    return clean[:4]


def _assign_owners(tasks: list[dict], members: list[dict]) -> None:
    """Give each task the best-fitting member by role, balancing load; everyone gets work when possible."""
    load = {m["name"]: 0 for m in members}

    def score(member, kind):
        role = (member.get("role") or "").lower()
        return sum(1 for k in ROLE_KEYWORDS.get(kind, ()) if re.search(rf"\b{re.escape(k)}", role))

    for t in tasks:
        if t.get("owner") in load:
            load[t["owner"]] += 1
            continue
        best = max(members, key=lambda m: (score(m, t.get("kind", "")), -load[m["name"]]))
        t["owner"] = best["name"]
        load[best["name"]] += 1
    # Balancing: even out the work (about two tasks each) - the lightest-loaded member takes the best-fitting
    # task from the busiest one until nobody has more than one task above anyone else
    by_name = {m["name"]: m for m in members}
    for _ in range(len(tasks) * 2):
        busiest = max(load, key=load.get)
        lightest = min(load, key=load.get)
        if load[busiest] - load[lightest] <= 1:
            break
        candidates = [t for t in tasks[1:] if t["owner"] == busiest] or [t for t in tasks if t["owner"] == busiest]
        pick = max(candidates, key=lambda t: score(by_name[lightest], t.get("kind", "")))
        pick["owner"] = lightest
        load[busiest] -= 1
        load[lightest] += 1


def _offline_repo_plan(profile: dict, members: list[dict], workspace: str, project_name: str) -> list[dict]:
    stack = [s.lower() for s in profile.get("stack") or []]
    features = _readme_features(profile.get("readme_excerpt", ""))
    repo = profile.get("full_name") or project_name
    company = workspace == "company"
    is_static = any("static web" in s for s in stack) and not any(s in stack for s in ("python", "node.js", "java (maven)"))
    has_ml = any(k in " ".join(stack) for k in ("vision", "opencv", "tensorflow", "pytorch", "machine learning", "keras"))

    def day(d, t):
        return f"Sprint day {d} - {t}" if company else f"Day {d} - {t}"

    tasks = [{"title": "Code review & architecture sign-off" if company else "Review the repo and agree on scope",
              "description": f"Walk through {repo}, list what works today, and agree on the MVP and the task split.",
              "kind": "lead", "depends_on": None, "deadline": day(1, "11:00 AM")}]
    feature_kinds = ["frontend", "backend"]
    if features:
        for i, f in enumerate(features[:3]):
            tasks.append({"title": f"Build and polish: {f}", "description": f"Finish the '{f}' feature described in the README.",
                          "kind": feature_kinds[i % 2], "depends_on": 0, "deadline": day(1, "5:00 PM")})
    else:
        tasks.append({"title": "Build the main screens and UI", "description": "Implement the core user flows and responsive layout.",
                      "kind": "frontend", "depends_on": 0, "deadline": day(1, "5:00 PM")})
        tasks.append({"title": "Build the data layer and APIs", "description": "Data model, storage and the endpoints the UI needs.",
                      "kind": "backend", "depends_on": 0, "deadline": day(1, "6:00 PM")})
    if is_static:
        tasks.append({"title": "Persist data and wire up the logic",
                      "description": "Save records (LocalStorage or a small backend), validation, and edge cases.",
                      "kind": "backend", "depends_on": 0, "deadline": day(1, "6:00 PM")})
    if has_ml:
        tasks.append({"title": "Train and integrate the recognition model",
                      "description": "Prepare data, tune the model, and expose it to the app.",
                      "kind": "ml", "depends_on": 0, "deadline": day(2, "11:00 AM")})
    integrate = len(tasks)
    tasks.append({"title": "Integrate everything end to end", "description": "Connect UI, logic and data; fix integration bugs.",
                  "kind": "backend", "depends_on": integrate - 1, "deadline": day(2, "12:00 PM")})
    tasks.append({"title": "QA, load and security testing" if company else "Test, fix bugs and write the test report",
                  "description": "Run through every feature, log issues, and verify the fixes.",
                  "kind": "qa", "depends_on": integrate, "deadline": day(2, "2:00 PM")})
    tasks.append({"title": "Release to production & stakeholder sign-off" if company else "Deploy and prepare the demo",
                  "description": ("Deploy, publish release notes, and collect sign-off." if company
                                  else "Deploy (e.g. GitHub Pages), update the README, and rehearse the demo."),
                  "kind": "devops" if company else "docs", "depends_on": integrate + 1, "deadline": day(2, "4:00 PM")})
    return tasks


def generate_repo_plan(profile: dict, members: list[dict], workspace: str, project_name: str,
                       description: str = "") -> tuple[list[dict], dict]:
    """Divide a GitHub repo into dependent tasks assigned by role. Returns (tasks, llm_meta).

    Each task: {title, description, owner, depends_on (earlier index or None), deadline}.
    """
    from github_repo import summary_for_prompt  # local import keeps llm_engine importable on its own

    names = {m["name"].lower(): m["name"] for m in members}
    raw, meta = call_llm_json(
        "You are a senior engineering lead splitting a GitHub project between teammates. Reply with ONLY JSON: "
        '{"tasks": [{"title": str, "description": str, "owner": str, "depends_on": int|null, "deadline": str}]}. '
        "Rules: concrete tasks grounded in the repo's real stack, files and README - about TWO per teammate (at most "
        "10) so each person's progress is tracked per task; assign each task to the teammate whose role fits best; "
        "depends_on is the 0-based index of "
        "an EARLIER task only when the work truly cannot start before it (most early tasks have null); deadlines "
        "like 'Day 1 - 5:00 PM' (or 'Sprint day 2 - 11:00 AM' for company projects). Use only the given names.",
        json.dumps({"project_name": project_name, "workspace": workspace, "goal": description,
                    "repository": summary_for_prompt(profile), "team": members}, ensure_ascii=False),
        max_tokens=1800, local_ok=False)

    tasks: list[dict] = []
    if isinstance(raw, dict):
        for t in (raw.get("tasks") or [])[:10]:
            if not isinstance(t, dict) or not str(t.get("title", "")).strip():
                continue
            dep = t.get("depends_on")
            tasks.append({
                "title": str(t["title"]).strip()[:120],
                "description": str(t.get("description") or "").strip()[:400],
                "owner": names.get(str(t.get("owner", "")).strip().lower()),  # unknown names are reassigned below
                "depends_on": dep if isinstance(dep, int) and 0 <= dep < len(tasks) else None,  # no cycles possible
                "deadline": str(t.get("deadline") or "").strip()[:40],
                "kind": "",
            })
    if len(tasks) < 3:
        meta["source"] = "offline repo planner" if raw is None else "offline repo planner (LLM plan too small)"
        tasks = _offline_repo_plan(profile, members, workspace, project_name)
    else:
        meta["source"] = "llm"
    _assign_owners(tasks, members)
    return tasks, meta


# =====================================================================
# Multi-task support: which of the sender's tasks is a message about?
# =====================================================================

_MATCH_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "have", "has", "had", "just", "now", "today", "all",
    "done", "complete", "completed", "finish", "finished", "working", "work", "progress", "task", "tasks", "start",
    "started", "update", "also", "will", "was", "are", "you", "your", "our", "but", "still", "waiting", "wait", "need",
    "needs", "blocked", "stuck", "bhai", "yaar", "hai", "gaya", "ho", "kar", "diya", "bas", "baaki", "lol", "okay",
    "build", "make", "made", "fully", "100", "end",
}


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _keywords(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _MATCH_STOPWORDS}


def task_match_scores(message: str, tasks: list[dict]) -> list[int]:
    """Keyword overlap between a message and each task (title counts double)."""
    words = _keywords(message)
    return [2 * len(words & _keywords(t.get("title", ""))) + len(words & _keywords(t.get("description", "")))
            for t in tasks]


def match_task(message: str, tasks: list[dict]) -> Optional[int]:
    """Index of the task a message refers to, or None if it can't be told apart (ask the person)."""
    if not tasks:
        return None
    if len(tasks) == 1:
        return 0
    scores = task_match_scores(message, tasks)
    best = max(scores)
    if best > 0 and scores.count(best) == 1:
        return scores.index(best)
    # Ambiguous by keywords: let the LLM decide when one is configured (offline -> None)
    raw, _ = call_llm_json(
        "A teammate sent a status message. Decide which ONE of their tasks it is about. Reply with ONLY JSON "
        '{"task_index": int|null}; use null if the message does not identify a task.',
        json.dumps({"message": message, "tasks": [{"index": i, "title": t.get("title"), "description": t.get("description", "")}
                                                  for i, t in enumerate(tasks)]}, ensure_ascii=False),
        max_tokens=200)
    idx = raw.get("task_index") if isinstance(raw, dict) else None
    return idx if isinstance(idx, int) and 0 <= idx < len(tasks) else None


def is_status_update(message: str) -> bool:
    msg = _norm_text(message)
    return bool(DONE_RE.search(msg) or PROGRESS_RE.search(msg) or BLOCKER_RE.search(msg))


def is_help_message(message: str) -> bool:
    """'Sent the credentials to Kabir' style messages are about someone else's task, not the sender's own."""
    msg = _norm_text(message)
    return bool(RESOLUTION_RE.search(msg) or SECRET_RE.search(msg)) and not DONE_RE.search(msg)
