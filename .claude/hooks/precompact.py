# .claude/hooks/precompact.py
"""PreCompact: штампует git HEAD во все активные state-файлы. Без LLM."""

import sys, re
from _common import already_in_hook, tasks_path, task_state_path, git_head, git, parse_active_slugs


def stamp_head(filepath):
    """Обновляет строку с хешем коммита в файле."""
    txt = filepath.read_text(encoding="utf-8", errors="replace")
    head = git_head()[:12]
    txt = re.sub(r"(?m)^[0-9a-f]{7,40}\b.*$", head + " (pre-compact snapshot)", txt, count=1)
    filepath.write_text(txt, encoding="utf-8")


def main():
    if already_in_hook():
        return 0

    tp = tasks_path()
    if not tp.exists():
        return 0

    slugs = parse_active_slugs(tp.read_text(encoding="utf-8", errors="replace"))
    for slug in slugs:
        sp = task_state_path(slug)
        if sp.exists():
            stamp_head(sp)

    git("add", "-A")
    git("commit", "-m", "state: pre-compact snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
