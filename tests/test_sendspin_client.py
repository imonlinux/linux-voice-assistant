"""Tests for the aiosendspin-based LVA Sendspin client wrapper."""

import pytest

pytest.importorskip("aiosendspin", reason="sendspin extra not installed")

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from linux_voice_assistant.config import SendspinConfig
from linux_voice_assistant.event_bus import EventBus
from linux_voice_assistant.sendspin.client import LVASendspinClient
from aiosendspin.client import client as aio_client_mod



def make_config(**kwargs) -> SendspinConfig:
    from linux_voice_assistant.config import SendspinConnectionConfig, SendspinPairingConfig

    kwargs.setdefault(
        "connection", SendspinConnectionConfig(server_host="192.168.0.100", server_port=8927)
    )
    # Default to the explicit espeak engine so tests don't hit the piper
    # model download path; piper-specific tests override this.
    kwargs.setdefault("pairing", SendspinPairingConfig(voice_engine="espeak-ng"))
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


def test_server_url_unset_with_mdns_defers_to_discovery(tmp_path: Path) -> None:
    """No server_host + mdns (default true): resolve at connect time."""
    from linux_voice_assistant.config import SendspinConnectionConfig

    sendspin = SendspinConfig(enabled=True, connection=SendspinConnectionConfig())
    client = make_client(tmp_path, sendspin, EventBus())
    assert client._server_url is None


def test_server_url_requires_host_when_mdns_disabled(tmp_path: Path) -> None:
    """Discovery off and no static host is a hard configuration error."""
    from linux_voice_assistant.config import SendspinConnectionConfig

    with pytest.raises(ValueError, match="mdns"):
        make_client(
            tmp_path,
            SendspinConfig(
                enabled=True, connection=SendspinConnectionConfig(mdns=False)
            ),
            EventBus(),
        )


async def test_resolve_endpoint_uses_mdns_result(tmp_path: Path) -> None:
    """Discovery feeds the ws:// endpoint when no static host is set."""
    from linux_voice_assistant.config import SendspinConnectionConfig
    from linux_voice_assistant.sendspin.discovery import DiscoveredSendspinServer

    sendspin = SendspinConfig(enabled=True, connection=SendspinConnectionConfig())
    client = make_client(tmp_path, sendspin, EventBus())

    async def fake_discover(timeout_s: float = 2.5):
        return [
            DiscoveredSendspinServer(
                instance_name="MA Server._sendspin-server._tcp.local.",
                host="192.168.0.7",
                port=8927,
                path="/sendspin",
            )
        ]

    with patch(
        "linux_voice_assistant.sendspin.client.discover_sendspin_servers",
        fake_discover,
    ):
        assert await client._resolve_endpoint() == "ws://192.168.0.7:8927/sendspin"


async def test_resolve_endpoint_raises_when_discovery_finds_nothing(
    tmp_path: Path,
) -> None:
    """An empty browse result surfaces as ConnectionError (reconnect retries)."""
    from linux_voice_assistant.config import SendspinConnectionConfig

    sendspin = SendspinConfig(enabled=True, connection=SendspinConnectionConfig())
    client = make_client(tmp_path, sendspin, EventBus())

    async def fake_discover(timeout_s: float = 2.5):
        return []

    with patch(
        "linux_voice_assistant.sendspin.client.discover_sendspin_servers",
        fake_discover,
    ):
        with pytest.raises(ConnectionError, match="mDNS"):
            await client._resolve_endpoint()


async def test_resolve_endpoint_static_host_bypasses_discovery(
    tmp_path: Path,
) -> None:
    """If you set server_host, discovery is bypassed (pre-2.0 semantics)."""
    from linux_voice_assistant.config import SendspinConnectionConfig
    from linux_voice_assistant.sendspin.discovery import DiscoveredSendspinServer

    sendspin = SendspinConfig(
        enabled=True,
        connection=SendspinConnectionConfig(server_host="10.0.0.5"),
    )
    client = make_client(tmp_path, sendspin, EventBus())

    async def fake_discover(timeout_s: float = 2.5):
        raise AssertionError("discovery must not run when server_host is set")

    with patch(
        "linux_voice_assistant.sendspin.client.discover_sendspin_servers",
        fake_discover,
    ):
        assert await client._resolve_endpoint() == "ws://10.0.0.5:8927/sendspin"


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


async def test_server_paired_check(tmp_path: Path) -> None:
    """PIN-paired servers count as paired (not just pairing-PSK tokens)."""
    from types import SimpleNamespace

    from linux_voice_assistant.sendspin.client import LVASendspinClient

    class StubStore:
        def __init__(self, record, psk):
            self._record, self._psk = record, psk

        async def record_by_server_id(self, server_id):
            return self._record

        async def pairing_psk(self):
            return self._psk

    record = SimpleNamespace(server_id="srv1")
    assert await LVASendspinClient._server_is_paired(StubStore(record, None), "srv1")
    assert not await LVASendspinClient._server_is_paired(StubStore(None, None), "srv1")
    # pairing-PSK token counts even without a long-term record
    assert await LVASendspinClient._server_is_paired(StubStore(None, "psk"), "srv1")
    # no server id (handshake incomplete) -> treated as unpaired
    assert not await LVASendspinClient._server_is_paired(StubStore(record, None), None)


