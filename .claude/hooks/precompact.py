# .claude/hooks/precompact.py
"""PreCompact: снапшот state.md через headless claude -p --model haiku."""

import sys
import subprocess
import time
from _common import read_input, already_in_hook, child_env, state_path, git_head

PROMPT = (
    "Ты обновляешь файл состояния сессии. На основе последнего ответа ассистента "
    "ниже сформируй КОМПАКТНЫЙ markdown (<=150 токенов) строго по шаблону:\n"
    "# Session State — {date}\n"
    "> <тема>\n"
    "## Текущий шаг\n"
    "- [ ] <что делаем>\n"
    "## Что сделано\n"
    "- [x] <пункт>\n"
    "## Затронутые файлы\n"
    "- <путь>\n"
    "## Известные проблемы\n"
    "- <если есть>\n"
    "## Последний коммит\n"
    "{head}\n\n"
    "Последний ответ ассистента:\n"
    "{msg}"
)


def main() -> int:
    if already_in_hook():
        return 0

    data = read_input()
    msg = data.get("last_assistant_message", "") or ""
    if not msg.strip():
        return 0

    prompt = PROMPT.format(
        date=time.strftime("%Y-%m-%d"),
        head=git_head() or "(нет коммитов)",
        msg=msg[:8000],
    )

    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku"],
            capture_output=True,
            text=True,
            env=child_env(),
            timeout=40,
        )
        if r.returncode == 0 and r.stdout.strip():
            sp = state_path()
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(r.stdout.strip() + "\n", encoding="utf-8")
    except Exception:
        pass  # никогда не блокируем компакт

    return 0


if __name__ == "__main__":
    sys.exit(main())
