"""The executor interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Completed:
    stdout: bytes
    stderr: bytes
    exit_code: int
    # Bytes produced past the ceiling and never read.
    dropped: int = 0


class Executor(ABC):
    """Runs a shell command, under whatever confinement it provides."""

    name: str

    @abstractmethod
    async def run(
        self, command: str, cwd: Path, timeout: int, max_bytes: int
    ) -> Completed:
        """Run to completion. Raises TimeoutError past `timeout`."""

    def describe(self) -> str:
        """One line for the banner, so what is in force is never a guess."""
        return self.name

    @staticmethod
    def available() -> bool:
        """Whether this executor can run on this machine."""
        return True
