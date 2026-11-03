"""Run directly in PyCharm; no external packages or SQL server required."""
import argparse
import json
from pathlib import Path
from catalog_engine.sqlite_demo import connect, load, select_equipment

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=BASE/'examples/demo_catalog.json')
    parser.add_argument('--db', type=Path, default=BASE/'output/demo/catalog.sqlite3')
    parser.add_argument('--min-travel-x', type=float, default=500)
    parser.add_argument('--min-spindle-rpm', type=float, default=10000)
    args = parser.parse_args()
    connection = connect(args.db)
    try:
        document_id = load(connection, args.input)
        results = select_equipment(connection, [
            {'parameter': 'travel_x_mm', 'min': args.min_travel_x, 'unit': 'mm'},
            {'parameter': 'spindle_speed_rpm', 'min': args.min_spindle_rpm, 'unit': 'rpm'}])
        print('SQL prototype: SYNTHETIC DATA')
        print('Database:', args.db.resolve())
        print('Document:', document_id)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
