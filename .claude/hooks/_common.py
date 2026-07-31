# .claude/hooks/_common.py
"""Общая логика для хуков OKIL: guard от рекурсии, git-хелперы, пути."""

import os
import sys
import json
import subprocess
import pathlib

RECURSION_GUARD = "OKIL_HOOK_CHILD"


def already_in_hook() -> bool:
    """Guard: не запускаться внутри дочернего claude -p, порождённого хуком."""
    return os.environ.get(RECURSION_GUARD) == "1"


def child_env() -> dict:
    """Копия env с флагом рекурсии для дочернего вызова claude."""
    e = os.environ.copy()
    e[RECURSION_GUARD] = "1"
    return e


def read_input() -> dict:
    """stdin-JSON от Claude Code. Пустой stdin → {}."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def project_dir() -> pathlib.Path:
    """Корень проекта: CLAUDE_PROJECT_DIR или 2 уровня вверх от .claude/hooks/."""
    p = os.environ.get("CLAUDE_PROJECT_DIR")
    if p:
        return pathlib.Path(p)
    return pathlib.Path(__file__).resolve().parents[2]


def state_path() -> pathlib.Path:
    return project_dir() / "output" / "session" / "state.md"


def log_path() -> pathlib.Path:
    return project_dir() / "wiki" / "log.md"


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Вызов git из корня проекта."""
    return subprocess.run(
        ["git", *args],
        cwd=str(project_dir()),
        capture_output=True,
        text=True,
        check=check,
    )


def git_dirty() -> bool:
    """Есть ли незакоммиченные изменения в репо."""
    r = git("status", "--porcelain")
    return bool(r.stdout.strip())


def git_head() -> str:
    """Последний коммит (полный хеш) или пустая строка."""
    r = git("log", "-1", "--format=%H")
    return r.stdout.strip() if r.returncode == 0 else ""


def is_okil_project() -> bool:
    """Проверка что мы в проекте OKIL (есть output/session/)."""
    return state_path().parent.exists()


def claude_cmd() -> list:
    """Возвращает [команда] для вызова Claude CLI (учитывает Windows .cmd)."""
    if sys.platform == "win32":
        # claude — bash-скрипт, не виден из subprocess без shell=True
        # Используем .cmd версию
        npm_claude = pathlib.Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
        if npm_claude.exists():
            return [str(npm_claude)]
    return ["claude"]
