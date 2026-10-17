"""Inspect a public GitHub repository (metadata, languages, file tree, README, manifests) for AI task division.

Uses the public GitHub REST API. Set GITHUB_TOKEN in .env to raise the anonymous rate limit (60 requests/hour).
Private, missing or unreachable repos fall back to what the URL and description say.
"""
import base64
import json
import os
import re
from typing import Optional

import httpx

API = "https://api.github.com"
TIMEOUT = 8.0

# File/dir markers -> technology
STACK_MARKERS = {
    "package.json": "Node.js",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "Pipfile": "Python",
    "manage.py": "Django",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Gradle",
    "build.gradle.kts": "Gradle",
    "settings.gradle": "Gradle",
    "AndroidManifest.xml": "Android",
    "pubspec.yaml": "Flutter",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "composer.json": "PHP",
    "Gemfile": "Ruby",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    "firebase.json": "Firebase",
    "vercel.json": "Vercel",
    "netlify.toml": "Netlify",
    ".github": "GitHub Actions",
    "app.json": "React Native / Expo",
    "index.html": "Static web",
    "Procfile": "Heroku",
}
# Dependency names -> technology
DEP_MARKERS = {
    "react": "React", "next": "Next.js", "vue": "Vue", "@angular/core": "Angular", "svelte": "Svelte",
    "express": "Express", "@nestjs/core": "NestJS", "mongoose": "MongoDB", "mongodb": "MongoDB", "pg": "PostgreSQL",
    "mysql2": "MySQL", "prisma": "Prisma", "firebase": "Firebase", "tailwindcss": "Tailwind CSS", "socket.io": "Socket.IO",
    "react-native": "React Native", "expo": "Expo", "electron": "Electron", "vite": "Vite",
    "flask": "Flask", "django": "Django", "fastapi": "FastAPI", "streamlit": "Streamlit", "opencv-python": "OpenCV",
    "opencv-contrib-python": "OpenCV", "face-recognition": "face_recognition", "face_recognition": "face_recognition",
    "dlib": "dlib", "tensorflow": "TensorFlow", "keras": "Keras", "torch": "PyTorch", "scikit-learn": "scikit-learn",
    "numpy": "NumPy", "pandas": "pandas", "sqlalchemy": "SQLAlchemy", "pymongo": "MongoDB", "psycopg2": "PostgreSQL",
    "mysql-connector-python": "MySQL", "openai": "OpenAI API", "langchain": "LangChain", "pillow": "Pillow",
}


def parse_repo_url(url: str) -> Optional[tuple[str, str]]:
    """'https://github.com/owner/repo(.git)(/tree/...)' or 'owner/repo' -> ('owner', 'repo')."""
    url = (url or "").strip()
    m = re.search(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", url) or re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", url)
    if not m:
        return None
    owner, repo = m.group(1), re.sub(r"\.git$", "", m.group(2))
    return owner, repo


