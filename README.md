# print-mcp

MCP server that gives any AI agent **real, colour-accurate printing through Adobe Photoshop**:
batch-print folders with your own printer ICC profile, your paper type and quality, and
driver auto-correction switched **off**. 16 tools: profiles, plan-before-print, spooler queue,
ICC search, DEVMODE inspection, sublimation mirror.

Windows · Photoshop 26.x · Python 3.10+ · MIT

Quick start:

```jsonc
// opencode.jsonc (same idea works in Claude Desktop, Cursor, Codex...)
"mcp": {
  "print": {
    "type": "local",
    "command": ["python", "-m", "print_mcp.server"],
    "environment": { "PYTHONPATH": "C:\\path\\to\\print-mcp" },
    "enabled": true
  }
}
```

```
pip install -r requirements.txt   # pywin32, Pillow
```

Then ask your agent: `print_plan {"folder": "D:\\orders\\client1"}` to preview,
`print_folder {...}` to print. Full setup (one-time Photoshop + printer dialog configuration)
and honest measurements of what Photoshop scripting can and cannot do are documented below
(the tool set was derived from real probes on Photoshop 26.11, scripts included in `tools/`).

Related project: [KopiTehnik Print](https://github.com/ildargiv/KopiTehnik-Print) — inkjet
photo printing without Photoshop.

---

# print-mcp (русский)

MCP-сервер для пакетной печати папок через Photoshop с фиксированным цветовым
пайплайном: свой ICC-профиль принтера, свой тип бумаги и качество, и **выключенная
автокоррекция цвета**.

## Что сервер умеет

- напечатать папку или файл: открыть в Photoshop → напечатать → закрыть, без сохранения
- зеркалить изображение автоматически (нужно для сублимации, перевод клеится вниз лицевой стороной)
- пауза между файлами, остановка на первой ошибке, отчёт по каждому файлу
- профили печати: сохранить, проверить, применить
- очередь спулера: список заданий, отмена
- поиск и проверка ICC-профилей (204 установленных, включая кастомные)
- чтение DEVMODE принтера и проверка, отключено ли управление цветом в драйвере

## Что сервер НЕ умеет и почему

Это не недоделка — это измерено на твоей машине, Photoshop 26.11. Модуль `print_mcp/jsx.py`
документирует точные результаты проб.

| Задача | Статус | Как делается |
|---|---|---|
| Напечатать папку файлов | ✅ автоматически | открыть в Photoshop → `print` → закрыть |
| Зеркало для сублима | ✅ автоматически | зеркалится временная копия файла |
| Паузы / стоп при ошибке / отчёт | ✅ автоматически | сервер |
| Выбрать принтер | ⚙️ один раз вручную | Диалог печати Photoshop |
| Тип бумаги, качество, dpi | ⚙️ один раз вручную | Диалог драйвера Epson |
| ICC-профиль, intent, компенсация | ⚙️ один раз вручную | Диалог печати Photoshop |
| Отключить коррекцию в драйвере | ⚙️ один раз вручную | Свойства принтера → Цвет → «Без коррекции цвета» |

Причины, почему программно нельзя:

1. **Photoshop 26.11 отвергает любой дескриптор команды `print`** — «параметры команды
   недействительны», даже пустой. Одинаково через COM-строку и через файл `.jsx`, значит
   не артефакт хоста. Работает только `executeAction("print", undefined, NO)` — печать
   с настройками из диалога.
2. **`Document.convertProfile` тоже отвергается** — конвертировать в ICC из скрипта нельзя.
3. **`FlipDirection` недостижим** ни через `FlipDirection`, `app.FlipDirection`,
   `Document.FlipDirection`, `$.globals.*` — поэтому `flipCanvas` не вызвать,
   зеркало делается на копии файла.
4. **Epson-драйвер принимает запись DEVMODE и игнорирует все публичные поля.**
   Проверено на `EPSON Stylus Photo 1410`: `dmPrintQuality` 576 → остался 720,
   `dmICMMethod` 0 → остался 3, `dmPaperLength` → без изменений. Тип бумаги, качество
   и цветовые настройки Epson хранит в приватном блоке `dmDriverExtra` (796 байт),
   публичного API нет.
5. **`DeviceCapabilities` на Epson роняет процесс** (access violation внутри DLL),
   поэтому запрос списков носителей изолирован в подпроцессе `print_mcp/capscan.py`.

Побочно: в этом хосте не определены `JSON`, `require`, `chr`, `app.photoshopVersion`,
`app.fullName` — отсюда собственный сериализатор в скриптах.

## Разовая настройка

**В Photoshop** (Файл → Печать):

- Принтер: нужный Epson
- Цветовая обработка: **Photoshop управляет цветами**
- Профиль принтера: свой ICC (например `1410 Sublim TKANI photo bumaga`)
- Компенсация: **Нет** ← это и есть «выключенная автокоррекция»
- Копии: 1
- Размер и положение: по размеру бумаги

**В драйвере Epson** (Параметры принтера → Дополнительно → Цвет):

- Цветовая коррекция: **Нет коррекции цвета**
- Тип носителя: нужная бумага
- Качество: нужное
- Разрешение: нужно (для фото 576×576 dpi)

Проверить, что драйвер не трогает цвет: `print_printer_info` → `icm_method`.
Должно быть `0` (ICM_OFF). Если `3` — драйвер подменяет цвет после Photoshop.

## Установка

Пример конфигурации (`.config/opencode/opencode.jsonc`, Claude Desktop и т.п.):

```jsonc
"mcp": {
  "print": {
    "type": "local",
    "command": ["python", "-m", "print_mcp.server"],
    "environment": { "PYTHONPATH": "C:\\path\\to\\print-mcp" },
    "enabled": true
  }
}
```

Зависимости: `pywin32` (COM к Photoshop, спулер), `Pillow` (зеркало файла).
Зафиксированы в `requirements.txt`, ставить вручную больше ничего не нужно.

## Использование из агента

Сохранить профиль один раз:

```
print_save_profile {
  "name": "sublim 1410",
  "printer": "EPSON Stylus Photo 1410 Series",
  "icc": "1410 Sublim TKANI photo bumaga",
  "mirror": true,
  "delay_seconds": 2,
  "stop_on_error": true
}
```

Проверить, что всё сходится:

```
print_validate_profile {"name": "sublim 1410"}
```

Посмотреть план без печати:

```
print_plan {"name": "sublim 1410", "folder": "Z:\\1ПЕЧАТЬ В ПОДВАЛЕ"}
```

Печатать:

```
print_folder {"name": "sublim 1410", "folder": "Z:\\1ПЕЧАТЬ В ПОДВАЛЕ"}
```

## Инструменты

| Инструмент | Назначение |
|---|---|
| `print_capabilities` | что умеет хост скриптов текущей версии Photoshop |
| `print_status` | версия, занятость, открытые документы |
| `print_open_documents` | открытые документы с размером и режимом |
| `print_list_printers` | установленные принтеры |
| `print_printer_info` | DEVMODE + что драйвер публикует о себе |
| `print_find_icc` | поиск по установленным ICC |
| `print_list_profiles` | сохранённые профили печати |
| `print_save_profile` | создать/перезаписать профиль |
| `print_delete_profile` | удалить профиль |
| `print_validate_profile` | проверка профиля до запуска |
| `print_apply_driver` | попытка записи DEVMODE + отчёт, что драйвер проигнорировал |
| `print_plan` | что напечатается из папки, без печати |
| `print_file` | напечатать один файл |
| `print_folder` | напечатать папку |
| `print_queue` | очередь спулера |
| `print_cancel` | отменить задание |

## Прямой запуск без агента

```powershell
cd C:\Users\Pod\Desktop\PRINT\print-mcp
$env:PYTHONPATH = "C:\Users\Pod\Desktop\PRINT\print-mcp"
python tools\mcp_call.py --list
python tools\mcp_call.py print_plan "@tools\args_plan.json"
```

`mcp_call.py` принимает аргументы как `@файл.json` — PowerShell сносит кавычки в
инлайн-JSON.

## Ограничения по форматам

- **Зеркало**: работает для JPG, PNG, TIFF. PSD/PSB — нет, Photoshop не умеет
  отражать, а многослойный файл не отразить как плоский. Такие файлы в папке
  будут пропущены с внятной ошибкой, а не напечатаны задом наперёд.
- **Расширения**: `.psd .psb .tif .tiff .png .jpg .jpeg`
- **Оригиналы не меняются** никогда: зеркалится временная копия, документ
  закрывается без сохранения.

## Диагностические скрипты

В `tools/` лежат скрипты, которыми эти выводы получены. Запускаются вручную,
в работе сервера не участвуют:

| Файл | Что проверяет |
|---|---|
| `diag_capabilities.py` | что есть в хосте: open/print/enum/flip |
| `diag_convert2.py` | `convertProfile` с согласованными парами профилей |
| `diag_flip_enum.py` | поиск `FlipDirection` всеми доступными путями |
| `diag_devmode_write.py` | принимает ли Epson запись DEVMODE |
| `diag_dpi.py` | откуда Photoshop берёт разрешение открытого файла |
| `check_mirror_preserves.py` | зеркальная копия сохраняет размер, dpi и ICC |
| `mcp_call.py` | вызов инструмента мимо агента, `--list` показывает всё |

## Тесты

```
python tests\test_print_mcp.py
```

14 тестов, Photoshop и принтер не нужны: раскладка DEVMODEW, геометрия зеркальной
копии (размер, переворот, dpi, ICC, оригинал не тронут), фильтр файлов, чтение
разрешения и сохранение профилей. `pytest` ставить не нужно, но и не мешает.

## Замечания о границах

- **dpi файла почти не влияет на размер отпечатка**, если в диалоге Photoshop
  выбрано «по размеру бумаги»: тогда размер задаёт бумага. Зеркальная копия всё
  равно переносит dpi и ICC, чтобы снимок совпадал с оригиналом при любом
  масштабировании, и `print_file` предупреждает, если Photoshop открыл копию с
  другим разрешением.
- В системе **нет ICC класса `link`**, только `output` (187 профилей). Подсказка
  про device link осталась бы верной только для реального link-профиля; здесь
  `resolved_icc` выбирает `output` и это правильно.
