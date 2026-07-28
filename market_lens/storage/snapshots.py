from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ValidatedSnapshot:
    dataset: str
    identity: dict[str, Any]
    source: str
    validator_version: str
    payload: Any
    source_as_of: date
    retrieved_at: datetime
    age_seconds: int
    row_count: int
    payload_sha256: str


class ValidatedSnapshotStore:
    def __init__(
        self,
        db_path: Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = db_path
        self.clock = clock
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
                CREATE TABLE IF NOT EXISTS validated_snapshots (
                    snapshot_key TEXT PRIMARY KEY,
                    dataset TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    validator_version TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    source_as_of TEXT NOT NULL,
                    retrieved_at INTEGER NOT NULL,
                    row_count INTEGER NOT NULL
                )
                """
            )

    @staticmethod
    def key_for(dataset: str, identity: dict[str, Any]) -> str:
        encoded = f"{dataset}:{_canonical_json(identity)}".encode()
        return hashlib.sha256(encoded).hexdigest()

    def put(
        self,
        *,
        dataset: str,
        identity: dict[str, Any],
        source: str,
        validator_version: str,
        payload: Any,
        source_as_of: date,
        row_count: int,
    ) -> None:
        if not self.enabled or row_count <= 0:
            return
        identity_json = _canonical_json(identity)
        payload_json = _canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO validated_snapshots (
                        snapshot_key,
                        dataset,
                        identity_json,
                        source,
                        validator_version,
                        schema_version,
                        payload_json,
                        payload_sha256,
                        source_as_of,
                        retrieved_at,
                        row_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(snapshot_key) DO UPDATE SET
                        identity_json = excluded.identity_json,
                        source = excluded.source,
                        validator_version = excluded.validator_version,
                        schema_version = excluded.schema_version,
                        payload_json = excluded.payload_json,
                        payload_sha256 = excluded.payload_sha256,
                        source_as_of = excluded.source_as_of,
                        retrieved_at = excluded.retrieved_at,
                        row_count = excluded.row_count
                    """,
                    (
                        self.key_for(dataset, identity),
                        dataset,
                        identity_json,
                        source,
                        validator_version,
                        SNAPSHOT_SCHEMA_VERSION,
                        payload_json,
                        payload_sha256,
                        source_as_of.isoformat(),
                        int(self.clock()),
                        row_count,
                    ),
                )
        except sqlite3.Error:
            return

    def get(
        self,
        *,
        dataset: str,
        identity: dict[str, Any],
        allowed_sources: set[str],
        validator_version: str,
        max_age_seconds: int,
        validator: Callable[[Any, date, int], bool],
    ) -> ValidatedSnapshot | None:
        if not self.enabled or max_age_seconds < 0:
            return None
        snapshot_key = self.key_for(dataset, identity)
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        dataset,
                        identity_json,
                        source,
                        validator_version,
                        schema_version,
                        payload_json,
                        payload_sha256,
                        source_as_of,
                        retrieved_at,
                        row_count
                    FROM validated_snapshots
                    WHERE snapshot_key = ?
                    """,
                    (snapshot_key,),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None

        (
            stored_dataset,
            identity_json,
            source,
            stored_validator_version,
            schema_version,
            payload_json,
            payload_sha256,
            source_as_of_value,
            retrieved_at_value,
            row_count_value,
        ) = row
        try:
            retrieved_at_timestamp = int(retrieved_at_value)
            age_seconds = int(self.clock()) - retrieved_at_timestamp
        except (TypeError, ValueError, OverflowError):
            self.delete(dataset, identity)
            return None
        if age_seconds < 0 or age_seconds > max_age_seconds:
            return None
        try:
            stored_schema_version = int(schema_version)
        except (TypeError, ValueError, OverflowError):
            self.delete(dataset, identity)
            return None
        if (
            stored_dataset != dataset
            or identity_json != _canonical_json(identity)
            or source not in allowed_sources
            or stored_validator_version != validator_version
            or stored_schema_version != SNAPSHOT_SCHEMA_VERSION
        ):
            self.delete(dataset, identity)
            return None
        if hashlib.sha256(str(payload_json).encode("utf-8")).hexdigest() != payload_sha256:
            self.delete(dataset, identity)
            return None
        try:
            payload = json.loads(payload_json)
            source_as_of = date.fromisoformat(str(source_as_of_value))
            row_count = int(row_count_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            self.delete(dataset, identity)
            return None
        try:
            payload_is_valid = validator(payload, source_as_of, row_count)
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            payload_is_valid = False
        if row_count <= 0 or not payload_is_valid:
            self.delete(dataset, identity)
            return None
        try:
            retrieved_at = datetime.fromtimestamp(
                retrieved_at_timestamp,
                tz=UTC,
            )
        except (OSError, OverflowError, ValueError):
            self.delete(dataset, identity)
            return None
        return ValidatedSnapshot(
            dataset=dataset,
            identity=dict(identity),
            source=str(source),
            validator_version=validator_version,
            payload=payload,
            source_as_of=source_as_of,
            retrieved_at=retrieved_at,
            age_seconds=age_seconds,
            row_count=row_count,
            payload_sha256=str(payload_sha256),
        )

    def delete(self, dataset: str, identity: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM validated_snapshots WHERE snapshot_key = ?",
                    (self.key_for(dataset, identity),),
                )
        except sqlite3.Error:
            return


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
