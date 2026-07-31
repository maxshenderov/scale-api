# .claude/hooks/stop.py
"""Stop: git-commit + push в origin (всё) + wms (только services/wms_optimizer)."""

import sys
import time
import subprocess
from _common import already_in_hook, git, git_dirty, project_dir


def push_async(remote: str, prefix: str = None):
    """Асинхронный push (фон, не блокирует). Если prefix — subtree push."""
    cwd = str(project_dir())
    if prefix:
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        cmd = ["git", "subtree", "push", "--prefix", prefix, remote, branch]
    else:
        cmd = ["git", "push", remote]
    subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    if already_in_hook():
        return 0
    if not git_dirty():
        return 0

    ts = time.strftime("%Y-%m-%d %H:%M")
    git("add", "-A")
    git("commit", "-m", f"wip: auto-save {ts}")

    push_async("origin")                          # всё
    push_async("wms", "services/wms_optimizer")   # только wms-сервис
    return 0


if __name__ == "__main__":
    sys.exit(main())
