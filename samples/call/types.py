# Excerpt from rotator (private repo). Full source: github.com/CreatmanCEO/rotator (access on request)
# This file is provided for code review purposes — it runs in context of the full project.

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

Speaker = Literal["me", "them", "system"]
Intent = Literal[
    "question_to_me",
    "command",
    "options",
    "correction",
    "clarification_response",
    "narration_query",
]
Mode = Literal["solo", "call"]


@dataclass
class Turn:
    text: str
    speaker: Speaker = "me"
    timestamp: float = field(default_factory=time.time)
    intent: Optional[Intent] = None
    claude_response: Optional[dict] = None


@dataclass
class ConversationState:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    turns: list[Turn] = field(default_factory=list)
    current_task: Optional[str] = None
    pending_clarification: Optional[str] = None
    mode: Mode = "solo"

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
        if len(self.turns) > 30:
            self.turns = self.turns[-30:]

    def last_n_seconds(self, seconds: float = 90.0) -> list[Turn]:
        cutoff = time.time() - seconds
        return [t for t in self.turns if t.timestamp >= cutoff]
