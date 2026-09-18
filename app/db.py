import sqlite3
from pathlib import Path
import logging
DB_PATH = Path(__file__).parent.parent / "bot.db"

# A short timeout (in seconds) tells SQLite to wait and retry briefly
# instead of raising "database is locked" immediately if another
# connection is momentarily writing. Also enabling WAL mode lets
# reads and writes happen more concurrently without blocking each
# other, which further reduces lock contention.

logger=logging.getLogger(__file__)
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    try:
        conn = _connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id TEXT UNIQUE,
                sender TEXT,
                local_path TEXT,
                status TEXT,
                drive_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error("failed to init database : {e}")


def insert_message(media_id: str, sender: str, local_path: str, status: str = "saved"):
    try:
        conn = _connect()
        conn.execute(
            "INSERT OR IGNORE INTO messages (media_id, sender, local_path, status) VALUES (?, ?, ?, ?)",
            (media_id, sender, local_path, status),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"failed to insert message in the local database : {e}")


def update_status(media_id: str, status: str, drive_file_id: str = None):
    try:
        conn = _connect()
        conn.execute(
            "UPDATE messages SET status = ?, drive_file_id = ? WHERE media_id = ?",
            (status, drive_file_id, media_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"failed to update status: {e}")