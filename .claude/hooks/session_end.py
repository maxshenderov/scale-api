# .claude/hooks/session_end.py
"""SessionEnd: финальный commit → git push → append wiki/log.md."""

import sys
import time
from _common import read_input, already_in_hook, git, git_dirty, git_head, log_path, is_okil_project


def main() -> int:
    if already_in_hook():
        return 0
    if not is_okil_project():
        return 0

    data = read_input()
    reason = data.get("reason", "unknown")

    # Добить незакоммиченное (если Stop не успел)
    if git_dirty():
        git("add", "-A")
        git("commit", "-m", f"wip: session end ({reason})")

    head = git_head()

    # Append строки в wiki/log.md
    lp = log_path()
    if head:
        lp.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"- {time.strftime('%Y-%m-%d %H:%M')} | session end ({reason})"
            f" | {head[:12]}\n"
        )
        with open(lp, "a", encoding="utf-8") as f:
            f.write(line)
        git("add", str(lp))
        git("commit", "-m", "log: session end")

    # Финальный push (не падаем, если нет remote)
    git("push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
