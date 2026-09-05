"""How a shell command is actually run.

The permission prompt decides whether a command may run. What it cannot
decide is what the command does once it is running: it guards the thing
quaso executes, not the thing that thing goes on to execute. An executor
is the second half of that, and the only part the kernel enforces.

It covers shell commands and nothing else. The file tools run inside
quaso's own process, where there is no child to confine, so for those
the workspace boundary in PermissionPolicy remains the only control and
`--mode yolo` still turns it off.
"""

from quaso.execution.base import Completed, Executor
from quaso.execution.direct import DirectExecutor

__all__ = ["Completed", "DirectExecutor", "Executor"]
