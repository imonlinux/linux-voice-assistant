"""Sendspin EventBus handlers.

Milestone 5 extraction: this module contains the EventBus subscribers that
translate LVA events into Sendspin client actions.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..event_bus import EventBus, EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)


class SendspinDuckingHandler(EventHandler):
    """Listen to LVA voice lifecycle events and request duck/unduck."""

    def __init__(self, event_bus: EventBus, client: "SendspinClient") -> None:
        super().__init__(event_bus)
        self._client = client
        self._subscribe_all_methods()

    @subscribe
    def voice_listen(self, _data: Optional[dict] = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_thinking(self, _data: Optional[dict] = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_responding(self, _data: Optional[dict] = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_vad_start(self, _data: Optional[dict] = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_idle(self, _data: Optional[dict] = None) -> None:
        self._client.set_ducked(False)

    @subscribe
    def voice_error(self, _data: Optional[dict] = None) -> None:
        self._client.set_ducked(False)


class SendspinControllerCommandHandler(EventHandler):
    """Listen for EventBus events requesting Sendspin controller commands.

    Expected event name: `sendspin_controller_command`

    Payload example:
      {"command": "play"}
      {"command": "volume", "volume": 42}
      {"command": "mute", "mute": True}
    """

    def __init__(self, event_bus: EventBus, client: "SendspinClient") -> None:
        super().__init__(event_bus)
        self._client = client
        self._subscribe_all_methods()

    @subscribe
    def sendspin_controller_command(self, data: Optional[dict] = None) -> None:
        if not isinstance(data, dict):
            return

        cmd = data.get("command") or data.get("cmd")
        if not cmd:
            return

        volume = data.get("volume")
        mute = data.get("mute")

        # Fire and forget; avoid blocking the EventBus thread.
        try:
            self._client._loop.create_task(
                self._client.send_controller_command(str(cmd), volume=volume, mute=mute)
            )
        except Exception:
            _LOGGER.debug("Sendspin: failed to schedule controller command", exc_info=True)
