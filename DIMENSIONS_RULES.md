# Machine dimensions / Feedrate

Актуальные правила объединения, единиц, исключения столбцов и обозначений приведены в [PRESENTATION_POLICY.md](PRESENTATION_POLICY.md).

Сохранённые запуски можно обработать командой:

```bash
python finalize_dimensions.py --run /path/to/original/run --output /external/new/run
```

Требуется новая внешняя целевая папка. Команда читает старые записи и сохраняет исходный запуск. Для согласованного обновления канонических единиц всех нормализованных файлов рекомендуется `python reexport.py --run /path/to/original/run --output /external/run_history`.
