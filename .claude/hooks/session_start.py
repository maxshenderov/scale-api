# .claude/hooks/session_start.py
"""SessionStart: инжектит реестр задач + состояния активных задач."""

import sys
import json
from _common import read_input, tasks_path, task_state_path, git_head, parse_active_slugs


def main() -> int:
    read_input()

    tp = tasks_path()
    if not tp.exists():
        return 0

    tasks_md = tp.read_text(encoding="utf-8", errors="replace")
    slugs = parse_active_slugs(tasks_md)

    # Собираем состояния активных задач
    task_states = []
    for slug in slugs:
        sp = task_state_path(slug)
        if sp.exists():
            content = sp.read_text(encoding="utf-8", errors="replace")
            # Сверка HEAD
            head = git_head()
            warning = ""
            if head and head[:12] not in content:
                warning = f"\n⚠️ HEAD не совпадает ({head[:12]})"
            task_states.append(f"### {slug}\n\n{content}{warning}")

    ctx = (
        "## Реестр задач\n\n"
        + tasks_md
    )
    if task_states:
        ctx += "\n---\n## Состояние активных задач\n\n" + "\n---\n".join(task_states)

    ctx += (
        "\n\n---\n"
        "**Первое действие:** покажи список активных задач из реестра. "
        "Спроси пользователя: продолжить задачу (номер), новая задача, или закрыть задачу."
    )

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