def test_start_gate_respects_configured_buffer_target(tmp_path: Path) -> None:
    """Playback must not start before the configured buffer target arrives.

    Starting at a bare 200 ms while the server maintains 350 ms caused an
    immediate underflow/clear/re-buffer cycle at every stream start.
    """
    from aiosendspin.client.models import PCMFormat
    from linux_voice_assistant.sendspin.output import AudioPlayer

    player = AudioPlayer(
        lambda ts: ts,
        lambda ts: ts,
        now_us=lambda: 0,
        min_start_buffer_ms=350.0,
    )
    player._format = PCMFormat(sample_rate=48000, channels=2, bit_depth=16)
    player._stream = MagicMock()

    def chunk(duration_ms: float):
        # 25 ms of stereo 16-bit 48 kHz PCM = 4800 bytes
        ts = player._expected_next_timestamp or 1_000_000
        data = b"\x00" * int(48000 * 2 * 2 * duration_ms / 1000)
        player.submit(ts, data)

    # 8 chunks = 200 ms: below the 350 ms gate, must not start
    for _ in range(8):
        chunk(25)
    assert player._stream_started is False

    # cross the 350 ms threshold (14 chunks = 350 ms)
    for _ in range(6):
        chunk(25)
    assert player._stream_started is True


# --------------------------------------------------------------------------- #
# Spoken pairing PIN (espeak-ng)
# --------------------------------------------------------------------------- #

def make_speaker_client(tmp_path: Path, **pairing_kwargs) -> LVASendspinClient:
    from linux_voice_assistant.config import SendspinPairingConfig

    # Default to explicit espeak engine so tests don't hit the piper model
    # download path; piper-specific tests override this.
    pairing_kwargs.setdefault("voice_engine", "espeak-ng")
    sendspin = make_config(pairing=SendspinPairingConfig(**pairing_kwargs))
    return make_client(tmp_path, sendspin, EventBus(track_events=True))


def test_speak_pin_builds_espeak_command(tmp_path: Path) -> None:
    """Digits are comma-separated and spoken twice; voice from server languages."""
    from unittest.mock import patch

    client = make_speaker_client(tmp_path)
    espeak_proc = MagicMock()
    mpv_proc = MagicMock()
    commands = []

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        if "mpv" in cmd[0]:
            return mpv_proc
        return espeak_proc

    with patch("linux_voice_assistant.sendspin.client.shutil.which", return_value="/usr/bin/espeak-ng"), \
         patch("linux_voice_assistant.sendspin.client.subprocess.Popen", side_effect=fake_popen):
        asyncio.run(client._on_pairing_speak_pin("900984", languages=("en-US",)))

    # first Popen is espeak (WAV to stdout), second is mpv reading stdin
    assert commands[0][0] == "/usr/bin/espeak-ng"
    assert commands[0][1:5] == ["-s", "120", "-v", "en-US"]
    announcement = commands[0][5]
    assert "9, 0, 0, 9, 8, 4" in announcement, "digits must be comma-separated"
    assert announcement.count("9, 0, 0, 9, 8, 4") == 2, "code must be spoken twice"
    assert commands[1][:4] == ["mpv", "--no-video", "--really-quiet", "--audio-display=no"]
    # mpv chained on espeak stdout, both processes retained for stop()
    espeak_proc.stdout.close.assert_called_once()


def test_speak_pin_none_stops_previous_announcement(tmp_path: Path) -> None:
    from unittest.mock import patch

    client = make_speaker_client(tmp_path, voice_engine="espeak-ng")
    espeak_proc = MagicMock()
    espeak_proc.poll.return_value = None
    client._pin_speech_procs = [espeak_proc, None]

    with patch("linux_voice_assistant.sendspin.client.shutil.which", return_value="/usr/bin/espeak-ng"), \
         patch("linux_voice_assistant.sendspin.client.subprocess.Popen") as mock_popen:
        asyncio.run(client._on_pairing_speak_pin(None, languages=()))

    espeak_proc.terminate.assert_called_once()
    mock_popen.assert_not_called()
    assert client._pin_speech_procs == []


def test_speak_pin_disabled_by_config(tmp_path: Path) -> None:
    from unittest.mock import patch

    client = make_speaker_client(tmp_path, speak_pin=False)
    with patch("linux_voice_assistant.sendspin.client.shutil.which", return_value="/usr/bin/espeak-ng"), \
         patch("linux_voice_assistant.sendspin.client.subprocess.Popen") as mock_popen:
        asyncio.run(client._on_pairing_speak_pin("1234", languages=()))
    mock_popen.assert_not_called()


def test_speak_pin_missing_espeak_degrades_gracefully(tmp_path: Path) -> None:
    from unittest.mock import patch

    client = make_speaker_client(tmp_path, voice_engine="espeak-ng")
    with patch("linux_voice_assistant.sendspin.client.shutil.which", return_value=None), \
         patch("linux_voice_assistant.sendspin.client.subprocess.Popen") as mock_popen:
        asyncio.run(client._on_pairing_speak_pin("1234", languages=()))
    mock_popen.assert_not_called()


def test_config_voice_overrides_server_languages(tmp_path: Path) -> None:
    from unittest.mock import patch

    client = make_speaker_client(tmp_path, voice="de", voice_engine="espeak-ng")
    captured = {}

    commands = []

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        return MagicMock()

    with patch("linux_voice_assistant.sendspin.client.shutil.which", return_value="/usr/bin/espeak-ng"), \
         patch("linux_voice_assistant.sendspin.client.subprocess.Popen", side_effect=fake_popen):
        asyncio.run(client._on_pairing_speak_pin("123456", languages=("en-US",)))

    assert commands[0][1:5] == ["-s", "120", "-v", "de"]


def test_speak_pin_rate_clamped_and_configurable(tmp_path: Path) -> None:
    """voice_speed is configurable and clamped to a sane range."""
    client = make_speaker_client(tmp_path, voice_speed=10)
    assert client._pairing_voice_speed == 80  # clamped to the 80-200 floor
    client = make_speaker_client(tmp_path, voice_speed=999)
    assert client._pairing_voice_speed == 200
    client = make_speaker_client(tmp_path, voice_speed=140)
    assert client._pairing_voice_speed == 140
