"""Choosing the confinement this machine can actually provide."""

from __future__ import annotations

from quaso.config import SandboxConfig
from quaso.execution.base import Executor
from quaso.execution.direct import DirectExecutor
from quaso.execution.sandbox_linux import BubblewrapExecutor
from quaso.execution.seatbelt import SeatbeltExecutor

# Tried in order. The first that reports itself available is used, and
# falling off the end means running unconfined rather than refusing to
# start: a sandbox nobody can run is not a reason to have no agent.
_BACKENDS: list[type[Executor]] = [SeatbeltExecutor, BubblewrapExecutor]


def create_executor(config: SandboxConfig) -> Executor:
    if config.mode == "off":
        return DirectExecutor()
    for backend in _BACKENDS:
        if backend.available():
            return backend(allow_network=config.network)
    return DirectExecutor()
