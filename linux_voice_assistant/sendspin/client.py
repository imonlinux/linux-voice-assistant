from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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


# -----------------------------------------------------------------------------
# Milestone 2: publishable internal state model (integration contract)
# -----------------------------------------------------------------------------

@dataclass
class SendspinConnectionState:
    connected: bool = False
    endpoint: Optional[str] = None  # "ws://host:port/path"
    server_id: Optional[str] = None
    server_name: Optional[str] = None


@dataclass
class SendspinStreamState:
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bit_depth: Optional[int] = None


@dataclass
class SendspinPlaybackState:
    playback_state: str = "unknown"  # playing|paused|stopped|unknown
    stream: SendspinStreamState = field(default_factory=SendspinStreamState)


@dataclass
class SendspinInternalState:
    connection: SendspinConnectionState = field(default_factory=SendspinConnectionState)
    playback: SendspinPlaybackState = field(default_factory=SendspinPlaybackState)
    metadata: Dict[str, Any] = field(default_factory=dict)
