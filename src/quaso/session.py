"""Conversation state and JSONL transcripts.

Transcripts double as the resume format: every appended message is a line.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

from quaso.messages import Message, system
from quaso.private import make_private, private_dir

SESSIONS_DIR = Path(".quaso") / "sessions"


def sessions_dir(root: Path) -> Path:
    return root / SESSIONS_DIR


def list_sessions(root: Path) -> list[Path]:
    """Transcripts, newest first."""
    directory = sessions_dir(root)
    if not directory.is_dir():
        return []
    return sorted(
        directory.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def find_session(root: Path, session_id: str) -> Path | None:
    candidate = sessions_dir(root) / f"{session_id}.jsonl"
    if candidate.is_file():
        return candidate
    for path in list_sessions(root):
        if path.stem.startswith(session_id):
            return path
    return None


class Session:
    def __init__(
        self,
        system_prompt: str,
        root: Path | None = None,
        persist: bool = True,
    ) -> None:
        self._system_prompt = system_prompt
        self._root = root or Path.cwd()
        self._persist = persist
        self.messages: list[Message] = []
        self.transcript: Path | None = None
        self.reset()

    @property
    def id(self) -> str:
        return self.transcript.stem if self.transcript else "(unsaved)"

    def reset(self) -> None:
        # The transcript is named but not created until there is
        # something to say. Writing it up front made the session about to
        # start the newest one on disk, so --continue resumed the empty
        # file it had just made instead of yesterday's conversation.
        self.messages = [system(self._system_prompt)]
        self.transcript = None
        self._started = False

    def _start_transcript(self) -> None:
        if self._started or not self._persist:
            return
        # The suffix keeps two sessions started in the same second apart.
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        directory = private_dir(sessions_dir(self._root))
        self.transcript = directory / f"{stamp}-{uuid.uuid4().hex[:4]}.jsonl"
        self._started = True
        self.transcript.touch()
        make_private(self.transcript)
        self._write(self.messages[0])

    def set_system(self, system_prompt: str) -> None:
        """Replace the system prompt, keeping the conversation intact.

        Used when something the prompt describes changes mid-session, such
        as switching to a model the skills are targeted at differently.
        """
        self._system_prompt = system_prompt
        if self.messages and self.messages[0].role == "system":
            self.messages[0] = system(system_prompt)
        else:
            self.messages.insert(0, system(system_prompt))
        # A transcript already on disk still opens with the old prompt,
        # so a resumed session would replay instructions that no longer
        # apply. Rewrite it rather than append a second system message.
        if self.transcript is not None:
            self.replace_history(self.messages)

    def append(self, message: Message) -> None:
        self._start_transcript()
        self.messages.append(message)
        self._write(message)

    def replace_history(self, messages: list[Message]) -> None:
        self.messages = messages
        if self.transcript is not None:
            lines = "".join(m.model_dump_json() + "\n" for m in messages)
            self.transcript.write_text(lines)
            make_private(self.transcript)

    def repair_dangling_tool_calls(self) -> int:
        """Answer tool calls left unanswered by an interrupt.

        Most providers reject an assistant turn whose calls have no results.
        """
        answered = {m.tool_call_id for m in self.messages if m.role == "tool"}
        repairs = 0
        for message in list(self.messages):
            if message.role != "assistant":
                continue
            for call in message.tool_calls:
                if call.id in answered:
                    continue
                self.append(
                    Message(
                        role="tool",
                        content="Error: interrupted before this tool ran",
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )
                repairs += 1
        return repairs

    def load(self, path: Path) -> None:
        messages: list[Message] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                messages.append(Message.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        if not messages:
            raise ValueError(f"No usable messages in {path}")
        self.messages = messages
        self.transcript = path if self._persist else None
        # Adopted, so nothing should name a second file later.
        self._started = self.transcript is not None
        self.repair_dangling_tool_calls()

    def _write(self, message: Message) -> None:
        if self.transcript is None:
            return
        with self.transcript.open("a") as f:
            f.write(message.model_dump_json() + "\n")
