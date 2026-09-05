"""Confinement on Linux, through bubblewrap.

bubblewrap builds a mount namespace: the filesystem is bound in read-only
and the workspace is bound back over it writable, so a write anywhere
else fails because there is nothing writable there to fail against. It
also unshares the network, which is why the network switch works here
and would not with Landlock alone, Landlock being filesystem-only.

The cost is a dependency. Where bwrap is missing this reports itself
unavailable and quaso says so at startup rather than implying a
confinement it does not have.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from quaso.execution.base import Completed, Executor
from quaso.execution.direct import spawn

# Bound in read-only. Anything outside these simply is not there, which
# is a stronger statement than denying writes to it.
_READABLE = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt")

# Cached: the probe spawns a process, and this is asked at startup and
# again by every parameterised test.
_USABLE: bool | None = None


class BubblewrapExecutor(Executor):
    name = "bubblewrap"

    def __init__(self, allow_network: bool = True) -> None:
        self.allow_network = allow_network

    @staticmethod
    def available() -> bool:
        """Whether bwrap is installed *and* permitted to run.

        Installed is not the same as usable. Ubuntu 23.10 and later
        restrict unprivileged user namespaces through AppArmor, so bwrap
        is present, starts, and fails with "setting up uid map:
        Permission denied" on every command it is given. Reporting it
        available there would mean every shell command failing instead
        of an honest fallback, so this runs it once and asks.
        """
        global _USABLE
        if _USABLE is not None:
            return _USABLE
        if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
            _USABLE = False
            return _USABLE
        try:
            probe = subprocess.run(
                ["bwrap", "--ro-bind", "/", "/", "true"],
                capture_output=True,
                timeout=10,
            )
            _USABLE = probe.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _USABLE = False
        return _USABLE

    def describe(self) -> str:
        network = "network allowed" if self.allow_network else "no network"
        return f"shell: bubblewrap, {network}"

    def _argv(self, command: str, cwd: Path) -> list[str]:
        workspace = str(cwd.resolve())
        argv = [
            "bwrap",
            "--die-with-parent",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        for path in _READABLE:
            if Path(path).exists():
                argv += ["--ro-bind", path, path]
        # The one writable place, bound after the read-only mounts so it
        # wins where they overlap.
        # Temp is bound rather than replaced with a fresh tmpfs. A
        # tmpfs is tidier until the workspace itself lives under /tmp,
        # as a CI checkout does, at which point it hides the one thing
        # the command needs. Binding it also matches what the macOS
        # profile permits, so the two backends allow the same places.
        for path in {Path(tempfile.gettempdir()).resolve(), Path("/tmp")}:
            if path.exists():
                argv += ["--bind", str(path), str(path)]
        argv += ["--bind", workspace, workspace]
        # The home directory is never bound, so the credentials the
        # Seatbelt profile has to deny by name are simply not present
        # here. Different mechanism, same result.
        if not self.allow_network:
            argv.append("--unshare-net")
        argv += ["--chdir", workspace, "sh", "-c", command]
        return argv

    async def run(
        self, command: str, cwd: Path, timeout: int, max_bytes: int
    ) -> Completed:
        return await spawn(self._argv(command, cwd), cwd, timeout, max_bytes)
