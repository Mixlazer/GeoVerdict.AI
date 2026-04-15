from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class A2AEnvelope:
    from_agent: str
    to_agent: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    protocol: str = "a2a-local-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "payload": self.payload,
            "created_at": self.created_at,
            "protocol": self.protocol,
        }


def build_handoff(from_agent: str, to_agent: str, payload: dict[str, Any]) -> dict[str, Any]:
    return A2AEnvelope(from_agent=from_agent, to_agent=to_agent, payload=payload).as_dict()
