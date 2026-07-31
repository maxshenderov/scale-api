# .claude/hooks/stop.py
"""Stop: git-commit + async push. Каждый ход летит на GitHub."""

import sys
import time
import subprocess
from _common import already_in_hook, git, git_dirty, project_dir


def main() -> int:
    if already_in_hook():
        return 0
    if not git_dirty():
        return 0

    ts = time.strftime("%Y-%m-%d %H:%M")
    git("add", "-A")
    git("commit", "-m", f"wip: auto-save {ts}")

    # push в фоне — не блокирует, не роняет хук если нет сети
    subprocess.Popen(
        ["git", "push"],
        cwd=str(project_dir()),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
