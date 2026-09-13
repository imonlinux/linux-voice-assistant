"""LVA Sendspin client built on aiosendspin.

Replaces the fork's hand-rolled resonate-era client (2026-09 protocol):
aiosendspin owns the connection (Noise encryption, pairing, fragmentation,
role negotiation, time sync) and hands us audio via callbacks. This module
owns the LVA-specific wiring:

- identity/pairing persistence (stable player identity across reboots)
- the synchronized output stage (vendored reference AudioPlayer)
- EventBus integration: voice-coordination ducking, state/metadata/volume
  publications consumed by other LVA subsystems
- pairing UX for a headless device: static PIN from config.json, PIN
  displayed in the daemon log, pairing window auto-opened while unpaired
"""

from __future__ import annotations

import asyncio
import copy
import logging
from pathlib import Path
from typing import Any, Optional, Union

from aiosendspin.client import SendspinClient as _AioSendspinClient
from aiosendspin.client.models import AudioFormat, PairingSupport
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.types import AudioCodec, GoodbyeReason, PlayerCommand, Roles
from aiosendspin.noise.trust_store import FileClientPairingStore

from ..config import SendspinConfig
from ..event_bus import EventBus
from .audio_devices import AudioDevice, query_devices
from .identity import load_or_create_identity
from .output import AudioPlayer

_LOGGER = logging.getLogger(__name__)

# How long after boot an unpaired client admits pairing attempts.
_UNPAIRED_PAIRING_WINDOW_S = 600.0
# Reconnect backoff bounds (seconds).
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 60.0


def _cfg_get(section: Any, key: str, default: Any) -> Any:
    """Read a key from a typed config section or a legacy raw dict."""
    if section is None:
        return default
    if isinstance(section, dict):
        value = section.get(key, default)
    else:
        value = getattr(section, key, default)
    return default if value is None else value


