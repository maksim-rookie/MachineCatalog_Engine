# Тестовый SQL-вариант для PyCharm

Это локальный прототип на SQLite, не реализация всей PostgreSQL-архитектуры.
Данные синтетические. Демо не разбирает PDF: оно проверяет загрузку
структурированных измерений в SQL и подбор по двум обязательным минимумам.
Подтверждение в демо задано только для проверки алгоритма.

1. Откройте корень проекта через File → Open в PyCharm.
2. В Settings → Project → Python Interpreter выберите существующий интерпретатор.
   Существующий `.venv` не пересоздавайте. Python 3.11+; Python 3.15 отдельно не проверен.
3. Откройте `run_pycharm_demo.py` и выберите Run.
   Внешние зависимости и отдельный SQL-сервер для демо не нужны.
4. База появится в `output/demo/catalog.sqlite3`. Повторный запуск не дублирует
   тот же документ. Разные версии документов сохраняются отдельно.

Для своей конфигурации Run → Edit Configurations:
- Script path: путь к `run_pycharm_demo.py`;
- Working directory: корень проекта;
- Parameters: `--min-travel-x 500 --min-spindle-rpm 10000`.

Из терминала:

```bash
python run_pycharm_demo.py
python run_pycharm_demo.py --input examples/demo_catalog.json --db output/demo/catalog.sqlite3
python -m unittest discover -s tests -p test_sqlite_demo.py -v
```

Ожидается MC200 → pass, MC300 → fail, MC400 → insufficient_data.
Ноль — число; отсутствующие, конфликтные, неподтверждённые значения и несовпадающие
единицы не считаются соответствием. Произвольные конвертации единиц и диапазоны
в этом прототипе не реализованы. Полный PDF-парсер по-прежнему запускается через
`main.py` и требует `requirements.txt`; автоматическая связь его CATALOG.json
с этой демонстрационной схемой пока отсутствует.
