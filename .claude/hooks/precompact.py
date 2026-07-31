# .claude/hooks/precompact.py
"""PreCompact: штампует git HEAD в state.md. Без LLM, без вызовов claude."""

import sys, re
from _common import already_in_hook, state_path, git_head, git


def main():
    if already_in_hook():
        return 0
    sp = state_path()
    if not sp.exists():
        return 0
    txt = sp.read_text(encoding="utf-8", errors="replace")
    head = git_head()[:12]
    # обновляем только строку последнего коммита
    txt = re.sub(r"(?m)^[0-9a-f]{7,40}\b.*$", head + " (pre-compact snapshot)", txt, count=1)
    sp.write_text(txt, encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "state: pre-compact snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
