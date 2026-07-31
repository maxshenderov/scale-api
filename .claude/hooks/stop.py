# .claude/hooks/stop.py
"""Stop: лёгкая проверка изменений + async git commit. Без суммаризации."""

import sys
import time
from _common import read_input, already_in_hook, git, git_dirty


def main() -> int:
    if already_in_hook():
        return 0

    read_input()

    if not git_dirty():
        return 0

    ts = time.strftime("%Y-%m-%d %H:%M")
    git("add", "-A")
    git("commit", "-m", f"wip: auto-save {ts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
