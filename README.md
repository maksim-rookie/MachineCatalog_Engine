# MachineCatalog Engine 8.4.5.1

[**Скачать MachineCatalog Engine 8.4.5.1 (ZIP)**](https://github.com/maksim-rookie/PDF_SEARCH_v8_4_4_LOCAL/raw/refs/heads/downloads/machinecatalog-8.4.5.1/releases/MachineCatalog_Engine_8.4.5.1.zip)

SHA-256: `81baea10237389a9eb4d1e6969c07f00f241b533bd273627da0ee53dba7ef94f`

Локальное извлечение характеристик станков из PDF и проверяемая проекция в CSV.

- [Установка, запуск и новая структура папок](README_RU.md)
- [Разбор пропусков в Kaibo и исправления](docs/ANALYSIS_KAIBO_8_4_5_1.md)
- [Изменения версии](docs/CHANGELOG_8_4_5_1.md)
- [Проверка версии](docs/VALIDATION_REPORT_8_4_5_1.md)

В проекте находятся только код, настройки, тесты и документация. Входные PDF, результаты и кэш хранятся во внешней папке `MachineCatalog_Engine_data` рядом с проектом. В архив версии эти данные не включаются.

Быстрый старт:

```bash
python -m venv .venv
# Linux: .venv/bin/python; Windows: .venv\Scripts\python.exe
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --init-data
# Поместите свои PDF в указанную программой внешнюю папку input.
.venv/bin/python main.py --no-cache
```

Результаты требуют ручной проверки. Данные и типизированные компоненты сохраняют исходные ячейки, страницы и связь с родительским фактом.
