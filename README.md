# MachineCatalog Engine 4.8.5.4

[**Скачать исходники 4.8.5.4 (ZIP)**](https://github.com/maksim-rookie/PDF_SEARCH_v8_4_4_LOCAL/raw/refs/heads/downloads/machinecatalog-4.8.5.4/releases/MachineCatalog_Engine_4.8.5.4_source.zip)

Обновлено из пользовательского архива `MachineCatalog_Engine_v4_8_5_4_UPDATED.zip`. Номер версии сохранён из исходников.

В GitHub-поставку входят код, JSON-настройки, тесты и документация. Каталог `output` и CSV аудита конкретного пользовательского запуска исключены. Полный присланный архив сохранён в рабочей среде. Отчёты в `analysis` относятся к исходному пользовательскому архиву.

- [Установка](INSTALL_PYCHARM_PY311.md)
- [Описание и запуск](README_RU.md)
- [Изменения версии](CHANGELOG_v4_8_5_4.md)

## Данные вне проекта

Пример запуска с внешними папками:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --input ../MachineCatalog_Engine_data/input --output ../MachineCatalog_Engine_data/runs/v4_8_5_4 --cache ../MachineCatalog_Engine_data/cache/v4_8_5_4 --no-cache
```

В Windows используйте `.venv\Scripts\python.exe`. Входные PDF добавьте самостоятельно во внешнюю папку `input`. При запуске без аргументов используются пути, заданные в присланном `main.py`.

## Проверка публикации

`python -m unittest discover -s tests -v`: 62 теста обнаружено, 48 прошли, 14 проверок реальных PDF явно пропущены из-за отсутствия дополнительных контрольных файлов в исходной поставке. При публикации логика приложения не изменялась.

Все результаты извлечения требуют проверки; успешное завершение обработки не подтверждает корректность каждой характеристики.
