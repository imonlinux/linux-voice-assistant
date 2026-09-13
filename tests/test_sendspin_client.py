"""Tests for the aiosendspin-based LVA Sendspin client wrapper."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from linux_voice_assistant.config import SendspinConfig
from linux_voice_assistant.event_bus import EventBus
from linux_voice_assistant.sendspin.client import LVASendspinClient


def make_config(**kwargs) -> SendspinConfig:
    from linux_voice_assistant.config import SendspinConnectionConfig

    kwargs.setdefault(
        "connection", SendspinConnectionConfig(server_host="192.168.0.100", server_port=8927)
    )
    return SendspinConfig(**kwargs)


def make_client(tmp_path: Path, sendspin: SendspinConfig, event_bus: EventBus) -> LVASendspinClient:
    return LVASendspinClient(
        loop=asyncio.new_event_loop(),
        event_bus=event_bus,
        config=sendspin,
        identity_path=tmp_path / "identity.json",
        pairing_path=tmp_path / "pairing.json",
        client_name="Test Player",
    )


def test_server_url_from_config(tmp_path: Path) -> None:
    from linux_voice_assistant.config import SendspinConnectionConfig

    sendspin = SendspinConfig(
        enabled=True,
        connection=SendspinConnectionConfig(
            server_host="192.168.0.100", server_port=8927, server_path="/sendspin"
        ),
    )
    client = make_client(tmp_path, sendspin, EventBus())
    assert client._server_url == "ws://192.168.0.100:8927/sendspin"


def test_server_url_requires_host(tmp_path: Path) -> None:
    """A missing server_host fails fast at construction with a clear error."""
    with pytest.raises(ValueError, match="server_host"):
        make_client(tmp_path, SendspinConfig(enabled=True), EventBus())


def test_ducking_updates_output_gain(tmp_path: Path) -> None:
    """Voice events translate to a duck multiplier on the output stage."""
    sendspin = make_config()
    client = make_client(tmp_path, sendspin, EventBus())

    output = MagicMock()
    client._output = output

    client.set_ducked(True)
    output.set_duck_gain.assert_called_once_with(client._duck_gain)

    client.set_ducked(True)
    output.set_duck_gain.assert_called_once()  # no repeated calls for same state

    client.set_ducked(False)
    output.set_duck_gain.assert_called_with(1.0)


def test_ducking_disabled_by_config(tmp_path: Path) -> None:
    sendspin = make_config()
    sendspin.coordination.duck_during_voice = False
    client = make_client(tmp_path, sendspin, EventBus())

    output = MagicMock()
    client._output = output

    client.set_ducked(True)
    output.set_duck_gain.assert_not_called()


def test_server_volume_command_applies_and_publishes(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from aiosendspin.models.types import PlayerCommand

    sendspin = make_config()
    event_bus = EventBus(track_events=True)
    client = make_client(tmp_path, sendspin, event_bus)

    output = MagicMock()
    client._output = output
    client._on_server_command(
        SimpleNamespace(command=PlayerCommand.VOLUME, volume=42)
    )

    output.set_volume.assert_called_once_with(42, muted=False)
    topics = [t for t, _ in event_bus.events_received]
    assert "sendspin_volume_changed" in topics
    assert client._user_volume == 42
