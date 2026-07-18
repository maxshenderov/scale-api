# EDT: Как создавать элементы на форме (с первого раза)

> Шпаргалка по правильному XML для управляемых форм в EDT. Что я выучил на ошибках с WMS-тестером.

---

## 1. Имена элементов — ТОЛЬКО латиница для `name`

Имена атрибутов формы (`Attribute name="..."`) и элементов управления (`InputField name="..."`) должны быть **ASCII**. Русские имена в `name` ломают кодировку.

```xml
<!-- ПРАВИЛЬНО -->
<Attribute name="URL" id="2">
<Attribute name="Warehouse" id="6">
<InputField name="Password" id="18">

<!-- НЕПРАВИЛЬНО — кракозябры при сохранении -->
<Attribute name="Пароль" id="4">
```

Для русских названий используй `Title`:
```xml
<InputField name="Password" id="18">
    <DataPath>Password</DataPath>
    <Title>
        <v8:item>
            <v8:lang>ru</v8:lang>
            <v8:content>Пароль</v8:content>
        </v8:item>
    </Title>
</InputField>
```

---

## 2. Команды: `<Commands>`, НЕ `<FormCommands>`

В XML управляемой формы команды находятся внутри `<Commands>`, не `<FormCommands>`.

```xml
<!-- ПРАВИЛЬНО -->
<Commands>
    <Command name="CheckConnection" id="1001">
        <Title>...</Title>
        <ToolTip>...</ToolTip>
        <Action>CheckConnection</Action>
        <Representation>TextPicture</Representation>
    </Command>
</Commands>

<!-- НЕПРАВИЛЬНО -->
<FormCommands>...</FormCommands>
```

Ссылка на команду из кнопки: `Form.Command.ИмяКоманды`
```xml
<Button name="Btn01" id="101">
    <Type>UsualButton</Type>
    <CommandName>Form.Command.CheckConnection</CommandName>
</Button>
```

---

## 3. Обязательные элементы команды

Каждая `<Command>` должна иметь:
- `Title` — название (с v8:item)
- `ToolTip` — подсказка (с v8:item)
- `Action` — имя процедуры-обработчика в модуле
- `Representation` — `TextPicture` (или `Picture`, `Text`)

Без `Title` или `ToolTip` команда не загрузится.

---

## 4. Типы атрибутов формы

### Строка
```xml
<Attribute name="URL" id="2">
    <Type>
        <v8:Type>xs:string</v8:Type>
        <StringQualifiers>
            <Len>0</Len>
        </StringQualifiers>
    </Type>
</Attribute>
```

### Ссылка на справочник
```xml
<Attribute name="Склад" id="7">
    <Title>
        <v8:item>
            <v8:lang>ru</v8:lang>
            <v8:content>Склад</v8:content>
        </v8:item>
    </Title>
    <Type>
        <v8:Type>cfg:CatalogRef.Склады</v8:Type>
    </Type>
</Attribute>
```

Доступные типы ссылок:
- `cfg:CatalogRef.Склады`
- `cfg:CatalogRef.Лико_Паллеты2_0`
- `cfg:CatalogRef.СкладскиеЯчейки`
- и т.д. — любой справочник конфигурации

### Выпадающий список (статический)
```xml
<InputField name="Формат" id="X">
    <DataPath>Формат</DataPath>
    <ListChoiceMode>true</ListChoiceMode>
    <ChoiceList>
        <xr:Item>
            <xr:Presentation/>
            <xr:CheckState>0</xr:CheckState>
            <xr:Value xsi:type="FormChoiceListDesTimeValue">
                <Presentation/>
                <Value xsi:type="xs:string">text</Value>
            </xr:Value>
        </xr:Item>
        <xr:Item>
            <xr:Presentation/>
            <xr:CheckState>0</xr:CheckState>
            <xr:Value xsi:type="FormChoiceListDesTimeValue">
                <Presentation/>
                <Value xsi:type="xs:string">html</Value>
            </xr:Value>
        </xr:Item>
    </ChoiceList>
</InputField>
```

**Важно:** `xs:ValueList` НЕ работает как тип атрибута формы. Для динамических списков выбора используй ссылочные атрибуты (`CatalogRef`) — 1С сама подставит список выбора.

---

## 5. События

```xml
<!-- Событие формы -->
<Events>
    <Event name="OnCreateAtServer">ПриСозданииНаСервере</Event>
</Events>

<!-- Событие поля -->
<InputField name="Склад" id="162">
    ...
    <Events>
        <Event name="OnChange">СкладПриИзменении</Event>
    </Events>
</InputField>
```

---

## 6. BSL: зарезервированные слова

В модуле формы нельзя использовать имя переменной `Параметры` — это зарезервированное свойство формы (`ПараметрыФормы`).

```bsl
// НЕПРАВИЛЬНО
Параметры = Новый Структура;

// ПРАВИЛЬНО
Парам = Новый Структура;
```

---

## 7. BSL: GUID из ссылки 1С

Если на форме есть реквизит-ссылка (например `Склад` типа `CatalogRef.Склады`), GUID получается так:

```bsl
&НаСервере
Функция ГуидСсылки(Ссылка)
    Если ЗначениеЗаполнено(Ссылка) Тогда
        Возврат Строка(Ссылка.УникальныйИдентификатор());
    КонецЕсли;
    Возврат "";
КонецФункции
```

---

## 8. Кодировка файлов — UTF-8 без BOM

При записи EDT файлов через PowerShell:

```powershell
$utf8 = New-Object System.Text.UTF8Encoding($false)  # $false = без BOM
[System.IO.File]::WriteAllText($path, $content, $utf8)
```

С BOM (`$true`) файл может прочитаться с двойным BOM при конкатенации here-strings.

---

## 9. Структура директорий обработки

```
МояОбработка/
  МояОбработка.xml                           # метаданные (ExternalDataProcessor)
  МояОбработка/
    Forms/
      Форма.xml                               # метаданные формы
      Форма/
        Ext/
          Form.xml                             # контролы, атрибуты, команды
          Form/
            Module.bsl                         # модуль формы
```

В `МояОбработка.xml` обязательно:
```xml
<DefaultForm>ExternalDataProcessor.МояОбработка.Form.Форма</DefaultForm>
<ChildObjects>
    <Form>Форма</Form>
</ChildObjects>
```

---

## 10. Полный пример Form.xml (скелет)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" 
      xmlns:v8="http://v8.1c.ru/8.1/data/core" 
      xmlns:xs="http://www.w3.org/2001/XMLSchema" 
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" 
      version="2.20">
    <Title>
        <v8:item>
            <v8:lang>ru</v8:lang>
            <v8:content>Моя обработка</v8:content>
        </v8:item>
    </Title>
    <AutoCommandBar name="" id="-1"/>
    <Events>
        <Event name="OnCreateAtServer">ПриСозданииНаСервере</Event>
    </Events>
    <ChildItems>
        <!-- элементы формы -->
    </ChildItems>
    <Attributes>
        <Attribute name="Объект" id="1">
            <Type>
                <v8:Type>cfg:ExternalDataProcessorObject.МояОбработка</v8:Type>
            </Type>
            <MainAttribute>true</MainAttribute>
        </Attribute>
        <!-- остальные атрибуты -->
    </Attributes>
    <Commands>
        <!-- команды -->
    </Commands>
</Form>
```

---

## Связи

- [[ЗагрузкаСклада]] — пример сложной формы с командами
- [[Liko_Rest]] — HTTP-сервис, куда уходят запросы
- [[Лико_HTTP_Сервер]] — модуль HTTP-хелперов
