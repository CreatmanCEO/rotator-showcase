# Excerpt from rotator (private repo). Full source: github.com/CreatmanCEO/rotator (access on request)
# This file is provided for code review purposes — it runs in context of the full project.

"""Rolling transcript buffer + markdown persistence.

Partial STT events update current_partial in-memory only (cheap).
Finals commit a Turn to ConversationState and append a line to the
session's markdown file at ~/.rotator/calls/<session_id>.md.

File handle is kept open for the buffer's lifetime; close() is exposed
for the app to call on shutdown.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, TextIO

from rotator.call.types import ConversationState, Speaker, Turn


class TranscriptBuffer:
    def __init__(self, *, state: ConversationState, dir: Path) -> None:
        self.state = state
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{state.session_id}.md"
        self.current_partial: str = ""
        needs_header = not self.path.exists()
        self._fh: Optional[TextIO] = self.path.open("a", encoding="utf-8")
        if needs_header:
            self._fh.write(
                f"# Call session {state.session_id} — {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            )
            self._fh.flush()

    def update_partial(self, text: str) -> None:
        self.current_partial = text

    def append_final(self, text: str, speaker: Speaker = "me") -> Turn:
        turn = Turn(text=text, speaker=speaker)
        self.state.add_turn(turn)
        self.current_partial = ""
        if self._fh is not None:
            self._fh.write(f"**{speaker}** [{time.strftime('%H:%M:%S')}]: {text}\n\n")
            self._fh.flush()
        return turn

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "TranscriptBuffer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
