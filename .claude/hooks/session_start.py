# .claude/hooks/session_start.py
"""SessionStart: читает state.md и инжектит в контекст новой сессии."""

import sys
import json
from _common import read_input, state_path, git_head


def main() -> int:
    read_input()
    sp = state_path()

    if not sp.exists():
        return 0

    content = sp.read_text(encoding="utf-8", errors="replace")

    # Сверка последнего коммита из state.md с реальным HEAD (СОМН-04)
    warning = ""
    head = git_head()
    if head and head[:12] not in content:
        warning = (
            "\n\n⚠️ ВНИМАНИЕ: последний коммит в state.md не совпадает с "
            f"git HEAD ({head[:12]}). Возможен дрейф — сверься с wiki/log.md."
        )

    ctx = (
        "Восстановленное состояние прошлой сессии (output/session/state.md):\n\n"
        + content
        + warning
    )

    print(
        json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": ctx,
            }
        })
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
