"""Proof of work: is a 'Complete' task actually done, or just claimed?

Proof levels for a Complete task:
  github       a commit or pull request on the project's repo backs it
  deliverable  a link to the work (slides, Figma, doc, demo) that actually opens
  lead         the Team Lead reviewed and approved it
  self         only the teammate's word: needs proof or a review
"""
import re
import time
from typing import Optional

import httpx

import github_repo

GITHUB_WORK_LINK = re.compile(r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(commit|pull)/([0-9a-fA-F]{7,40}|\d+)", re.I)
ANY_LINK = re.compile(r"https?://[^\s<>\"']+")

PROOF_REQUEST = ("📎 Proof needed for \"{title}\": reply with the commit or pull request link, a link to the "
                 "finished work (slides, Figma, doc, demo), or send a screenshot or file right here.")
PROOF_REMINDER = ("📎 Still waiting for proof on \"{title}\": send the commit / PR link, a link to the work, "
                  "or a screenshot or file, so your Team Lead can verify it.")


def proof_level(task) -> str:
    if task.status != "Complete":
        return "none"
    if task.github_verified:
        return "github"
    if task.deliverable_url:
        return "deliverable"
    if getattr(task, "lead_approved", False):
        return "lead"
    return "self"


def is_verified(task) -> bool:
    return proof_level(task) in ("github", "deliverable", "lead")


def links_in(text: str) -> list[str]:
    return [u.rstrip(".,);]") for u in ANY_LINK.findall(text or "")]


def check_link(url: str) -> tuple[Optional[bool], str]:
    """(True, why) if the link opens, (False, why) if it's broken, (None, why) if we couldn't check (offline).

    A sign-in page (401/403) still proves the document exists, so it counts as opening."""
    try:
        resp = httpx.get(url, timeout=6, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Coordina-LinkCheck"})
    except httpx.HTTPError as exc:
        return None, f"couldn't check the link ({type(exc).__name__})"
    if resp.status_code < 400 or resp.status_code in (401, 403):
        return True, f"opens ({resp.status_code})"
    return False, f"the link doesn't open (error {resp.status_code})"


def verify_github_link(repo_url: Optional[str], url: str) -> tuple[Optional[dict], str]:
    """Check a commit / PR link against the project's repo. Returns (commit info | None, reason)."""
    m = GITHUB_WORK_LINK.match(url)
    if not m:
        return None, "not a commit or pull request link"
    owner, repo, kind, ref = m.groups()
    linked = github_repo.parse_repo_url(f"{owner}/{repo}")
    project = github_repo.parse_repo_url(repo_url or "")
    if project and linked and (linked[0].lower(), linked[1].lower()) != (project[0].lower(), project[1].lower()):
        return None, f"that link is for {owner}/{repo}, not this project's repo ({project[0]}/{project[1]})"
    try:
        info = github_repo.commit_or_pull(f"{owner}/{repo}", kind, ref)
    except FileNotFoundError:
        return None, f"that {'commit' if kind == 'commit' else 'pull request'} doesn't exist on GitHub"
    except RuntimeError as exc:
        return None, str(exc)
    return info, "verified on GitHub"


def commit_summary(info: dict) -> str:
    first = (info.get("message") or "").strip().splitlines()[0][:80] if info.get("message") else ""
    label = f"PR #{info['number']} ({info.get('state')})" if info.get("number") else (info.get("sha") or "")[:7]
    return f"{label} {first} · {info.get('author') or 'unknown'} · {(info.get('date') or '')[:10]}".strip()


_commit_cache: dict[str, tuple[float, list[dict]]] = {}


def recent_commits_cached(repo_url: str, ttl: float = 60) -> list[dict]:
    """Recent commits, cached briefly so a burst of 'done' messages doesn't hit GitHub's rate limit."""
    hit = _commit_cache.get(repo_url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    commits = github_repo.recent_commits(repo_url)
    _commit_cache[repo_url] = (time.time(), commits)
    return commits