def _headers(raw: bool = False) -> dict:
    h = {"User-Agent": "Coordina-Hackathon", "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token and not token.lower().startswith("your-"):
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(client: httpx.Client, path: str, raw: bool = False):
    resp = client.get(API + path, headers=_headers(raw))
    if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        raise RuntimeError("GitHub API rate limit reached (set GITHUB_TOKEN in backend/.env)")
    if resp.status_code == 404:
        raise FileNotFoundError("repository not found or private")
    resp.raise_for_status()
    return resp.text if raw else resp.json()


def _file_text(client: httpx.Client, owner: str, repo: str, path: str) -> Optional[str]:
    try:
        return _get(client, f"/repos/{owner}/{repo}/contents/{path}", raw=True)[:20000]
    except Exception:
        return None


def _deps_from_manifests(client, owner, repo, root_names: set) -> tuple[list[str], dict]:
    stack, manifests = [], {}
    if "package.json" in root_names:
        text = _file_text(client, owner, repo, "package.json")
        if text:
            try:
                pkg = json.loads(text)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                manifests["package.json"] = sorted(deps)[:40]
                stack += [tech for dep, tech in DEP_MARKERS.items() if dep in deps]
            except ValueError:
                pass
    if "requirements.txt" in root_names:
        text = _file_text(client, owner, repo, "requirements.txt")
        if text:
            names = [re.split(r"[<>=~!\[; ]", line.strip(), 1)[0].lower() for line in text.splitlines()
                     if line.strip() and not line.startswith("#")]
            manifests["requirements.txt"] = names[:40]
            stack += [tech for dep, tech in DEP_MARKERS.items() if dep in names]
    return stack, manifests


def _similar_repos(client: httpx.Client, owner: str, repo: str) -> list[str]:
    """Owner's public repos whose name contains (or starts like) the requested one - catches truncated URLs."""
    try:
        repos = _get(client, f"/users/{owner}/repos?per_page=100&sort=updated")
    except Exception:
        return []
    want = repo.lower()
    names = [r["name"] for r in repos if isinstance(r, dict)]
    return [n for n in names if n.lower().startswith(want) or want in n.lower()]


def analyze_repo(url: str, description: str = "") -> dict:
    """Return a compact repo profile. Never raises: on failure 'fetched' is False and 'error' explains why."""
    parsed = parse_repo_url(url)
    if not parsed:
        return {"fetched": False, "error": "Not a GitHub repository URL", "url": url, "name": None, "stack": [],
                "readme_excerpt": "", "root_files": [], "description": description, "suggestions": []}
    owner, repo = parsed
    profile = {
        "fetched": False, "url": f"https://github.com/{owner}/{repo}", "owner": owner, "name": repo,
        "full_name": f"{owner}/{repo}", "description": description, "language": None, "languages": {}, "topics": [],
        "root_files": [], "root_dirs": [], "stack": [], "manifests": {}, "readme_excerpt": "", "stars": None,
        "default_branch": None, "error": None, "resolved_from": None, "suggestions": [],
    }
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            try:
                meta = _get(client, f"/repos/{owner}/{repo}")
            except FileNotFoundError:
                similar = _similar_repos(client, owner, repo)
                profile["suggestions"] = [f"https://github.com/{owner}/{n}" for n in similar]
                if len(similar) != 1:
                    raise
                profile["resolved_from"] = profile["url"]
                repo = similar[0]
                profile.update(name=repo, full_name=f"{owner}/{repo}", url=f"https://github.com/{owner}/{repo}")
                meta = _get(client, f"/repos/{owner}/{repo}")
            profile.update(
                fetched=True,
                full_name=meta.get("full_name", profile["full_name"]),
                description=meta.get("description") or description,
                language=meta.get("language"),
                topics=meta.get("topics") or [],
                stars=meta.get("stargazers_count"),
                default_branch=meta.get("default_branch"),
            )
            try:
                profile["languages"] = _get(client, f"/repos/{owner}/{repo}/languages")
            except Exception:
                pass
            try:
                contents = _get(client, f"/repos/{owner}/{repo}/contents")
                profile["root_files"] = sorted(c["name"] for c in contents if c.get("type") == "file")[:60]
                profile["root_dirs"] = sorted(c["name"] for c in contents if c.get("type") == "dir")[:40]
            except Exception:
                pass
            try:
                readme = _get(client, f"/repos/{owner}/{repo}/readme", raw=True)
                profile["readme_excerpt"] = re.sub(r"\n{3,}", "\n\n", readme)[:3000]
            except Exception:
                pass
            names = set(profile["root_files"]) | set(profile["root_dirs"])
            dep_stack, manifests = _deps_from_manifests(client, owner, repo, names)
            profile["manifests"] = manifests
            stack = [tech for marker, tech in STACK_MARKERS.items() if marker in names]
            stack += list(profile["languages"].keys())[:5] + dep_stack
            profile["stack"] = list(dict.fromkeys(stack))
    except Exception as exc:  # private / missing / offline / rate-limited
        profile["error"] = f"{type(exc).__name__}: {exc}"[:200]
        words = re.sub(r"[-_.]+", " ", repo)
        profile["description"] = description or f"Project based on the '{words}' repository"
        # Guess stack from the slug + description so the plan is still sensible
        text = f"{repo} {description}".lower()
        guess = [t for k, t in (("android", "Android"), ("flutter", "Flutter"), ("react", "React"), ("django", "Django"),
                                ("flask", "Flask"), ("ml", "Machine Learning"), ("face", "Computer Vision"),
                                ("attendance", "Computer Vision"), ("web", "Web"), ("api", "REST API")) if k in text]
        profile["stack"] = list(dict.fromkeys(guess))
    return profile


def recent_commits(url: str, limit: int = 30) -> list[dict]:
    """Newest-first commits of a public repo: [{sha, message, author_login, author_name, date, url}].

    Raises RuntimeError with a readable reason (not found, rate limit, offline)."""
    parsed = parse_repo_url(url)
    if not parsed:
        raise RuntimeError("Not a GitHub repository URL")
    owner, repo = parsed
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            data = _get(client, f"/repos/{owner}/{repo}/commits?per_page={limit}")
    except FileNotFoundError:
        raise RuntimeError(f"{owner}/{repo} wasn't found or is private")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Couldn't reach GitHub: {type(exc).__name__}") from exc
    out = []
    for c in data if isinstance(data, list) else []:
        commit = c.get("commit") or {}
        out.append({
            "sha": c.get("sha", ""),
            "message": commit.get("message", ""),
            "author_login": (c.get("author") or {}).get("login") or "",
            "author_name": (commit.get("author") or {}).get("name") or "",
            "date": (commit.get("author") or {}).get("date") or "",
            "url": c.get("html_url", ""),
        })
    return out


def summary_for_prompt(profile: dict) -> dict:
    """The subset of the profile worth sending to the LLM."""
    return {
        "repo": profile.get("full_name"),
        "description": profile.get("description"),
        "primary_language": profile.get("language"),
        "languages": list((profile.get("languages") or {}).keys())[:8],
        "detected_stack": profile.get("stack"),
        "topics": profile.get("topics"),
        "root_files": profile.get("root_files")[:40] if profile.get("root_files") else [],
        "root_dirs": profile.get("root_dirs")[:25] if profile.get("root_dirs") else [],
        "dependencies": profile.get("manifests"),
        "readme_excerpt": (profile.get("readme_excerpt") or "")[:2000],
    }


def commit_or_pull(repo_url: str, kind: str, ref: str) -> dict:
    """One commit ('commit', sha) or pull request ('pull', number) of the project's repo.

    Returns {sha, message, author, date, url, state}. Raises FileNotFoundError when it doesn't exist,
    RuntimeError when GitHub can't be reached."""
    parsed = parse_repo_url(repo_url)
    if not parsed:
        raise RuntimeError("Not a GitHub repository URL")
    owner, repo = parsed
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            if kind == "commit":
                c = _get(client, f"/repos/{owner}/{repo}/commits/{ref}")
                commit = c.get("commit") or {}
                return {"sha": c.get("sha", ""), "message": commit.get("message", ""),
                        "author": (c.get("author") or {}).get("login") or (commit.get("author") or {}).get("name") or "",
                        "date": (commit.get("author") or {}).get("date") or "", "url": c.get("html_url", ""), "state": "committed"}
            p = _get(client, f"/repos/{owner}/{repo}/pulls/{int(ref)}")
            return {"sha": (p.get("merge_commit_sha") or (p.get("head") or {}).get("sha") or ""), "message": p.get("title", ""),
                    "author": (p.get("user") or {}).get("login") or "", "date": p.get("merged_at") or p.get("created_at") or "",
                    "url": p.get("html_url", ""), "state": "merged" if p.get("merged_at") else p.get("state", "open"),
                    "number": p.get("number")}
    except (FileNotFoundError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Couldn't reach GitHub: {type(exc).__name__}") from exc
