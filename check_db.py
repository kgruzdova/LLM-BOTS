import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot_data.db"

conn = sqlite3.connect(DB_PATH)

tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()

if not tables:
    print("База данных пуста — таблиц нет")
else:
    print(f"Найдено таблиц: {len(tables)}\n")
    for (table,) in tables:
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        print(f"{'='*50}")
        print(f"Таблица: {table}  ({len(rows)} записей)")
        print(f"{'='*50}")
        for row in rows:
            print(f"  [{row[0]}] {row[2]}  |  {row[1]}")
        print()

conn.close()
