"""System clock, UUIDv7 identifiers, and reproducibility metadata."""

import platform
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import UUID

from opsiu import __version__
from opsiu.application.ports import Clock, RunMetadata


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Uuid7Generator:
    """Monotonic RFC 9562 UUIDv7 identifiers, including within one millisecond."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self._last_ms = -1
        self._random = 0
        self._lock = Lock()

    def new_id(self) -> str:
        with self._lock:
            milliseconds = int(self.clock.now().timestamp() * 1000)
            if milliseconds > self._last_ms:
                self._random = secrets.randbits(74)
            else:
                milliseconds = self._last_ms
                self._random += 1
                if self._random >= 1 << 74:
                    milliseconds += 1
                    self._random = 0
            self._last_ms = milliseconds
            value = (
                milliseconds << 80
                | 7 << 76
                | (self._random >> 62) << 64
                | 2 << 62
                | (self._random & ((1 << 62) - 1))
            )
            return str(UUID(int=value))


def collect_metadata(settings_hash: str, cwd: Path | None = None) -> RunMetadata:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()

    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain"))
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "unknown", False
    return RunMetadata(
        settings_hash, commit, dirty, platform.python_version(), __version__
    )
