import sys

content = open(r'd:\project\OKIL\services\wms_optimizer\solver\hybrid_v7.py', 'r', encoding='utf-8').read()

old_marker = '    # ------------------------------------------------------------------\n    # Фаза 3: Joint CP-SAT Repack\n    # ------------------------------------------------------------------'

if old_marker in content:
    start = content.index(old_marker)
    next_section = content.index('    # ------------------------------------------------------------------\n    # Операции\n    # ------------------------------------------------------------------', start)
    new_phase = open(r'd:\project\OKIL\services\wms_optimizer\solver\_new_phase.txt', 'r', encoding='utf-8').read()
    content = content[:start] + new_phase + content[next_section:]
    open(r'd:\project\OKIL\services\wms_optimizer\solver\hybrid_v7.py', 'w', encoding='utf-8').write(content)
    print('OK: replaced')
else:
    print('NOT FOUND')