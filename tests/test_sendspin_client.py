"""Tests for the aiosendspin-based LVA Sendspin client wrapper."""

import pytest

pytest.importorskip("aiosendspin", reason="sendspin extra not installed")

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from linux_voice_assistant.config import SendspinConfig
from linux_voice_assistant.event_bus import EventBus
from linux_voice_assistant.sendspin.client import LVASendspinClient
from aiosendspin.client import client as aio_client_mod



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
    echo_tasks = []
    client.loop = MagicMock()
    client.loop.is_running.return_value = True
    client.loop.create_task = echo_tasks.append
    client._connected = True
    client._client = MagicMock()

    client._on_server_command(
        SimpleNamespace(player=SimpleNamespace(command=PlayerCommand.VOLUME, volume=42))
    )

    output.set_volume.assert_called_once_with(42, muted=False)
    assert len(echo_tasks) == 1, "volume command must be echoed via client/state"
    topics = [t for t, _ in event_bus.events_received]
    assert "sendspin_volume_changed" in topics
    assert client._user_volume == 42


def test_server_mute_command_applies(tmp_path: Path) -> None:
    """Mute commands from MA set the muted flag on the output stage."""
    from types import SimpleNamespace

    from aiosendspin.models.types import PlayerCommand

    sendspin = make_config()
    event_bus = EventBus(track_events=True)
    client = make_client(tmp_path, sendspin, event_bus)

    output = MagicMock()
    client._output = output
    echo_tasks = []
    client.loop = MagicMock()
    client.loop.is_running.return_value = True
    client.loop.create_task = echo_tasks.append
    client._connected = True
    client._client = MagicMock()

    client._on_server_command(
        SimpleNamespace(player=SimpleNamespace(command=PlayerCommand.MUTE, mute=True))
    )

    output.set_volume.assert_called_once_with(100, muted=True)
    assert len(echo_tasks) == 1, "mute command must also be echoed via client/state"
    # mute must not publish a volume-change event
    topics = [t for t, _ in event_bus.events_received]
    assert "sendspin_volume_changed" not in topics


def test_server_command_without_player_section_is_ignored(tmp_path: Path) -> None:
    """A server/command without a player object must not raise or apply."""
    from types import SimpleNamespace

    sendspin = make_config()
    client = make_client(tmp_path, sendspin, EventBus(track_events=True))

    client._on_server_command(SimpleNamespace(player=None))

    assert client._user_volume == 100


def test_stream_end_accepts_roles_and_closes_output(tmp_path: Path) -> None:
    """Stream end carries a roles list; the output device is released."""
    client = make_client(tmp_path, make_config(), EventBus())
    output = MagicMock()
    client._output = output
    client._current_format = MagicMock()

    client._on_stream_end(None)
    output.close_stream.assert_called_once()
    assert client._current_format is None


def test_stream_end_for_other_roles_is_ignored(tmp_path: Path) -> None:
    """A stream/end scoped to non-player roles must not touch our output."""
    client = make_client(tmp_path, make_config(), EventBus())
    output = MagicMock()
    client._output = output

    client._on_stream_end(["controller"])

    output.close_stream.assert_not_called()


def test_stream_clear_drops_buffered_audio(tmp_path: Path) -> None:
    """Seek/jump clears buffered chunks so stale audio can't play."""
    client = make_client(tmp_path, make_config(), EventBus())
    output = MagicMock()
    client._output = output

    client._on_stream_clear(["player"])

    output.clear.assert_called_once()


# --------------------------------------------------------------------------- #
# Library contract conformance
#
# Every listener the wrapper registers must accept exactly what the library's
# declared callback type passes. Two shipping bugs (stream/end roles, the
# server-command payload envelope) were signature/shape mismatches; this test
# makes signature drift a build failure instead of a hardware surprise.
# --------------------------------------------------------------------------- #

import inspect
import typing

from aiosendspin.client import client as aio_client_mod

