from __future__ import annotations

import json
import logging

from app.storage.database import Database, now_ms

logger = logging.getLogger(__name__)


class ConfigRepository:
    """Key/value persistence for subtitle behaviour flags (e.g. always-on-top).

    Stored in the `subtitle_config` table (key, value, updated_at). Values are
    JSON encoded so booleans survive a round trip. Read/write failures fall back
    to the in-memory state and are logged without interrupting the feature.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_bool(self, key: str, default: bool = False) -> bool:
        try:
            row = self.database.query_one(
                "SELECT value FROM subtitle_config WHERE key=?", (key,)
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("读取 subtitle_config 失败，使用默认值：%s", exc)
            return default
        if row is None:
            return default
        try:
            return bool(json.loads(row["value"]))
        except (ValueError, TypeError):
            return default

    def set_bool(self, key: str, value: bool) -> None:
        try:
            self.database.execute(
                """
                INSERT INTO subtitle_config(key, value, updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                                updated_at=excluded.updated_at
                """,
                (key, json.dumps(bool(value)), now_ms()),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("写入 subtitle_config 失败，内存状态保持：%s", exc)

    def get_string(self, key: str, default: str = "") -> str:
        """Reads a raw string value (e.g. an anchor-point enum)."""
        try:
            row = self.database.query_one(
                "SELECT value FROM subtitle_config WHERE key=?", (key,)
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("读取 subtitle_config 失败，使用默认值：%s", exc)
            return default
        if row is None:
            return default
        return str(row["value"])

    def exists(self, key: str) -> bool:
        try:
            row = self.database.query_one(
                "SELECT 1 FROM subtitle_config WHERE key=?", (key,)
            )
        except Exception:  # pragma: no cover - defensive
            return False
        return row is not None

    def set_string(self, key: str, value: str) -> None:
        try:
            self.database.execute(
                """
                INSERT INTO subtitle_config(key, value, updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                                updated_at=excluded.updated_at
                """,
                (key, str(value), now_ms()),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("写入 subtitle_config 失败，内存状态保持：%s", exc)
