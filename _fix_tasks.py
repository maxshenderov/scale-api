# -*- coding: utf-8 -*-
import io

path = '1s/ERP/obrab/ZagSklad/ЗагрузкаСклада/Forms/Форма/Ext/Form/Module.bsl'
with io.open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f'Initial lines: {len(lines)}')

# Task 5: Fix SQL on line 409 (index 408)
old_sql = lines[408]
# Build correct SQL line
new_sql = ('\t\t\t"ВЫБРАТЬ\n'
           '\t\t\t|	ЕСТЬNULL(СУММА(ВЫБОР КОГДА Лико_СкладскиеСекции.КоличествоАдресов = 0 ТОГДА 3 '
           'ИНАЧЕ Лико_СкладскиеСекции.КоличествоАдресов КОНЕЦ), 0) КАК ВсегоАдресов\n')
lines[408] = new_sql
print('Task 5: SQL fixed')

# Task 3 Step 1: Replace floor label (lines 800-802, indices 799-801)
label_lines = [
    '\t\t\t// Метка этажа (с плейсхолдером процента — будет заменён после подсчёта ячеек)\n',
    '\t\t\tИмяПлейсхолдераПЦ = "%%FPCT_" + Формат(НомерЭтажа, "ЧН=0; ЧГ=0") + "%%";\n',
    '\t\t\tИмяПлейсхолдераЦВ = "%%FCLR_" + Формат(НомерЭтажа, "ЧН=0; ЧГ=0") + "%%";\n',
    '\t\t\tHTML = HTML + "<div class=""floor-header""><span class=""floor-label"">Э"\n',
    '\t\t\t\t+ Формат(НомерЭтажа, "ЧН=0; ЧГ=0") + "</span>"\n',
    '\t\t\t\t+ " <span class=""floor-pct"" style=""color:" + ИмяПлейсхолдераЦВ + """>"\n',
    '\t\t\t\t+ ИмяПлейсхолдераПЦ + "%</span></div>";\n',
]
lines[799:802] = label_lines
print('Task 3 Step 1: floor label replaced')

# Task 3 Step 2: Find sections-row close and insert after
sections_idx = None
for i, l in enumerate(lines):
    if 'конец sections-row' in l:
        sections_idx = i
        break

if sections_idx:
    pct_block = [
        '\n',
        '\t\t\t//+Лико m.shenderov 22.06.2026 — замена плейсхолдеров на реальный процент этажа\n',
        '\t\t\tЭтажСчетчик = СчетчикиПоЭтажам.Получить(НомерЭтажа);\n',
        '\t\t\tПроцентЭтажа = 0;\n',
        '\t\t\tЕсли ЭтажСчетчик <> Неопределено И ЭтажСчетчик.Всего > 0 Тогда\n',
        '\t\t\t\tПроцентЭтажа = Окр((ЭтажСчетчик.Всего - ЭтажСчетчик.Зеленых) / ЭтажСчетчик.Всего * 100, 0);\n',
        '\t\t\tКонецЕсли;\n',
        '\t\t\tЦветЭтажаHex = ЦветВHex(ЦветЗагрузкиПроцент(ПроцентЭтажа));\n',
        '\t\t\tHTML = СтрЗаменить(HTML, ИмяПлейсхолдераЦВ, ЦветЭтажаHex);\n',
        '\t\t\tHTML = СтрЗаменить(HTML, ИмяПлейсхолдераПЦ, Формат(ПроцентЭтажа, "ЧН=0; ЧГ=0"));\n',
    ]
    insert_at = sections_idx + 2  # after sections-row line + blank line
    for j, bl in enumerate(pct_block):
        lines.insert(insert_at + j, bl)
    print(f'Task 3 Step 2: floor pct block inserted after line {insert_at}')
else:
    print('ERROR: sections-row not found')

# Task 4 Step 1: Replace overall percentage header (lines 749-750)
pct_header = '\t\t\t+ "<div>Загрузка: <span style=""color:%%COLOR%%;font-weight:bold"">%%PCT%%%%</span></div>";\n'
lines[748:750] = [pct_header]
print('Task 4 Step 1: overall header replaced')

# Task 4 Step 2: Insert overall percentage before zones comment
zones_idx = None
for i, l in enumerate(lines):
    if l.strip().startswith('// === Зоны'):
        zones_idx = i
        break

if zones_idx:
    overall_block = [
        '\t//+Лико m.shenderov 22.06.2026 — замена плейсхолдеров на реальный процент занятости\n',
        '\tПроцентЗанятости = ?(ВсегоЯчеекВсего > 0, Окр((ВсегоЯчеекВсего - ЗеленыхЯчеекВсего) / ВсегоЯчеекВсего * 100, 0), 0);\n',
        '\tЦветЗанятостиHex = ЦветВHex(ЦветЗагрузкиПроцент(ПроцентЗанятости));\n',
        '\tHTML = СтрЗаменить(HTML, "%%COLOR%%", ЦветЗанятостиHex);\n',
        '\tHTML = СтрЗаменить(HTML, "%%PCT%%", Формат(ПроцентЗанятости, "ЧН=0; ЧГ=0"));\n',
        '\n',
    ]
    for j, bl in enumerate(overall_block):
        lines.insert(zones_idx + j, bl)
    print(f'Task 4 Step 2: overall pct inserted before line {zones_idx+1}')
else:
    print('ERROR: zones not found')

with io.open(path, 'w', encoding='utf-8', newline='') as f:
    f.writelines(lines)
print(f'Final lines: {len(lines)}')
print('Done')
