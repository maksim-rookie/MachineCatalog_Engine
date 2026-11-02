# Установка MachineCatalog Engine v4.8.5.4 в PyCharm — Windows, Python 3.11

## Шаг 1. Откройте новый проект

Распакуйте исходный ZIP, например в `D:\MachineCatalog_Engine_v4_8_5_4`. В PyCharm: **File → Open**, выберите папку с `main.py`. Откройте терминал **Alt+F12**. Не копируйте `.venv` из прошлой версии.

## Шаг 2. Создайте окружение через терминал PyCharm

```powershell
py -3.11 --version
py -3.11 -m venv .venv
```

При желании активируйте окружение в PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

В `cmd.exe`: `.venv\Scripts\activate.bat`. **Активация не обязательна:** все дальнейшие команды используют путь к Python внутри `.venv`, поэтому ограничение политики PowerShell не мешает установке.

## Шаг 3. Подключите интерпретатор в PyCharm

**File → Settings → Python → Interpreter → Add Interpreter → Add Local Interpreter → Existing** (названия могут отличаться в зависимости от редакции). Выберите `<папка проекта>\.venv\Scripts\python.exe`.

Проверка:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

Должно быть `Python 3.11.x` и путь внутри текущего проекта.

## Шаг 4. Установите зависимости

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Не запускайте `pip freeze > requirements.txt` без особой необходимости: это перезапишет проверенный список зависимостей. Старый адаптер DOCX/XLSX/OCR требует отдельного `requirements_legacy.txt`.

## Шаг 5. Выполните тесты

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Интеграционные тесты DHF/DBC/KAIBO автоматически активируются, если соответствующие PDF находятся в папке `pdf/`, либо заданы переменные `MCE_TEST_DHF`, `MCE_TEST_DBC`, `MCE_TEST_KAIBO` с путями файлов. Часть тестов без контрольных PDF будет пропущена.

## Шаг 6. Выполните новый запуск

Положите PDF-каталоги в `pdf/`:

```powershell
.\.venv\Scripts\python.exe main.py --no-cache
```

Для конкретной папки:

```powershell
.\.venv\Scripts\python.exe main.py --input "D:\Catalogs\PDF" --no-cache
```

Результат: `output\v4_8_5_4\run_YYYYMMDD_HHMMSS_microseconds\`; указатель: `output\v4_8_5_4\LATEST_RUN.txt`.

## Шаг 7. Запускайте через кнопку Run

**Run → Edit Configurations → + → Python**. `Script`: `main.py`; `Working directory`: папка проекта; `Parameters`: `--no-cache`; `Interpreter`: `.venv\Scripts\python.exe`.

## Шаг 8. Проверьте итоги

Откройте `ALL_MODELS_wide.csv`: одна строка на базовую модель; параметр с различными значениями по комплектациям сопровождается пометкой `исполнения`. Сверяйте его с `ALL_CONFIGURATIONS_wide.csv`, `MODEL_FAMILIES.csv` и `ALL_MODELS_long.csv`. Для исходных обозначений и координат смотрите `source_label`, `value_raw`, `source_page`, `cell_bbox`.

## Шаг 9. Ручная корректировка (при необходимости)

1. Скопируйте `config/review_overrides.example.json` в `config/review_overrides.json`.
2. Укажите точный `source_file`, SHA-256 документа, `fact_id`, `corrected_value`, `reviewer`, `reason`.
3. Используйте `--review-file config/review_overrides.json --no-cache`.
4. Проверьте `REVIEW_CORRECTIONS_AUDIT.csv` и `ALL_MODELS_long.csv`: `value_raw` должен остаться исходным, `value_extracted` — исходным значением до исправления.

Отсутствующее объяснение или неверный SHA-256 блокируют поправку. Система не подтверждает автоматически подлинность поправок: ответственность за рецензирование и проверку документа остаётся за человеком.
