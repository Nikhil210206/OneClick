"""SQLite persistence + single-flight lock per key."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from app.config import settings
from app.models import CacheEntry, Slots

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    key         TEXT PRIMARY KEY,
    siis_hash   TEXT,
    component   TEXT,
    symptom     TEXT,
    plan        TEXT NOT NULL,
    query_texts TEXT NOT NULL,
    created_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
)
"""

# key -> entry. The source of truth at runtime; SQLite is the copy that survives a restart.
_entries: dict[str, CacheEntry] = {}
_lock = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.sqlite_path)
    connection.execute(_SCHEMA)
    return connection


def entries() -> dict[str, CacheEntry]:
    """Everything currently cached, keyed by cache key."""
    return _entries


def load() -> int:
    """Read the SQLite snapshot into memory at startup. Returns how many entries came back."""
    global _entries
    with _connect() as connection:
        rows = connection.execute(
            "SELECT key, siis_hash, component, symptom, plan, query_texts, created_at, hits"
            " FROM cache_entries"
        ).fetchall()
    _entries = {
        row[0]: CacheEntry(
            key=row[0],
            siis_hash=row[1],
            slots=Slots(component=row[2], symptom=row[3]),
            plan=json.loads(row[4]),
            query_texts=json.loads(row[5]),
            created_at=row[6],
            hits=row[7],
        )
        for row in rows
    }
    return len(_entries)


def put(entry: CacheEntry) -> None:
    """Save a solved plan in memory and on disk."""
    entry.created_at = entry.created_at or time.time()
    _entries[entry.key] = entry
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO cache_entries"
            " (key, siis_hash, component, symptom, plan, query_texts, created_at, hits)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.key,
                entry.siis_hash,
                entry.slots.component,
                entry.slots.symptom,
                json.dumps(entry.plan),
                json.dumps(entry.query_texts),
                entry.created_at,
                entry.hits,
            ),
        )


def record_hit(key: str) -> None:
    """Count a hit; used by /v1/metrics, not by the lookup itself."""
    entry = _entries.get(key)
    if entry:
        entry.hits += 1


def clear() -> None:
    """Empty the cache, in memory and on disk. The scorer's first call must be genuinely cold."""
    _entries.clear()
    with _connect() as connection:
        connection.execute("DELETE FROM cache_entries")


@contextmanager
def single_flight(key: str):
    """Serialise identical concurrent misses so the pipeline runs once, not once per caller."""
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:
        yield
