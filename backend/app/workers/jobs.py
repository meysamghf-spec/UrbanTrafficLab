"""In-process background jobs.

Redis is not required for the development configuration. A thread runs a
simulation after the API response is committed. Tests can set UTL_SYNC_JOBS=1
and run the job on the request thread instead.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from app.core.config import get_settings


class RunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()

    def reset(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.discard(run_id)

    def cancel(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.add(run_id)

    def cancelled(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancelled

    def clear(self) -> None:
        with self._lock:
            self._cancelled.clear()


REGISTRY = RunRegistry()


def schedule(function: Callable[..., None], *args: object) -> None:
    if get_settings().sync_jobs:
        function(*args)
        return
    thread = threading.Thread(target=function, args=args, daemon=True, name="utl-simulation")
    thread.start()
