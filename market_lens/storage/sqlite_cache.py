from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path


class SQLiteCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.enabled = True
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._init_db()
        except sqlite3.Error:
            self.enabled = False

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_cache (
                    cache_key TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )

    @staticmethod
    def key_for(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def get(self, url: str, ttl_seconds: int) -> str | None:
        if not self.enabled:
            return None
        cache_key = self.key_for(url)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT body, created_at FROM http_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        body, created_at = row
        if int(time.time()) - int(created_at) > ttl_seconds:
            return None
        return str(body)

    def set(self, url: str, body: str) -> None:
        if not self.enabled:
            return
        cache_key = self.key_for(url)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO http_cache (cache_key, url, body, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        body = excluded.body,
                        created_at = excluded.created_at
                    """,
                    (cache_key, url, body, int(time.time())),
                )
        except sqlite3.Error:
            return
