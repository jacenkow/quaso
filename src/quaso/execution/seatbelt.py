"""Confinement on macOS, through the kernel's own sandbox.

sandbox-exec applies a policy the kernel enforces on the command and on
everything it goes on to spawn. That is the difference between it and
the permission prompt: the prompt guards what quaso runs, this guards
what that goes on to do.

Apple has marked the tool deprecated for years while continuing to ship
it and rely on it, and there is no supported replacement for confining
an arbitrary child process. The executor interface is the insurance: if
it is withdrawn, one backend is rewritten rather than the shell tool.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from quaso.execution.base import Completed, Executor
from quaso.execution.direct import spawn

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Paths worth denying even though reads are otherwise open. An allow-list
# would be stronger and would also break every build that reads from
# somewhere unexpected, which is most of them. These are what a prompt
# injection would actually go looking for.
SECRET_PATHS = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".config/gh",
    ".kube",
    ".docker/config.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "Library/Keychains",
)


DEVICES = (
    "/dev/null",
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
    "/dev/dtracehelper",
    "/dev/ptmx",
)


def _quote(path: Path) -> str:
    """Escape a path for the profile's string syntax."""
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def build_profile(
    workspace: Path, home: Path, allow_network: bool = True
) -> str:
    """A profile permitting ordinary work and nothing beyond it."""
    # gettempdir() rather than /tmp: on macOS the real temporary
    # directory is a per-user path under /var/folders, and mktemp uses
    # it, so a profile allowing only /tmp breaks anything using a
    # temporary file.
    writable = [
        workspace.resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path("/private/tmp"),
        Path("/tmp"),
    ]
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec process-fork)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow signal (target same-sandbox))",
        "(allow file-read*)",
    ]
    lines += [
        f'(allow file-write* (subpath "{_quote(path)}"))' for path in writable
    ]
    # Devices, without which ordinary tools break in ways that look like
    # bugs in them: pytest cannot open /dev/null and dies with an
    # internal error before running anything. Denying these confines
    # nothing, since none of them is a place data can be kept. Found by
    # running the suite under the sandbox, not by reasoning about it.
    lines += [f'(allow file-write* (literal "{d}"))' for d in DEVICES]
    lines.append('(allow file-write* (regex #"^/dev/tty[a-z0-9]*$"))')
    lines.append('(allow file-write* (regex #"^/dev/fd/[0-9]+$"))')
    lines.append("(allow file-ioctl)")
    # Denials come last: in this profile language the final matching
    # rule wins, so a deny after the blanket read is what takes effect.
    lines += [
        f'(deny file-read* (subpath "{_quote(home / name)}"))'
        for name in SECRET_PATHS
    ]
    lines.append("(allow network*)" if allow_network else "(deny network*)")
    return "\n".join(lines) + "\n"


class SeatbeltExecutor(Executor):
    name = "seatbelt"

    def __init__(self, allow_network: bool = True) -> None:
        self.allow_network = allow_network

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and Path(SANDBOX_EXEC).exists()

    def describe(self) -> str:
        network = "network allowed" if self.allow_network else "no network"
        return f"shell: seatbelt, {network}"

    async def run(
        self, command: str, cwd: Path, timeout: int, max_bytes: int
    ) -> Completed:
        profile = build_profile(
            cwd, Path.home(), allow_network=self.allow_network
        )
        shell = shutil.which("sh") or "/bin/sh"
        # The profile goes in as an argument rather than a file: no
        # temporary file to clean up, and nothing on disk for the
        # command itself to rewrite before it is read.
        argv = [SANDBOX_EXEC, "-p", profile, shell, "-c", command]
        return await spawn(argv, cwd, timeout, max_bytes)
