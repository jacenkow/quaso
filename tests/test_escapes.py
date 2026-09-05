"""What a command can reach, with and without confinement.

These are the same three things in both directions. Unconfined they all
succeed, which is not a bug but a statement of what quaso permits today
and what SECURITY.md warns about. Under a sandbox they must all fail.

The pair is the point. A sandbox test that only runs sandboxed proves
nothing: it passes just as well when the escape was never possible, or
when the profile silently failed to apply and the command simply errored
for some other reason.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from quaso.execution import DirectExecutor
from quaso.execution.base import Completed

TIMEOUT = 20
CAP = 100_000


async def _run(executor, command: str, cwd: Path) -> Completed:
    return await executor.run(command, cwd, TIMEOUT, CAP)


def _outside(tmp_path: Path) -> Path:
    """A file the workspace has no business touching."""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    secret = outside / "id_rsa"
    secret.write_text("PRIVATE-KEY-CONTENTS")
    return secret


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    return workspace


@pytest.fixture
def secret_probe():
    """A credential the sandbox must not reach, in the real home.

    A planted fake home does not work: bubblewrap binds the temporary
    directory, so a home invented under it is legitimately visible,
    while the real one is never bound at all. Seatbelt denies the same
    path by name. Different mechanisms, one place to test them.
    """
    ssh = Path.home() / ".ssh"
    created = not ssh.exists()
    ssh.mkdir(mode=0o700, exist_ok=True)
    probe = ssh / f"quaso-probe-{uuid.uuid4().hex[:8]}"
    probe.write_text("PRIVATE-KEY-CONTENTS")
    yield probe
    probe.unlink(missing_ok=True)
    if created:
        ssh.rmdir()


@pytest.fixture
def escape_target():
    """Somewhere outside the workspace and outside the temporary
    directory, which the sandbox has to allow because real work needs
    it. The home directory is neither, and is what an injected command
    would be reaching for anyway.
    """
    target = Path.home() / f".quaso-escape-probe-{uuid.uuid4().hex[:8]}"
    yield target
    target.unlink(missing_ok=True)


class TestUnconfined:
    """The control. Every one of these passing is the exposure that the
    sandbox exists to close, so if one starts failing the sandboxed
    results below have stopped meaning anything."""

    @pytest.mark.asyncio
    async def test_it_can_write_outside_the_workspace(
        self, tmp_path, escape_target
    ):
        workspace = _workspace(tmp_path)
        await _run(DirectExecutor(), f"echo x > {escape_target}", workspace)
        assert escape_target.exists()

    @pytest.mark.asyncio
    async def test_it_can_read_a_secret(self, tmp_path):
        workspace = _workspace(tmp_path)
        secret = _outside(tmp_path)
        done = await _run(DirectExecutor(), f"cat {secret}", workspace)
        assert b"PRIVATE-KEY-CONTENTS" in done.stdout

    @pytest.mark.asyncio
    async def test_it_can_open_a_socket(self, tmp_path):
        """Network reachability without depending on the internet: a
        socket that binds and connects to itself is enough to show the
        capability is there."""
        workspace = _workspace(tmp_path)
        probe = (
            "python3 -c \"import socket; s=socket.socket(); "
            "s.bind(('127.0.0.1', 0)); s.listen(1); print('bound')\""
        )
        done = await _run(DirectExecutor(), probe, workspace)
        assert b"bound" in done.stdout


# Printed only when the network is genuinely usable, in whichever terms
# the backend enforces. Neither probe touches the internet: the suite is
# offline and stays that way.
_NETWORK_PROBES = {
    # A socket that opens at all is one Seatbelt did not refuse.
    "seatbelt": (
        'python3 -c "import socket; s=socket.socket(); '
        "s.bind(('127.0.0.1', 0)); print('reachable')\""
    ),
    # Inside an unshared namespace only loopback remains, so anything
    # beyond one interface means a route off the machine survived.
    "bubblewrap": (
        'python3 -c "import socket; '
        "print('reachable' if len(socket.if_nameindex()) > 1 else 'alone')\""
    ),
}


def _sandboxes():
    """Every sandbox this machine can actually run, for parameterising.

    Empty on a platform with none, which skips rather than pretends.
    """
    from quaso.execution.sandbox_linux import BubblewrapExecutor
    from quaso.execution.seatbelt import SeatbeltExecutor

    found = []
    for backend, label in (
        (SeatbeltExecutor, "seatbelt"),
        (BubblewrapExecutor, "bubblewrap"),
    ):
        if backend.available():
            found.append(pytest.param(backend, id=label))
    return found


sandboxed = pytest.mark.parametrize("make_executor", _sandboxes())
needs_sandbox = pytest.mark.skipif(
    not _sandboxes(), reason="no sandbox available on this platform"
)


@needs_sandbox
class TestConfined:
    """The same three escapes, which must now all fail."""

    @sandboxed
    @pytest.mark.asyncio
    async def test_writing_outside_the_workspace_is_refused(
        self, tmp_path, make_executor, escape_target
    ):
        workspace = _workspace(tmp_path)
        await _run(make_executor(), f"echo x > {escape_target}", workspace)
        assert not escape_target.exists()

    @sandboxed
    @pytest.mark.asyncio
    async def test_writing_inside_the_workspace_still_works(
        self, tmp_path, make_executor
    ):
        """A sandbox that breaks ordinary work gets turned off."""
        workspace = _workspace(tmp_path)
        target = workspace / "built"
        done = await _run(make_executor(), f"echo x > {target}", workspace)
        assert target.exists(), done.stderr.decode()

    @sandboxed
    @pytest.mark.asyncio
    async def test_reading_a_secret_is_refused(
        self, tmp_path, make_executor, secret_probe
    ):
        workspace = _workspace(tmp_path)
        done = await _run(make_executor(), f"cat {secret_probe}", workspace)
        assert b"PRIVATE-KEY-CONTENTS" not in done.stdout
        assert done.exit_code != 0

    @sandboxed
    @pytest.mark.asyncio
    async def test_ordinary_reads_still_work(self, tmp_path, make_executor):
        workspace = _workspace(tmp_path)
        (workspace / "source.py").write_text("print('hello')")
        done = await _run(make_executor(), "cat source.py", workspace)
        assert b"hello" in done.stdout

    @sandboxed
    @pytest.mark.asyncio
    async def test_the_network_can_be_denied(self, tmp_path, make_executor):
        """Asked in the terms each backend actually works in.

        Seatbelt refuses the socket operation itself. bubblewrap gives
        the command a network namespace of its own, where a socket still
        opens and binds happily because loopback is there; what is gone
        is every interface that leads anywhere. One assertion covering
        both would have to be weak enough to prove nothing, which is how
        this test passed on macOS while proving nothing on Linux.
        """
        workspace = _workspace(tmp_path)
        executor = make_executor(allow_network=False)
        done = await _run(executor, _NETWORK_PROBES[executor.name], workspace)
        assert b"reachable" not in done.stdout, done.stdout

    @sandboxed
    @pytest.mark.asyncio
    async def test_the_network_is_there_when_allowed(
        self, tmp_path, make_executor
    ):
        workspace = _workspace(tmp_path)
        executor = make_executor(allow_network=True)
        done = await _run(executor, _NETWORK_PROBES[executor.name], workspace)
        assert b"reachable" in done.stdout, done.stderr


@needs_sandbox
class TestConfinementSurvivesChildren:
    """A policy that only bound the first process would be theatre: the
    model can write `sh -c` as easily as anything else."""

    @sandboxed
    @pytest.mark.asyncio
    async def test_a_grandchild_is_confined_too(
        self, tmp_path, make_executor, escape_target
    ):
        workspace = _workspace(tmp_path)
        await _run(
            make_executor(),
            f"sh -c 'sh -c \"echo x > {escape_target}\"'",
            workspace,
        )
        assert not escape_target.exists()


class TestSelection:
    """What is chosen, and what is reported when nothing can be."""

    def test_off_means_off(self):
        from quaso.config import SandboxConfig
        from quaso.execution.registry import create_executor

        executor = create_executor(SandboxConfig(mode="off"))
        assert isinstance(executor, DirectExecutor)

    def test_auto_takes_what_the_platform_has(self):
        from quaso.config import SandboxConfig
        from quaso.execution.registry import create_executor

        executor = create_executor(SandboxConfig())
        if _sandboxes():
            assert not isinstance(executor, DirectExecutor)
        else:
            assert isinstance(executor, DirectExecutor)

    def test_no_backend_available_still_runs(self, monkeypatch):
        """A machine with no sandbox should get an agent, not an error."""
        from quaso.config import SandboxConfig
        from quaso.execution import registry

        monkeypatch.setattr(registry, "_BACKENDS", [])
        executor = registry.create_executor(SandboxConfig())
        assert isinstance(executor, DirectExecutor)

    def test_what_is_in_force_is_never_a_guess(self):
        """The banner reports the executor, not the configuration, so a
        sandbox that could not start cannot look like one that did."""
        from quaso.config import SandboxConfig
        from quaso.execution import registry

        asked = SandboxConfig(mode="auto")
        executor = registry.create_executor(asked)
        assert executor.describe()
        if isinstance(executor, DirectExecutor):
            assert "no sandbox" in executor.describe()


class TestBubblewrapArguments:
    """The Linux backend cannot run here, but the command it builds can
    still be checked. CI runs Linux, where the escapes above cover it."""

    def test_only_the_workspace_and_temp_are_writable(self, tmp_path):
        """The same two places the macOS profile permits. Everything
        else is bound read-only or is not there at all."""
        import tempfile

        from quaso.execution.sandbox_linux import BubblewrapExecutor

        argv = BubblewrapExecutor()._argv("true", tmp_path)
        binds = {
            argv[i + 1] for i, item in enumerate(argv) if item == "--bind"
        }
        allowed = {
            str(tmp_path.resolve()),
            str(Path(tempfile.gettempdir()).resolve()),
            "/tmp",
        }
        assert binds <= allowed
        assert str(tmp_path.resolve()) in binds

    def test_the_home_directory_is_never_bound(self, tmp_path):
        from quaso.execution.sandbox_linux import BubblewrapExecutor

        argv = BubblewrapExecutor()._argv("true", tmp_path)
        assert str(Path.home()) not in argv

    def test_a_workspace_under_tmp_is_still_reachable(self, tmp_path):
        """A tmpfs over /tmp hides a workspace that lives under it, and
        a CI checkout does. Temp is bound, not replaced."""
        from quaso.execution.sandbox_linux import BubblewrapExecutor

        argv = BubblewrapExecutor()._argv("true", tmp_path)
        assert "--tmpfs" not in argv
        assert str(tmp_path.resolve()) in argv

    def test_temp_is_writable(self, tmp_path):
        """mktemp is ordinary, so temp has to be bound read-write."""
        import tempfile
        from pathlib import Path

        from quaso.execution.sandbox_linux import BubblewrapExecutor

        argv = BubblewrapExecutor()._argv("true", tmp_path)
        temp = str(Path(tempfile.gettempdir()).resolve())
        binds = [
            argv[i + 1] for i, item in enumerate(argv) if item == "--bind"
        ]
        assert temp in binds

    def test_the_network_switch_reaches_the_arguments(self, tmp_path):
        from quaso.execution.sandbox_linux import BubblewrapExecutor

        allowed = BubblewrapExecutor(allow_network=True)._argv("x", tmp_path)
        denied = BubblewrapExecutor(allow_network=False)._argv("x", tmp_path)
        assert "--unshare-net" not in allowed
        assert "--unshare-net" in denied


@needs_sandbox
class TestOrdinaryWorkStillWorks:
    """The failure mode that matters as much as an escape: a sandbox
    that breaks normal commands is one people switch off. Each of these
    was a real breakage found by running the suite under the sandbox."""

    @sandboxed
    @pytest.mark.asyncio
    async def test_dev_null_is_writable(self, tmp_path, make_executor):
        """pytest opens it at startup and dies with an internal error if
        it cannot, which reads as a bug in pytest."""
        workspace = _workspace(tmp_path)
        done = await _run(
            make_executor(), "echo x > /dev/null && echo fine", workspace
        )
        assert b"fine" in done.stdout

    @sandboxed
    @pytest.mark.asyncio
    async def test_a_python_process_runs(self, tmp_path, make_executor):
        workspace = _workspace(tmp_path)
        done = await _run(
            make_executor(), "python3 -c 'print(1 + 1)'", workspace
        )
        assert b"2" in done.stdout

    @sandboxed
    @pytest.mark.asyncio
    async def test_a_temporary_file_can_be_made(self, tmp_path, make_executor):
        workspace = _workspace(tmp_path)
        done = await _run(
            make_executor(),
            "t=$(mktemp) && echo x > $t && cat $t",
            workspace,
        )
        assert b"x" in done.stdout

    @sandboxed
    @pytest.mark.asyncio
    async def test_randomness_is_available(self, tmp_path, make_executor):
        workspace = _workspace(tmp_path)
        done = await _run(
            make_executor(),
            "python3 -c 'import secrets; print(secrets.token_hex(4))'",
            workspace,
        )
        assert done.exit_code == 0, done.stderr.decode()


class TestAFailedSandboxIsNotAPass:
    """The failure this suite is shaped to catch.

    On Ubuntu 23.10 and later, bwrap is installed but forbidden to make
    a user namespace, so every command fails before running. Each escape
    test then passes, because nothing happened: no file was written, no
    secret read, no socket opened. Only the ordinary-work tests notice,
    which is the whole reason they are here.
    """

    @pytest.mark.asyncio
    async def test_an_executor_that_runs_nothing_would_be_caught(
        self, tmp_path
    ):
        class Broken:
            async def run(self, command, cwd, timeout, max_bytes):
                return Completed(
                    stdout=b"",
                    stderr=b"bwrap: setting up uid map: Permission denied",
                    exit_code=1,
                )

        workspace = _workspace(tmp_path)
        done = await _run(
            Broken(), "echo x > /dev/null && echo fine", workspace
        )
        # The escape assertions would all hold. This one does not.
        assert b"fine" not in done.stdout

    def test_installed_is_not_the_same_as_usable(self):
        """available() runs bwrap rather than trusting `which`."""
        import inspect

        from quaso.execution.sandbox_linux import BubblewrapExecutor

        source = inspect.getsource(BubblewrapExecutor.available)
        assert "subprocess.run" in source
