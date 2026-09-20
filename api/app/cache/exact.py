"""Tier 0: exact key = norm_query + siis_hash."""

import hashlib

from app.cache import store
from app.config import settings

_KEY_LENGTH = 16  # matches the keys in data/fixtures/*/cache_events.json


def make_key(norm_query: str, siis_hash: str | None) -> str:
    """Cache key for one question against one article.

    The prompt version is part of the key so a prompt change never serves plans built by the
    old one; the article hash is in there so the same question with a different article misses.
    """
    material = f"{settings.prompt_version}|{norm_query}|{siis_hash or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_KEY_LENGTH]


def get(key: str) -> dict | None:
    """The plan stored under this exact key, or None."""
    entry = store.entries().get(key)
    if entry is None:
        return None
    store.record_hit(key)
    return entry.plan
