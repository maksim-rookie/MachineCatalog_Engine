# MachineCatalog Engine 4.8.7

Версия 4.8.7 на основе 4.8.6: устойчивый импорт, сортировка по категориям и
числам, единый `CATALOG.json`, CSV только по запросу. Подробности: [README_RU.md](README_RU.md).

## Quick Start — Linux / macOS

Требуется Python 3.11 или новее. Выполняйте команды из корня проекта:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p pdf
# Скопируйте каталоги в pdf/, затем:
python main.py --input ./pdf --output ./output --cache ./cache
```

## Quick Start — Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
New-Item -ItemType Directory -Force pdf
# Скопируйте каталоги в pdf/, затем:
python main.py --input .\pdf --output .\output --cache .\cache
```

Если активация PowerShell запрещена политикой, запускайте интерпретатор напрямую:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py --input .\pdf --output .\output --cache .\cache
```

Альтернатива venv: `conda create -n machinecatalog python=3.11`, затем
`conda activate machinecatalog` и те же команды установки и запуска.

## Запуск и результат

```bash
# JSON — стандартный результат; один итоговый CSV включается явно:
python main.py --input ./pdf --output ./output --cache ./cache --export-csv
# Числовая сортировка моделей по параметру; отсутствующие значения идут в конец:
python main.py --input ./pdf --output ./output --cache ./cache --sort-by spindle_speed_rpm
# Диагностика без чтения/записи кэша:
python main.py --input ./pdf --output ./output --no-cache --log-level DEBUG
# Проверки:
python -m unittest discover -s tests -v
```

- `--input`: каталог исходных PDF, JSON, CSV или Excel. TXT/MD/DOCX используют старый адаптер совместимости.
- `--output`: родительский каталог результатов. Каждый запуск создаёт новый `run_*`.
- `--cache`: каталог JSON-кэша; изменившийся код или схема автоматически меняют его ключи.
- `--export-csv`: добавить **один** `CATALOG.csv`, с теми же строками и порядком колонок, что в JSON.
- `--sort-by`: канонический ключ параметра, например `machine_weight_kg`.
- `--strict-quality`: код возврата 2 при необходимости проверки; результаты сохраняются.
- `--overrides`: дополнительный JSON со словарными правилами.

`LATEST_RUN.txt` содержит имя последнего запуска. Внутри него основной файл —
`CATALOG.json`: строки таблицы, определения колонок, статусы, числовые измерения,
факты, исходные страницы/ячейки, неизвестные поля и отчёт качества.
Технические `RUN_*` и `SCHEMA_SNAPSHOT.json` сохраняют диагностику; промежуточные
CSV и копии по сериям больше не создаются. Старые результаты автоматически не удаляются.

`- — не указано в каталоге; !- — требуется подтверждение`.
`human_verified=false` означает отсутствие ручной проверки, а не ошибку словаря.
Ошибки источника, неизвестное отсутствие и ожидание ручной проверки подсчитываются отдельно.

Для JSON/CSV/Excel обязательна колонка `model`. Длинный формат также требует
`parameter` и `value` (значение может быть `null`). Широкий формат использует
канонические имена параметров в заголовках. Регистр и пробелы нормализуются;
неизвестные поля сохраняются. Пример и ограничения — в [README_RU.md](README_RU.md).

Текстовые PDF обрабатываются по ячейкам и координатам. Для таблиц без заголовков
Description/Unit используются реальные позиции моделей и единиц. Произвольная
разметка, зашифрованные PDF и сканы не гарантируются: неподдержанные данные
сохраняются для проверки. Подключение OCR через старый адаптер — отдельный режим
`--legacy-fallback`, требующий Tesseract и языковых пакетов.

Коды возврата: `0` — обработка завершена; `1` — ошибка обработки хотя бы одного
файла; `2` — `--strict-quality` и непрошедшая проверка качества.
Успешный код возврата не подтверждает правильность всех данных.

## PyCharm: тестовый SQL-вариант

Запустите `run_pycharm_demo.py`. Используются SQLite и синтетические данные;
SQL-сервер и внешние зависимости для демо не нужны. [Инструкция](docs/PYCHARM_DEMO.md).

[Скачать исходники 4.8.7](releases/MachineCatalog_Engine_4.8.7_source.zip).

Ограничения: Python 3.15 не проверен; PostgreSQL DDL — проект схемы,
не применённая миграция. SQL-демо не соединено с PDF-импортом.
JSON-вывод по умолчанию отличается от legacy 4.8.6; полный адаптер
обратной совместимости ещё не реализован.

## Строгий machine_spec

Адаптер, справочник field_id, confidence и запуск: [SPEC_CONTRACT.md](docs/SPEC_CONTRACT.md).
Обновлённый архив 4.8.7 включает адаптер и 60 тестов.
