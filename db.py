import sqlite3
import logging
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot_data.db"

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_name(user_id: int) -> str:
    return f"user_{user_id}"


def ensure_user_table(user_id: int) -> None:
    """Создаёт таблицу тезисов для пользователя, если её ещё нет."""
    table = _table_name(user_id)
    with _connect() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{table}" (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                thesis    TEXT    NOT NULL,
                created_at DATETIME DEFAULT (datetime('now', 'localtime'))
            )
        """)
    logger.debug(f"Таблица {table!r} готова")


def save_theses(user_id: int, theses: list[str]) -> None:
    """Сохраняет список тезисов в таблицу пользователя."""
    ensure_user_table(user_id)
    table = _table_name(user_id)
    with _connect() as conn:
        conn.executemany(
            f'INSERT INTO "{table}" (thesis) VALUES (?)',
            [(t,) for t in theses],
        )
    logger.info(f"Сохранено {len(theses)} тезис(ов) для user_{user_id}")


def load_theses(user_id: int) -> list[str]:
    """Возвращает все тезисы пользователя из БД (от старых к новым)."""
    ensure_user_table(user_id)
    table = _table_name(user_id)
    with _connect() as conn:
        rows = conn.execute(
            f'SELECT thesis FROM "{table}" ORDER BY id ASC'
        ).fetchall()
    theses = [row["thesis"] for row in rows]
    logger.debug(f"Загружено {len(theses)} тезис(ов) для user_{user_id}")
    return theses


def clear_theses(user_id: int) -> int:
    """Удаляет все тезисы пользователя из БД. Возвращает количество удалённых записей."""
    ensure_user_table(user_id)
    table = _table_name(user_id)
    with _connect() as conn:
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        conn.execute(f'DELETE FROM "{table}"')
    logger.info(f"Очищено {count} тезис(ов) для user_{user_id}")
    return count