class LVASendspinClient:
    """LVA's Sendspin player: aiosendspin connection + synchronized output.

    Public surface preserved from the previous hand-rolled client so the
    daemon wiring (``_start_sendspin``) and the EventBus handlers in
    ``controller.py`` keep working: ``run()``, ``stop()``, ``disconnect()``,
    ``set_ducked()``, ``send_controller_command()``.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        event_bus: EventBus,
        config: Union[SendspinConfig, dict],
        *,
        identity_path: Path,
        pairing_path: Path,
        client_name: str,
        initial_volume: int = 100,
    ) -> None:
        self.loop = loop
        self.event_bus = event_bus
        self.config = config
        self.client_name = client_name

        connection_cfg = _cfg_get(config, "connection", {})
        player_cfg = _cfg_get(config, "player", {})
        pairing_cfg = _cfg_get(config, "pairing", {})
        coord_cfg = _cfg_get(config, "coordination", {})

        self._server_url = self._resolve_server_url(connection_cfg)
        self._min_buffer_ms = float(_cfg_get(player_cfg, "sync_target_latency_ms", 250))
        self._static_delay_ms = float(_cfg_get(player_cfg, "output_latency_ms", 0))
        self._buffer_capacity = int(_cfg_get(player_cfg, "buffer_capacity_bytes", 2_000_000))
        self._sample_rate = int(_cfg_get(player_cfg, "sample_rate", 48000))
        self._channels = int(_cfg_get(player_cfg, "channels", 2))
        self._bit_depth = int(_cfg_get(player_cfg, "bit_depth", 16))
        self._output_device_name = _cfg_get(player_cfg, "output_device", None)

        self._duck_during_voice = bool(_cfg_get(coord_cfg, "duck_during_voice", True))
        self._duck_gain = float(_cfg_get(coord_cfg, "duck_gain", 0.3))
        self._ducked = False

        self._static_pin = _cfg_get(pairing_cfg, "pin", None)
        self._identity_path = Path(identity_path)
        self._pairing_path = Path(pairing_path)

        self._client: Optional[_AioSendspinClient] = None
        self._output: Optional[AudioPlayer] = None
        self._current_format: Optional[AudioFormat] = None
        self._connected = False
        self._stopping = False
        self._user_volume = max(0, min(100, int(initial_volume)))
        self._muted = False

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_server_url(connection_cfg: Any) -> str:
        host = _cfg_get(connection_cfg, "server_host", None)
        port = int(_cfg_get(connection_cfg, "server_port", 8927) or 8927)
        path = str(_cfg_get(connection_cfg, "server_path", "/sendspin") or "/sendspin")
        if not host:
            raise ValueError(
                "sendspin.connection.server_host is not configured; set it to the "
                "Music Assistant server address in config.json"
            )
        return f"ws://{host}:{port}{path}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect, reconnecting with backoff until stopped."""
        backoff = _RECONNECT_MIN_S
        while not self._stopping:
            try:
                await self._connect_once()
                backoff = _RECONNECT_MIN_S
            except asyncio.CancelledError:
                raise
            except Exception:  # pylint: disable=broad-except
                if self._stopping:
                    return
                _LOGGER.warning(
                    "Sendspin: connection to %s failed; retrying in %.0fs",
                    self._server_url,
                    backoff,
                    exc_info=True,
                )
            await asyncio.sleep(backoff)
            backoff = min(_RECONNECT_MAX_S, backoff * 2)

    async def _connect_once(self) -> None:
        identity = load_or_create_identity(self._identity_path)
        pairing_store = await FileClientPairingStore.open(self._pairing_path)
        if self._static_pin:
            await pairing_store.set_static_pin(str(self._static_pin))

        client = _AioSendspinClient(
            identity=identity,
            client_name=self.client_name,
            roles=[Roles.PLAYER, Roles.CONTROLLER, Roles.METADATA],
            pairing_store=pairing_store,
            pairing_support=PairingSupport(
                gesture_prompt=self._on_pairing_gesture,
                pin_display=self._on_pairing_pin,
                offer_static_pin=self._static_pin is not None,
            ),
            player_support=ClientHelloPlayerSupport(
                supported_formats=[
                    SupportedAudioFormat(
                        codec=AudioCodec.PCM,
                        sample_rate=self._sample_rate,
                        channels=self._channels,
                        bit_depth=self._bit_depth,
                    ),
                ],
                buffer_capacity=self._buffer_capacity,
                supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
            ),
            min_buffer_ms=self._min_buffer_ms,
            static_delay_ms=self._static_delay_ms,
            initial_volume=self._user_volume,
            initial_muted=self._muted,
        )
        client.add_audio_chunk_listener(self._on_audio_chunk)
        client.add_stream_start_listener(self._on_stream_start)
        client.add_stream_end_listener(self._on_stream_end)
        client.add_metadata_listener(self._on_metadata)
        client.add_group_update_listener(self._on_group_update)
        client.add_server_command_listener(self._on_server_command)
        client.add_disconnect_listener(self._on_disconnect)

        self._client = client
        self._current_format = None
        self._output = AudioPlayer(
            client.compute_play_time,
            client.compute_server_time,
            now_us=client.clock.now_us,
            is_clock_synced=lambda: client.is_time_synchronized,
        )
        self._apply_output_volume()

        _LOGGER.info("Sendspin: connecting to %s", self._server_url)
        await client.connect(self._server_url)
        self._connected = True
        self._publish_connection_state(True)
        _LOGGER.info("Sendspin: connected (client_id=%s…)", identity.peer_id[:12])

        # Headless pairing UX: while unpaired, keep a pairing window open so
        # the operator can pair from MA without shell access.
        if not await pairing_store.pairing_psk():
            client.open_pairing_window()
            _LOGGER.info(
                "Sendspin: server not yet paired — pairing window open for %ss. "
                "Select the player in Music Assistant to pair.",
                int(_UNPAIRED_PAIRING_WINDOW_S),
            )

        # Block until the connection drops (or we are stopping).
        while not self._stopping and self._connected:
            await asyncio.sleep(0.25)

    def stop(self) -> None:
        self._stopping = True

    async def disconnect(self, reason: str = "shutdown") -> None:  # noqa: ARG002
        self._stopping = True
        self._connected = False
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Sendspin: disconnect failed", exc_info=True)

    # ------------------------------------------------------------------
    # Ducking / controller commands (previous public surface)
    # ------------------------------------------------------------------

    def set_ducked(self, ducked: bool) -> None:
        """Coordinate with voice activity: duck music while LVA listens/responds."""
        if not self._duck_during_voice:
            return
        ducked = bool(ducked)
        if ducked == self._ducked:
            return
        self._ducked = ducked
        self._apply_output_volume()
        _LOGGER.debug("Sendspin: ducked=%s", ducked)

    async def send_controller_command(
        self,
        command: str,
        volume: Optional[float] = None,
        mute: Optional[bool] = None,
    ) -> None:
        """Controller-role hook kept for EventBus compatibility (v1: log only)."""
        _LOGGER.debug("Sendspin: controller command %s (volume=%s mute=%s)", command, volume, mute)

    # ------------------------------------------------------------------
    # aiosendspin listeners
    # ------------------------------------------------------------------

    def _apply_output_volume(self) -> None:
        if self._output is None:
            return
        self._output.set_volume(self._user_volume, muted=self._muted)
        self._output.set_duck_gain(self._duck_gain if self._ducked else 1.0)

    def _on_audio_chunk(self, server_timestamp_us: int, audio_data: bytes, fmt: AudioFormat) -> None:
        if self._output is None:
            return
        if self._current_format != fmt:
            # PCM-only v1: mid-stream format changes re-open the device
            # (a brief click is acceptable; FLAC/Opus support comes later).
            device = self._select_output_device()
            self._output.set_format(fmt, device=device)
            self._current_format = fmt
            self._apply_output_volume()
        self._output.submit(server_timestamp_us, audio_data)

    def _on_stream_start(self, message: Any) -> None:
        self.event_bus.publish("sendspin_playback_state", {"state": "playing"})
        _LOGGER.info("Sendspin: stream started")

    def _on_stream_end(self) -> None:
        if self._output is not None:
            self._output.clear()
        self.event_bus.publish("sendspin_playback_state", {"state": "stopped"})
        _LOGGER.info("Sendspin: stream ended")

    def _on_metadata(self, metadata: Any) -> None:
        try:
            self.event_bus.publish("sendspin_metadata", copy.deepcopy(metadata))
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Sendspin: failed to publish metadata", exc_info=True)

    def _on_group_update(self, update: Any) -> None:
        try:
            self.event_bus.publish(
                "sendspin_playback_state",
                {
                    "state": str(getattr(update, "playback_state", "")),
                    "group_id": getattr(update, "group_id", None),
                },
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Sendspin: failed to publish group update", exc_info=True)

    def _on_server_command(self, payload: Any) -> None:
        """Server-initiated volume/mute changes apply to the output stage."""
        command = getattr(payload, "command", None)
        if command == PlayerCommand.VOLUME:
            self._user_volume = int(getattr(payload, "volume", 100))
        elif command == PlayerCommand.MUTE:
            self._muted = bool(getattr(payload, "mute", False))
        else:
            return
        self._apply_output_volume()
        self.event_bus.publish("sendspin_volume_changed", {"volume": self._user_volume})

    def _on_disconnect(self) -> None:
        self._connected = False
        self._publish_connection_state(False)
        _LOGGER.info("Sendspin: disconnected from server")

    # ------------------------------------------------------------------
    # Pairing UX (headless)
    # ------------------------------------------------------------------

    async def _on_pairing_gesture(self, active: bool) -> None:
        """Auto-open the window when a gated pairing attempt starts."""
        if active:
            _LOGGER.info("Sendspin: pairing attempt detected — opening pairing window")
            if self._client is not None:
                self._client.open_pairing_window()
        else:
            _LOGGER.info("Sendspin: pairing wait ended")

    async def _on_pairing_pin(self, pin: Optional[str]) -> None:
        """PinDisplay out-channel: the PIN goes to the daemon log."""
        if pin is None:
            _LOGGER.info("Sendspin: pairing ended; PIN cleared")
        else:
            _LOGGER.info("Sendspin: PAIRING PIN — enter this in Music Assistant: %s", pin)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_output_device(self) -> AudioDevice:
        devices = query_devices()
        if self._output_device_name:
            for device in devices:
                if device.name == self._output_device_name:
                    return device
            _LOGGER.warning(
                "Sendspin: output device %r not found; using system default",
                self._output_device_name,
            )
        for device in devices:
            if device.is_default:
                return device
        return devices[0]

    def _publish_connection_state(self, connected: bool) -> None:
        self.event_bus.publish(
            "sendspin_connection_state",
            {"connected": connected, "endpoint": self._server_url},
        )
