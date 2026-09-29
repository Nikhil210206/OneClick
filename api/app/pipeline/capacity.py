"""Reliability (design doc: Reliability, errors and deployment): the cold-capacity guard.

The API is public and unauthenticated (FAQ Theme 2 Q22), and a cold run holds a request thread and LLM
quota for seconds. At most settings.cold_max_concurrent cold pipelines run at once. A request that finds
no slot within settings.cold_queue_wait_s is answered rules-only by the caller (pipeline/run.py) instead
of queueing: cache hits never wait behind cold load, and a burst cannot outspend the free tier.

The limit is global, not per client: the scorer sends everything from one address.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import settings

_slots = threading.BoundedSemaphore(settings.cold_max_concurrent)
_lock = threading.Lock()
_in_flight = 0
_turned_away = 0


@contextmanager
def cold_slot() -> Iterator[bool]:
    """True with a slot held for the block, False when none freed up within cold_queue_wait_s."""
    global _in_flight, _turned_away
    held = _slots.acquire(timeout=settings.cold_queue_wait_s)
    with _lock:
        if held:
            _in_flight += 1
        else:
            _turned_away += 1
    try:
        yield held
    finally:
        if held:
            with _lock:
                _in_flight -= 1
            _slots.release()


def state() -> dict:
    """Cold runs in flight, the limit, and how many requests were answered rules-only for want of a slot."""
    with _lock:
        return {
            "cold_in_flight": _in_flight,
            "cold_max_concurrent": settings.cold_max_concurrent,
            "cold_turned_away": _turned_away,
        }
