# Отчёт проверки MachineCatalog Engine v4.8.5.2

## Сводка испытаний

- Полный **холодный** прогон: **11 PDF**, **19.841 с**, попадания в кэш **0**.
- Обнаружены **94 обозначения физических моделей**, сгруппированы в **93 базовых моделей**; **95 строк комплектаций**.
- Сохранены **3187 записей характеристик**: 2945 значений и 242 явных неприменимостей.
- Сравнение со входным прогоном v4.8.5.1: **0 потерянных и 0 добавленных идентификаторов фактов** (`fact_id`) из 3187; модель, параметр, исходный текст, значение, страница и строка также полностью совпадают.
- **47 из 47 тестов пройдены**, включая интеграционные тесты DHF, DBC и KAIBO на реальных PDF, а также тест подстановки ручной поправки с сохранением первоначальных доказательств.
- Все **22 Python-файлов** проекта разобраны синтаксическим парсером с целевым языком Python 3.11. Реальный Windows/Python 3.11 запуск здесь не выполнялся.
- Итог `extraction_gate_passed=false`, `quality_passed=false`. Это **не** результат с полностью подтверждённой семантикой.

## DHF 8000: подтверждение иерархии (страница 19 каталога)

| Уровень | Значение | Ключевой факт |
| --- | --- | --- |
| Базовая модель | DHF 8000 | Одна запись в `ALL_MODELS_wide.csv` |
| Физическая модификация | DHF 8000, standard | Магазин **60**; паллета **800 × 800 мм** |
| Физическая модификация | DHF 8000ST, ST | Магазин **60**; паллета **Ø1000 мм** по сноске `**` |
| Отдельная комплектация | DHF 8000, `30000rpm / PSC` | Магазин **80**; шпиндель **30000 об/мин** |

`ALL_MODEL_VARIANTS_wide.csv` сохраняет две физические модификации. `ALL_CONFIGURATIONS_wide.csv` сохраняет три записи исполнения. В сводном файле разные значения оснащены текстом `[исполнения: ...]`, а в `ALL_MODELS_wide_values.csv` конфликтующие конфигурации не превращаются в одно число.

## Сопоставление параметров

Приоритет групп в сводных CSV:

**Travel → Table → Spindle → Feedrate → Automatic tool changer (ATC) → Machine dimensions → Control**, далее Capacity, Turret, Sub spindle, Tailstock, Motor, Power source, Accuracy, Tank capacity, Machine, Other.

Ключи располагаются в заданном в `config/schema.json` порядке; исходные названия остаются в `ALL_MODELS_long.csv`. Словарь `config/parameter_synonyms.json` хранит названия DN и KAIBO и не объединяет значения с несовместимыми единицами (например, `m/min` ≠ `mm/min` без явного правила преобразования).

## Классификация

| Исходный PDF | Обозначений | Базовых моделей | Фактов | Типы |
| --- | ---: | ---: | ---: | --- |
| `dhf-8000-series.pdf` | 2 | 1 | 87 | Horizontal 5-Axis Machining Center |
| `dn-solutions-boring-machines-dbc-series.pdf` | 8 | 8 | 262 | Horizontal Boring Machine |
| `dn-solutions-vertical-machining-center-mynx-ii-series.pdf` | 7 | 7 | 315 | Vertical Machining Center |
| `dnm-series-4th-gen-1.pdf` | 6 | 6 | 194 | Vertical Machining Center |
| `DVF_5000.pdf` | 1 | 1 | 50 | 5-Axis Machining Center |
| `dvf_6500-8000-8000t.pdf` | 6 | 6 | 192 | 5-Axis Machining Center |
| `lynx-2100-2600-series-new.pdf` | 15 | 15 | 664 | Turning Center |
| `NHP_4000_5000.pdf` | 3 | 3 | 96 | Horizontal Machining Center |
| `puma-dnt-series-new.pdf` | 6 | 6 | 204 | Turning Center |
| `SVM-series.pdf` | 2 | 2 | 64 | Vertical Machining Center |
| `凯博数控电子样本2025.pdf` | 38 | 38 | 1059 | 5-Axis Machining Center, Bridge 5-Axis Machining Center, CNC Engraving and Milling Machine, Cradle 5-Axis Machining Center, Gantry CNC Milling Machine, Graphite Machining Center, High-speed Precision Machining Center, Horizontal Machining Center, Medium and High-speed Machining Center, Profile Machining Center, Vertical Machining Center |

В частности, DHF классифицируется как `Horizontal 5-Axis Machining Center`, NHP — `Horizontal Machining Center`, DNM/MYNX/SVM — `Vertical Machining Center`, DVF — `5-Axis Machining Center`, LYNX/PUMA — `Turning Center`, DBC — `Horizontal Boring Machine`. Разные серии KAIBO остаются типизированными по собственным заголовкам таблиц.

## Оставшиеся вопросы качества

| Источник | Страница | Неполностью извлечённых заполненных ячеек |
| --- | ---: | ---: |
| `dnm-series-4th-gen-1.pdf` | 23 | 1 |
| `puma-dnt-series-new.pdf` | 22 | 3 |

- Итого: **4** физические заполненные ячейки без записей фактов (эта проблема существовала в исходном прогоне), **1** несопоставленная строка основных таблиц, **369** замечаний в журнале.
- Требуют проверки конфигурационные опции, конфликтующие единицы, а также применимость систем ЧПУ в ряде каталогов.
- Положительный тест модели данных не доказывает, что все каталоги любого производителя будут распознаны без ошибок.
- Исправленный экспорт и вручную введённые поправки **не** изменяют первоначальный текст `value_raw`, `source_quote`, `source_page` и `cell_bbox`.

## Воспроизводимость

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --no-cache
```

Все входные PDF в исходный архив не включены; для воспроизводимого полного прогона нужны те же 11 каталогов. Подробные файлы результатов отдельным ZIP.