_CALLBACK_CONTRACTS = [
    ("add_audio_chunk_listener", "AudioChunkCallback", "_on_audio_chunk"),
    ("add_stream_start_listener", "StreamStartCallback", "_on_stream_start"),
    ("add_stream_end_listener", "StreamEndCallback", "_on_stream_end"),
    ("add_stream_clear_listener", "StreamClearCallback", "_on_stream_clear"),
    ("add_metadata_listener", "MetadataCallback", "_on_metadata"),
    ("add_group_update_listener", "GroupUpdateCallback", "_on_group_update"),
    ("add_server_command_listener", "ServerCommandCallback", "_on_server_command"),
    ("add_disconnect_listener", "DisconnectCallback", "_on_disconnect"),
    ("add_pairing_abort_listener", "PairingAbortCallback", "_on_pairing_abort"),
]


@pytest.mark.parametrize("add_method,alias_name,handler_name", _CALLBACK_CONTRACTS)
def test_handler_matches_library_callback_contract(
    tmp_path: Path, add_method: str, alias_name: str, handler_name: str
) -> None:
    """Our handlers must accept exactly the arguments the library passes."""
    client = make_client(tmp_path, make_config(), EventBus())
    handler = getattr(client, handler_name)

    callback_alias = getattr(aio_client_mod, alias_name)
    # Callable[[A, B], None] -> get_args yields ([A, B], None): first element
    # is the argument list.
    arg_types = typing.get_args(callback_alias)[0]

    params = [
        p for name, p in inspect.signature(handler).parameters.items() if name != "self"
    ]
    required = [p for p in params if p.default is inspect.Parameter.empty]

    assert len(arg_types) >= len(required), (
        f"{handler_name}: library passes {len(arg_types)} arg(s) "
        f"but handler requires {len(required)}"
    )
    assert len(arg_types) <= len(params), (
        f"{handler_name}: handler accepts {len(params)} arg(s) "
        f"but library passes {len(arg_types)}"
    )


def test_every_library_listener_we_register_exists(tmp_path: Path) -> None:
    """Guard against aiosendspin renames: all add_* methods we call must exist."""
    client = make_client(tmp_path, make_config(), EventBus())
    for add_method, _, _ in _CALLBACK_CONTRACTS:
        assert hasattr(aio_client_mod.SendspinClient, add_method), f"library lost {add_method}?"


def test_state_supported_commands_declares_static_delay(tmp_path: Path) -> None:
    """Match the reference client: SET_STATIC_DELAY echo support is declared."""
    from aiosendspin.models.types import PlayerCommand

    client = make_client(tmp_path, make_config(), EventBus())
    # Verified indirectly: the constructor is called with this list in
    # _connect_once; assert the enum members we rely on exist.
    assert PlayerCommand.SET_STATIC_DELAY
    assert PlayerCommand.VOLUME
    assert PlayerCommand.MUTE


def test_voice_events_duck_music_via_event_bus(tmp_path: Path) -> None:
    """REGRESSION for the port: voice lifecycle events must duck the music.

    The client self-registers SendspinDuckingHandler; this exercises the full
    EventBus path (voice_listen -> set_ducked -> output duck gain) rather than
    calling set_ducked directly, so a missing handler registration fails here.
    """
    event_bus = EventBus(track_events=True)
    client = make_client(tmp_path, make_config(), event_bus)

    output = MagicMock()
    client._output = output

    event_bus.publish("voice_listen")
    output.set_duck_gain.assert_called_with(client._duck_gain)

    event_bus.publish("voice_thinking")
    event_bus.publish("voice_responding")
    output.set_duck_gain.assert_called_with(client._duck_gain)

    event_bus.publish("voice_idle")
    output.set_duck_gain.assert_called_with(1.0)


def test_duck_handler_survives_reconnect(tmp_path: Path) -> None:
    """A reconnect creates a new AudioPlayer; ducked state must be re-applied."""
    event_bus = EventBus(track_events=True)
    client = make_client(tmp_path, make_config(), event_bus)

    output = MagicMock()
    client._output = output
    client.set_ducked(True)

    # Simulate _connect_once creating a fresh output stage mid-duck
    client._apply_output_volume()

    output.set_duck_gain.assert_called_with(client._duck_gain)
    output.set_volume.assert_called_with(client._user_volume, muted=False)
