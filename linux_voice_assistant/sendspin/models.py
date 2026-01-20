from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DiscoveredSendspinServer:
    """A discovered Sendspin server endpoint."""
    instance_name: str
    host: str
    port: int
    path: str = "/sendspin"
    properties: Dict[str, str] | None = None

    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"


@dataclass
class SendspinSessionInfo:
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    active_roles: List[str] = None  # e.g. ["player@v1", "controller@v1"]

    def __post_init__(self) -> None:
        if self.active_roles is None:
            self.active_roles = []
