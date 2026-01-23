#!/usr/bin/env python3
"""
Sendspin client (LVA -> Music Assistant)

- Dict-or-object tolerant config access
- Control-plane handshake (client/hello -> wait server/hello -> state/time/heartbeats)
- player@v1 streaming:
  - stream/start: spawn mpv reading raw PCM from stdin
  - binary frames: strip 9-byte header, forward PCM to mpv stdin
- Volume/mute/ducking:
  - Uses mpv IPC (--input-ipc-server=...) to set mpv volume + mute
  - Ducking temporarily reduces mpv volume to user_volume * duck_percent/100
- Publishes sendspin_volume_changed on EventBus when MA changes volume.
- Milestone 1 hardening:
  - Honor sendspin.coordination.duck_during_voice
  - Harden mpv IPC apply with quiet retries during startup
  - Publish sendspin_audio_state for downstream consumers

Milestone 2:
- Maintain minimal internal state object (connection/playback/stream/metadata)
- Emit events with stable payload shapes:
  - sendspin_connection_state
  - sendspin_playback_state
  - sendspin_metadata
  - sendspin_audio_state (from Milestone 1)
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import shutil
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional, Tuple

import websockets
from websockets.exceptions import ConnectionClosed

from ..event_bus import EventBus, EventHandler, subscribe

_LOGGER = logging.getLogger(__name__)

# Silence websockets frame dumps (keeps our Sendspin debug logs intact)
for name in (
    "websockets",
    "websockets.client",
    "websockets.server",
    "websockets.protocol",
    "websockets.frames",
):
    logging.getLogger(name).setLevel(logging.WARNING)

_SENDSPIN_SERVICE_TYPE = "_sendspin-server._tcp.local."
_BINARY_HEADER_LEN = 9


# ---------------------------------------------------------------------------
# Discovery / endpoint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SendspinEndpoint:
    host: str
    port: int
    path: str = "/sendspin"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"


# ---------------------------------------------------------------------------
# Milestone 2: Minimal publishable state object
# ---------------------------------------------------------------------------

@dataclass
class _ConnectionState:
    connected: bool = False
    endpoint: Optional[str] = None
    server_id: Optional[str] = None
    server_name: Optional[str] = None


@dataclass
class _StreamState:
    codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bit_depth: Optional[int] = None


@dataclass
class _PlaybackState:
    playback_state: str = "unknown"  # playing/paused/stopped/unknown


@dataclass
class _SendspinState:
    connection: _ConnectionState = field(default_factory=_ConnectionState)
    playback: _PlaybackState = field(default_factory=_PlaybackState)
    stream: _StreamState = field(default_factory=_StreamState)
    metadata: Optional[dict] = None  # preserve structure (no normalization yet)


# ---------------------------------------------------------------------------
# Ducking handler (EventBus -> client duck state)
# ---------------------------------------------------------------------------

class _SendspinDuckingHandler(EventHandler):
    """Listens to LVA voice lifecycle events and requests duck/unduck."""

    def __init__(self, event_bus: EventBus, client: "SendspinClient") -> None:
        super().__init__(event_bus)
        self._client = client
        self._subscribe_all_methods()

    @subscribe
    def voice_listen(self, _data: dict | None = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_thinking(self, _data: dict | None = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_responding(self, _data: dict | None = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_vad_start(self, _data: dict | None = None) -> None:
        self._client.set_ducked(True)

    @subscribe
    def voice_idle(self, _data: dict | None = None) -> None:
        self._client.set_ducked(False)

    @subscribe
    def voice_error(self, _data: dict | None = None) -> None:
        self._client.set_ducked(False)


# ---------------------------------------------------------------------------
# Controller command handler (EventBus -> client controller commands)
# ---------------------------------------------------------------------------

class _SendspinControllerCommandHandler(EventHandler):
    """Listens for EventBus events requesting Sendspin controller commands.

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
    def sendspin_controller_command(self, data: dict | None = None) -> None:
        if not isinstance(data, dict):
            return

        cmd = data.get("command") or data.get("cmd")
        if not cmd:
            return

        volume = data.get("volume")
        mute = data.get("mute")
        self._client._loop.create_task(
            self._client.send_controller_command(str(cmd), volume=volume, mute=mute)
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SendspinClient:
    """
    Minimal Sendspin client that keeps MA player online and ready.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        event_bus: EventBus,
        config: Any,
        client_id: str,
        client_name: str,
    ) -> None:
        self._loop = loop
        self._event_bus = event_bus
        self._cfg = config
        self._client_id = client_id
        self._client_name = client_name

        self._stop_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()

        # Milestone 2 publishable state
        self._state = _SendspinState()
        self._last_pub_connection: Optional[dict] = None
        self._last_pub_playback: Optional[dict] = None
        self._last_pub_metadata: Optional[dict] = None

        # Player state we report to MA (0-100)
        self._volume: int = 100
        self._muted: bool = False

        # Ducking state
        self._ducked: bool = False
        self._duck_percent: int = 20
        self._duck_enabled: bool = True  # sendspin.coordination.duck_during_voice

        # Subscribe ducking handler to LVA event bus
        self._ducking_handler = _SendspinDuckingHandler(event_bus=self._event_bus, client=self)

        # Subscribe controller command handler to LVA event bus
        # Event: sendspin_controller_command {command: str, volume?: 0-100, mute?: bool}
        self._controller_handler = _SendspinControllerCommandHandler(event_bus=self._event_bus, client=self)

        # Subscribe controller command handler to LVA event bus
        self._controller_command_handler = _SendspinControllerCommandHandler(event_bus=self._event_bus, client=self)

        # Operational state per Sendspin spec: synchronized|error|external_source
        self._op_state: str = "synchronized"

        # Server identity (from server/hello)
        self._server_id: Optional[str] = None
        self._server_name: Optional[str] = None
        self._active_roles: Tuple[str, ...] = ()

        # Controller role: server-advertised supported commands (server/state controller.supported_commands)
        self._controller_supported_commands: set[str] = set()

        # Websocket handle
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

        # Discovery cache
        self._last_endpoint: Optional[SendspinEndpoint] = None

        # Streaming state (local)
        self._stream_active: bool = False
        self._stream_codec: str = "pcm"
        self._stream_rate: int = 48000
        self._stream_channels: int = 2
        self._stream_bit_depth: int = 16

        self._pcm_proc: Optional[asyncio.subprocess.Process] = None
        self._pcm_writer_task: Optional[asyncio.Task] = None
        self._pcm_stderr_task: Optional[asyncio.Task] = None
        self._pcm_wait_task: Optional[asyncio.Task] = None
        self._pcm_queue: "asyncio.Queue[bytes]" = asyncio.Queue(maxsize=200)
        # Decoder / transcoder state (for non-PCM codecs)
        self._decoder_proc: Optional[asyncio.subprocess.Process] = None
        self._decoder_writer_task: Optional[asyncio.Task] = None
        self._decoder_reader_task: Optional[asyncio.Task] = None
        self._decoder_stderr_task: Optional[asyncio.Task] = None
        self._encoded_queue: "asyncio.Queue[bytes]" = asyncio.Queue(maxsize=200)

        # Opus decoder (optional dependency; used when negotiated codec is 'opus')
        self._opus_decoder: Any = None
        self._opus_max_frame_size: int = 0
        self._opus_available: bool = False

        # FLAC decoder availability (ffmpeg required)
        self._ffmpeg_path: str = "ffmpeg"
        self._ffmpeg_available: bool = False

        # mpv stderr tail buffer (for post-mortem)
        self._mpv_stderr_tail: Deque[str] = deque(maxlen=40)

        # mpv IPC
        self._mpv_ipc_path: Optional[str] = None
        self._mpv_ipc_ready: bool = False
        self._mpv_ipc_lock = asyncio.Lock()

        # Milestone 1: track last published audio state to dedupe
        self._last_audio_state: Optional[dict] = None

        # Diagnostics for binary frame flow
        self._pcm_frame_count: int = 0
        self._pcm_first_frame_at: Optional[float] = None
        self._pcm_last_frame_at: Optional[float] = None

        # Session diagnostics
        self._current_endpoint_url: Optional[str] = None
        self._last_rx_type: Optional[str] = None
        self._last_rx_at: Optional[float] = None
        self._last_bin_at: Optional[float] = None

        # IMPORTANT: stream stop is normal on pause/track-change.
        # We only trigger websocket disconnect when the PCM sink fails unexpectedly.
        self._sink_failed: bool = False
        # Pull duck percent from config (dict or object)
        try:
            player_cfg = self._cfg_get_section(self._cfg, 'player')
            if player_cfg is not None:
                self._duck_percent = int(
                    self._cfg_get(player_cfg, 'duck_volume_percent', self._duck_percent) or self._duck_percent
                )
        except Exception:
            pass
        self._duck_percent = max(0, min(100, int(self._duck_percent)))

        # Pull coordination settings from config
        self._refresh_coordination_settings(log=True)

        # Cache decoder availability for codec advertisement and runtime decisions
        try:
            player_cfg = self._cfg_get_section(self._cfg, "player")
            if player_cfg is not None:
                self._ffmpeg_path = str(self._cfg_get(player_cfg, "ffmpeg_path", self._ffmpeg_path) or self._ffmpeg_path)
        except Exception:
            pass
        try:
            import opuslib  # type: ignore
            self._opus_available = True
        except Exception:
            self._opus_available = False
        self._ffmpeg_available = shutil.which(self._ffmpeg_path) is not None

    # ---------------------------------------------------------------------
    # Milestone 2: Event emission (stable payloads)
    # ---------------------------------------------------------------------

    def _emit_connection_state(self) -> None:
        payload = {
            "connected": bool(self._state.connection.connected),
            "endpoint": self._state.connection.endpoint,
            "server_id": self._state.connection.server_id,
            "server_name": self._state.connection.server_name,
        }
        if payload == self._last_pub_connection:
            return
        self._last_pub_connection = payload
        _LOGGER.info(
            "Sendspin: connection_state -> %s endpoint=%s server=%s id=%s",
            "connected" if payload["connected"] else "disconnected",
            payload["endpoint"],
            payload["server_name"],
            payload["server_id"],
        )
        try:
            self._event_bus.publish("sendspin_connection_state", payload)
        except Exception:
            _LOGGER.debug("Sendspin: failed to publish sendspin_connection_state", exc_info=True)

    def _emit_playback_state(self) -> None:
        payload = {
            "playback_state": self._state.playback.playback_state or "unknown",
            "codec": self._state.stream.codec,
            "sample_rate": self._state.stream.sample_rate,
            "channels": self._state.stream.channels,
            "bit_depth": self._state.stream.bit_depth,
        }
        if payload == self._last_pub_playback:
            return
        self._last_pub_playback = payload
        try:
            self._event_bus.publish("sendspin_playback_state", payload)
        except Exception:
            _LOGGER.debug("Sendspin: failed to publish sendspin_playback_state", exc_info=True)

    def _emit_metadata(self, metadata: dict) -> None:
        # Defensive copy so dedupe comparisons are stable even if upstream mutates dicts.
        safe_metadata = copy.deepcopy(metadata)
        payload = {"metadata": safe_metadata}
        if payload == self._last_pub_metadata:
            return
        self._last_pub_metadata = payload
        try:
            self._event_bus.publish("sendspin_metadata", payload)
        except Exception:
            _LOGGER.debug("Sendspin: failed to publish sendspin_metadata", exc_info=True)

    def _set_connection(self, *, connected: bool, endpoint: Optional[str] = None) -> None:
        changed = False
        if self._state.connection.connected != bool(connected):
            self._state.connection.connected = bool(connected)
            changed = True
        if endpoint is not None and self._state.connection.endpoint != endpoint:
            self._state.connection.endpoint = endpoint
            changed = True
        if self._state.connection.server_id != self._server_id:
            self._state.connection.server_id = self._server_id
            changed = True
        if self._state.connection.server_name != self._server_name:
            self._state.connection.server_name = self._server_name
            changed = True
        if changed:
            self._emit_connection_state()

    def _set_playback(self, playback_state: str) -> None:
        ps = str(playback_state or "unknown")
        if ps not in ("playing", "paused", "stopped", "unknown"):
            ps = "unknown"
        if ps == self._state.playback.playback_state:
            return
        self._state.playback.playback_state = ps
        _LOGGER.info("Sendspin: playback_state -> %s", ps)
        self._emit_playback_state()

    def _set_stream(self, *, codec: Optional[str], rate: Optional[int], ch: Optional[int], depth: Optional[int]) -> None:
        changed = False
        if codec != self._state.stream.codec:
            self._state.stream.codec = codec
            changed = True
        if rate != self._state.stream.sample_rate:
            self._state.stream.sample_rate = rate
            changed = True
        if ch != self._state.stream.channels:
            self._state.stream.channels = ch
            changed = True
        if depth != self._state.stream.bit_depth:
            self._state.stream.bit_depth = depth
            changed = True
        if changed:
            self._emit_playback_state()

    # ---------------------------------------------------------------------
    # Config helpers (dict-or-object tolerant)
    # ---------------------------------------------------------------------

    @staticmethod
    def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _cfg_get_section(cls, obj: Any, key: str) -> Any:
        val = cls._cfg_get(obj, key, None)
        if isinstance(val, dict):
            return val
        if val is None:
            return None
        return val

    # ---------------------------------------------------------------------
    # Coordination / ducking config
    # ---------------------------------------------------------------------

    def _refresh_coordination_settings(self, *, log: bool = False) -> None:
        old = self._duck_enabled
        new = old
        raw_val = None
        try:
            coord_cfg = self._cfg_get_section(self._cfg, "coordination")
            if coord_cfg is not None:
                raw_val = self._cfg_get(coord_cfg, "duck_during_voice", old)
                new = bool(raw_val)
        except Exception:
            new = old

        self._duck_enabled = new

        if log:
            _LOGGER.info(
                "Sendspin: coordination loaded (duck_during_voice=%s, raw=%r, cfg_type=%s)",
                self._duck_enabled,
                raw_val,
                type(self._cfg).__name__,
            )

        if not self._duck_enabled and self._ducked:
            self._ducked = False
            self._publish_audio_state()
            if self._stream_active:
                self._loop.create_task(self._apply_mpv_audio_state())

    # ---------------------------------------------------------------------
    # Milestone 1: publish audio state
    # ---------------------------------------------------------------------

    def _publish_audio_state(self) -> None:
        payload = {
            "volume": int(self._volume),
            "muted": bool(self._muted),
            "ducked": bool(self._ducked),
            "duck_percent": int(self._duck_percent),
            "effective_volume": int(self._effective_mpv_volume()),
            "duck_enabled": bool(self._duck_enabled),
        }
        if payload == self._last_audio_state:
            return
        self._last_audio_state = payload
        try:
            self._event_bus.publish("sendspin_audio_state", payload)
        except Exception:
            _LOGGER.debug("Sendspin: failed to publish sendspin_audio_state", exc_info=True)

    # ---------------------------------------------------------------------
    # Public control
    # ---------------------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()
        self._disconnect_event.set()

    def set_ducked(self, ducked: bool) -> None:
        if not self._duck_enabled:
            _LOGGER.debug("Sendspin: duck request ignored (duck_during_voice=false)")
            if self._ducked:
                self._ducked = False
                self._publish_audio_state()
                if self._stream_active:
                    self._loop.create_task(self._apply_mpv_audio_state())
            return

        new_val = bool(ducked)
        if new_val == self._ducked:
            return

        self._ducked = new_val
        self._publish_audio_state()

        if self._stream_active:
            self._loop.create_task(self._apply_mpv_audio_state())

    async def disconnect(self, *, reason: str = "disconnect") -> None:
        await self._stop_stream(reason=reason)

        ws = self._ws
        self._ws = None
        try:
            if ws is not None:
                await ws.close(code=1000, reason=reason)
        except Exception:
            _LOGGER.debug("Sendspin: ws close failed", exc_info=True)



    async def request_pcm_fallback(
        self,
        *,
        reason: str,
        sample_rate: int = 48000,
        channels: int = 2,
        bit_depth: int = 16,
    ) -> None:
        """Request a PCM stream format from the server (best-effort fallback)."""
        ws = self._ws
        if ws is None:
            _LOGGER.debug("Sendspin: cannot request PCM fallback (not connected)")
            return

        payload = {
            "player": {
                "codec": "pcm",
                "sample_rate": int(sample_rate),
                "channels": int(channels),
                "bit_depth": int(bit_depth),
            }
        }
        try:
            await self._send_json(ws, {"type": "stream/request-format", "payload": payload})
            _LOGGER.warning("Sendspin: requested PCM fallback (%s)", reason)
        except Exception:
            _LOGGER.debug("Sendspin: failed to request PCM fallback", exc_info=True)

    async def send_controller_command(
        self,
        command: str,
        *,
        volume: Optional[int] = None,
        mute: Optional[bool] = None,
    ) -> bool:
        """Send a Sendspin controller command (client -> server).

        Returns True if the message was queued to the websocket; False otherwise.

        Commands are validated against the server-advertised supported_commands
        (best-effort). If the server hasn't advertised supported_commands yet,
        the command is still sent.
        """
        ws = self._ws
        if ws is None:
            _LOGGER.debug("Sendspin: controller command ignored (not connected): %s", command)
            return False

        cmd = str(command or "").strip()
        if not cmd:
            return False

        if self._controller_supported_commands and cmd not in self._controller_supported_commands:
            _LOGGER.warning(
                "Sendspin: controller command '%s' not in server supported_commands=%s; sending anyway",
                cmd,
                sorted(self._controller_supported_commands),
            )

        controller_obj: Dict[str, Any] = {"command": cmd}
        if cmd == "volume" and volume is not None:
            try:
                controller_obj["volume"] = max(0, min(100, int(volume)))
            except Exception:
                pass
        if cmd == "mute" and mute is not None:
            controller_obj["mute"] = bool(mute)

        try:
            await self._send_json(ws, {"type": "client/command", "payload": {"controller": controller_obj}})
            _LOGGER.debug("Sendspin: sent controller command %s", controller_obj)
            return True
        except Exception:
            _LOGGER.debug("Sendspin: failed to send controller command", exc_info=True)
            return False
    # ---------------------------------------------------------------------
    # Main loop
    # ---------------------------------------------------------------------

    async def run(self) -> None:
        enabled = bool(self._cfg_get(self._cfg, "enabled", False))
        if not enabled:
            _LOGGER.info("Sendspin: disabled; not starting")
            return

        self._refresh_coordination_settings(log=True)

        try:
            initial = self._cfg_get_section(self._cfg, "initial")
            if initial is not None:
                self._volume = int(self._cfg_get(initial, "volume", self._volume))
                self._muted = bool(self._cfg_get(initial, "muted", self._muted))
        except Exception:
            pass

        self._volume = max(0, min(100, int(self._volume)))
        self._publish_audio_state()

        backoff_s = 1.0

        while not self._stop_event.is_set():
            try:
                endpoint = await self._discover_or_select_endpoint()
                if endpoint is None:
                    _LOGGER.warning("Sendspin: no server discovered/selected; retrying")
                    await asyncio.sleep(min(backoff_s, 10.0))
                    backoff_s = min(backoff_s * 1.5, 10.0)
                    continue

                backoff_s = 1.0
                await self._connect_and_run(endpoint)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                _LOGGER.warning("Sendspin: connection loop error: %s", e, exc_info=True)
                await asyncio.sleep(min(backoff_s, 10.0))
                backoff_s = min(backoff_s * 1.5, 10.0)

        await self.disconnect(reason="shutdown")

    # ---------------------------------------------------------------------
    # Discovery
    # ---------------------------------------------------------------------

    async def _discover_or_select_endpoint(self) -> Optional[SendspinEndpoint]:
        conn = self._cfg_get_section(self._cfg, "connection")
        if conn is None:
            return None

        host = self._cfg_get(conn, "server_host", None)
        port = self._cfg_get(conn, "server_port", None)
        path = self._cfg_get(conn, "server_path", "/sendspin") or "/sendspin"
        if host:
            try:
                port_i = int(port) if port is not None else 8927
            except Exception:
                port_i = 8927
            ep = SendspinEndpoint(host=str(host), port=port_i, path=str(path))
            self._last_endpoint = ep
            return ep

        use_mdns = bool(self._cfg_get(conn, "mdns", True))
        if not use_mdns:
            return self._last_endpoint

        eps = await self._discover_mdns(timeout_s=2.0)
        if not eps:
            return self._last_endpoint

        ep = eps[0]
        self._last_endpoint = ep
        _LOGGER.info("Sendspin: discovered %d server(s)", len(eps))
        for e in eps:
            _LOGGER.debug("Sendspin: candidate %s", e.ws_url)
        return ep

    async def _discover_mdns(self, *, timeout_s: float) -> list[SendspinEndpoint]:
        try:
            from zeroconf.asyncio import AsyncZeroconf, AsyncServiceBrowser, AsyncServiceInfo
            from zeroconf import ServiceStateChange
        except Exception as e:
            _LOGGER.warning("Sendspin: zeroconf not available: %s", e)
            return []

        found: Dict[str, SendspinEndpoint] = {}
        got_any = asyncio.Event()

        azc = AsyncZeroconf()
        zc = azc.zeroconf

        async def _resolve(name: str) -> None:
            try:
                info = AsyncServiceInfo(_SENDSPIN_SERVICE_TYPE, name)
                ok = await info.async_request(zc, timeout=1500)
                if not ok:
                    return

                addrs = info.parsed_addresses()
                if not addrs:
                    return

                props = info.properties or {}
                path_b = props.get(b"path") or props.get(b"ws_path")
                path_s = "/sendspin"
                if isinstance(path_b, (bytes, bytearray)):
                    try:
                        path_s = path_b.decode("utf-8").strip() or "/sendspin"
                    except Exception:
                        path_s = "/sendspin"

                ep = SendspinEndpoint(host=addrs[0], port=int(info.port), path=path_s)
                found[name] = ep
                got_any.set()

                _LOGGER.debug(
                    "Sendspin: discovered service %s -> %s:%s%s",
                    name,
                    ep.host,
                    ep.port,
                    ep.path,
                )
            except Exception:
                _LOGGER.debug("Sendspin: failed to resolve %s", name, exc_info=True)

        def _on_change(*args: Any, **kwargs: Any) -> None:
            name = args[2] if len(args) >= 4 else kwargs.get("name")
            state_change = args[3] if len(args) >= 4 else kwargs.get("state_change")
            if not name:
                return
            if state_change == ServiceStateChange.Added:
                self._loop.create_task(_resolve(str(name)))

        browser = AsyncServiceBrowser(zc, _SENDSPIN_SERVICE_TYPE, handlers=[_on_change])
        try:
            try:
                await asyncio.wait_for(got_any.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                pass
        finally:
            try:
                await browser.async_cancel()
            except Exception:
                pass
            try:
                await azc.async_close()
            except Exception:
                pass

        return [found[k] for k in sorted(found.keys())]

    # ---------------------------------------------------------------------
    # Websocket session
    # ---------------------------------------------------------------------

    async def _connect_and_run(self, endpoint: SendspinEndpoint) -> None:
        url = endpoint.ws_url
        self._current_endpoint_url = url

        # Reset per-session diagnostics
        self._last_rx_type = None
        self._last_rx_at = None
        self._last_bin_at = None

        _LOGGER.info("Sendspin: connecting to %s", url)

        conn = self._cfg_get_section(self._cfg, "connection") or {}
        conn_timeout = float(self._cfg_get(conn, "timeout_seconds", 6.0) or 6.0)
        ping_interval = float(self._cfg_get(conn, "ping_interval_seconds", 20.0) or 20.0)
        ping_timeout = float(self._cfg_get(conn, "ping_timeout_seconds", 20.0) or 20.0)

        self._disconnect_event.clear()
        self._sink_failed = False

        # Mark disconnected until handshake completes
        self._set_connection(connected=False, endpoint=url)

        async with websockets.connect(
            url,
            open_timeout=conn_timeout,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        ) as ws:
            self._ws = ws

            await self._send_client_hello()

            ok = await self._await_server_hello(
                timeout_s=float(self._cfg_get(conn, "hello_timeout_seconds", 8.0) or 8.0)
            )
            if not ok:
                _LOGGER.warning("Sendspin: handshake timed out waiting for server/hello; reconnecting")
                return

            # Connection state is now considered "connected" post server/hello
            self._set_connection(connected=True, endpoint=url)

            await self._send_initial_client_state()

            recv_task = self._loop.create_task(self._recv_loop(ws))
            time_task = self._loop.create_task(self._time_sync_loop(ws))
            hb_task = self._loop.create_task(self._heartbeat_loop(ws))

            stop_task = self._loop.create_task(self._stop_event.wait())
            disc_task = self._loop.create_task(self._disconnect_event.wait())

            done, pending = await asyncio.wait(
                {recv_task, time_task, hb_task, stop_task, disc_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, ConnectionClosed):
                    _LOGGER.debug("Sendspin: task ended with exception: %r", exc, exc_info=True)

            close_code = getattr(ws, "close_code", None)
            close_reason = getattr(ws, "close_reason", None)
            if close_code is not None:
                _LOGGER.info(
                    "Sendspin: websocket close observed (code=%s reason=%r last_rx_type=%s last_bin_age_s=%s)",
                    close_code,
                    close_reason,
                    self._last_rx_type,
                    f"{(time.monotonic() - self._last_bin_at):.3f}" if self._last_bin_at else None,
                )

        # Session ended
        self._ws = None
        self._server_id = None
        self._server_name = None
        self._active_roles = ()

        self._set_connection(connected=False, endpoint=url)
        self._set_playback("unknown")

        await self._stop_stream(reason="ws_closed")

    async def _send_json(self, ws: websockets.WebSocketClientProtocol, obj: dict) -> None:
        await ws.send(json.dumps(obj))

    # ---------------------------------------------------------------------
    # State helpers (volume/mute) + preference publish
    # ---------------------------------------------------------------------

    def _set_volume(self, new_volume: int, *, publish: bool = True) -> None:
        v = max(0, min(100, int(new_volume)))
        if v == self._volume:
            return
        self._volume = v

        if publish:
            try:
                self._event_bus.publish("sendspin_volume_changed", {"volume": v})
            except Exception:
                _LOGGER.debug("Sendspin: failed to publish sendspin_volume_changed", exc_info=True)

        self._publish_audio_state()

        if self._stream_active:
            self._loop.create_task(self._apply_mpv_audio_state())

    def _set_muted(self, muted: bool) -> None:
        m = bool(muted)
        if m == self._muted:
            return
        self._muted = m
        self._publish_audio_state()
        if self._stream_active:
            self._loop.create_task(self._apply_mpv_audio_state())

    def _effective_mpv_volume(self) -> int:
        if self._muted:
            return 0
        vol = int(self._volume)
        if self._ducked:
            vol = int(round(vol * (self._duck_percent / 100.0)))
        return max(0, min(100, vol))

    # ---------------------------------------------------------------------
    # mpv IPC
    # ---------------------------------------------------------------------

    @staticmethod
    def _sanitize_id(s: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "_", s)[:80]

    async def _mpv_ipc_send(self, payload: dict) -> None:
        path = self._mpv_ipc_path
        if not path:
            return
        data = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            _reader, writer = await asyncio.open_unix_connection(path)
            writer.write(data)
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception:
            _LOGGER.debug("Sendspin: mpv IPC send failed", exc_info=True)

    async def _wait_for_mpv_ipc(self, *, timeout_s: float = 1.5) -> bool:
        path = self._mpv_ipc_path
        if not path:
            return False
        deadline = time.monotonic() + max(0.2, timeout_s)
        while time.monotonic() < deadline and not self._stop_event.is_set():
            if os.path.exists(path):
                return True
            await asyncio.sleep(0.05)
        return os.path.exists(path)

    async def _apply_mpv_audio_state(self) -> None:
        async with self._mpv_ipc_lock:
            if not self._mpv_ipc_path or not self._stream_active:
                return

            for attempt in range(6):
                if not self._mpv_ipc_ready:
                    self._mpv_ipc_ready = await self._wait_for_mpv_ipc(timeout_s=0.5)

                if self._mpv_ipc_ready:
                    break

                if attempt == 5:
                    _LOGGER.debug("Sendspin: mpv IPC not ready after retries (%s)", self._mpv_ipc_path)
                    return

                await asyncio.sleep(0.1)

            eff_vol = self._effective_mpv_volume()
            await self._mpv_ipc_send({"command": ["set_property", "mute", bool(self._muted)]})
            await self._mpv_ipc_send({"command": ["set_property", "volume", int(eff_vol)]})

            _LOGGER.debug(
                "Sendspin: applied mpv audio state (user_vol=%s muted=%s ducked=%s duck%%=%s eff_vol=%s)",
                self._volume,
                self._muted,
                self._ducked,
                self._duck_percent,
                eff_vol,
            )

    # ---------------------------------------------------------------------
    # SPEC-CORRECT messages
    # ---------------------------------------------------------------------

    def _build_supported_roles(self) -> list[str]:
        roles_cfg = self._cfg_get_section(self._cfg, "roles")

        roles: list[str] = []
        if roles_cfg is None:
            roles = ["player@v1", "controller@v1", "metadata@v1"]
        else:
            if bool(self._cfg_get(roles_cfg, "player", True)):
                roles.append("player@v1")
            if bool(self._cfg_get(roles_cfg, "controller", True)):
                roles.append("controller@v1")
            if bool(self._cfg_get(roles_cfg, "metadata", True)):
                roles.append("metadata@v1")
            if bool(self._cfg_get(roles_cfg, "artwork", False)):
                roles.append("artwork@v1")
            if bool(self._cfg_get(roles_cfg, "visualizer", False)):
                roles.append("visualizer@v1")

        return roles

    def _build_player_support(self) -> Dict[str, Any]:
        player_cfg = self._cfg_get_section(self._cfg, "player")

        sample_rate = int(self._cfg_get(player_cfg, "sample_rate", 48000) or 48000) if player_cfg else 48000
        channels = int(self._cfg_get(player_cfg, "channels", 2) or 2) if player_cfg else 2
        bit_depth = int(self._cfg_get(player_cfg, "bit_depth", 16) or 16) if player_cfg else 16
        buffer_capacity = (
            int(self._cfg_get(player_cfg, "buffer_capacity_bytes", 1048576) or 1048576) if player_cfg else 1048576
        )

        preferred = str(self._cfg_get(player_cfg, "preferred_codec", "pcm") or "pcm").lower().strip() if player_cfg else "pcm"
        raw_supported = self._cfg_get(player_cfg, "supported_codecs", ["pcm"]) if player_cfg else ["pcm"]

        # Normalize codecs list (accept string or list)
        if isinstance(raw_supported, str):
            codecs_in = [raw_supported]
        elif isinstance(raw_supported, (list, tuple)):
            codecs_in = [str(x) for x in raw_supported if x is not None]
        else:
            codecs_in = []

        allowed = {"opus", "flac", "pcm"}
        codecs: list[str] = []
        for c in codecs_in:
            c2 = str(c).lower().strip()
            if not c2 or c2 not in allowed:
                continue
            if c2 not in codecs:
                codecs.append(c2)

        if "pcm" not in codecs:
            codecs.append("pcm")

        if preferred not in codecs:
            preferred = "pcm"

        ordered_codecs = [preferred] + [c for c in codecs if c != preferred]

        # Filter advertised codecs by local decoder availability (best-effort).
        available: list[str] = []
        for c in ordered_codecs:
            if c == "pcm":
                available.append(c)
            elif c == "opus" and self._opus_available:
                available.append(c)
            elif c == "flac" and self._ffmpeg_available:
                available.append(c)
        if "pcm" not in available:
            available.append("pcm")

        # If preferred isn't available locally, prefer the first available codec.
        if preferred not in available:
            preferred = available[0]

        ordered_codecs = [preferred] + [c for c in available if c != preferred]

        supported_formats = [
            {"codec": c, "channels": channels, "sample_rate": sample_rate, "bit_depth": bit_depth}
            for c in ordered_codecs
        ]

        return {
            "supported_formats": supported_formats,
            "buffer_capacity": buffer_capacity,
            "supported_commands": ["volume", "mute"],
        }

    async def _send_client_hello(self) -> None:
        assert self._ws is not None
        ws = self._ws

        supported_roles = self._build_supported_roles()

        payload: Dict[str, Any] = {
            "client_id": self._client_id,
            "name": self._client_name,
            "version": 1,
            "supported_roles": supported_roles,
        }

        if "player@v1" in supported_roles:
            payload["player@v1_support"] = self._build_player_support()

        await self._send_json(ws, {"type": "client/hello", "payload": payload})
        _LOGGER.info("Sendspin: client/hello sent (client_id=%s)", self._client_id)

    async def _await_server_hello(self, *, timeout_s: float) -> bool:
        assert self._ws is not None
        ws = self._ws

        deadline = time.monotonic() + max(0.5, timeout_s)

        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
            except asyncio.TimeoutError:
                return False

            if isinstance(msg, (bytes, bytearray)):
                continue

            try:
                data = json.loads(msg)
            except Exception:
                _LOGGER.debug("Sendspin: received non-JSON during handshake")
                continue

            mtype = data.get("type")
            payload = data.get("payload") or {}

            if mtype == "server/hello":
                self._server_id = payload.get("server_id")
                self._server_name = payload.get("name")
                roles = payload.get("active_roles") or []
                if isinstance(roles, list):
                    self._active_roles = tuple(str(r) for r in roles)

                _LOGGER.info(
                    "Sendspin: server/hello received (server=%s id=%s active_roles=%s)",
                    self._server_name,
                    self._server_id,
                    list(self._active_roles),
                )
                return True

            if mtype == "server/time":
                continue

            _LOGGER.debug("Sendspin: ignoring pre-hello message type=%s", mtype)

        return False

    async def _send_initial_client_state(self) -> None:
        if self._ws is None:
            return
        ws = self._ws

        payload: Dict[str, Any] = {"state": self._op_state}
        if "player@v1" in self._build_supported_roles():
            payload["player"] = {"volume": int(self._volume), "muted": bool(self._muted)}

        await self._send_json(ws, {"type": "client/state", "payload": payload})
        _LOGGER.debug("Sendspin: initial client/state sent")

    async def _send_player_state(self, ws: websockets.WebSocketClientProtocol) -> None:
        payload: Dict[str, Any] = {}
        if "player@v1" in self._build_supported_roles():
            payload["player"] = {"volume": int(self._volume), "muted": bool(self._muted)}
        payload["state"] = self._op_state

        await self._send_json(ws, {"type": "client/state", "payload": payload})
        _LOGGER.debug("Sendspin: client/state heartbeat sent")

    # ---------------------------------------------------------------------
    # Streaming (PCM -> mpv)
    # ---------------------------------------------------------------------

    async def _start_stream(self, *, codec: str, sample_rate: int, channels: int, bit_depth: int) -> None:
        _LOGGER.info(
            "Sendspin: stream/start (codec=%s rate=%s ch=%s depth=%s)",
            codec,
            sample_rate,
            channels,
            bit_depth,
        )

        self._set_stream(codec=str(codec), rate=int(sample_rate), ch=int(channels), depth=int(bit_depth))

        codec = str(codec or "pcm").lower().strip()

        if not self._codec_available(codec):
            # Phase 2 Milestone 4 decision: best-effort fallback to PCM.
            _LOGGER.warning("Sendspin: codec %r not available locally; requesting PCM fallback", codec)
            await self.request_pcm_fallback(
                reason=f"codec_unavailable:{codec}",
                sample_rate=sample_rate,
                channels=channels,
                bit_depth=bit_depth,
            )
            return

        # Stop any existing stream first.
        await self._stop_stream(reason="restart_stream")

        self._sink_failed = False
        self._pcm_frame_count = 0
        self._pcm_first_frame_at = None
        self._pcm_last_frame_at = None

        self._stream_active = True
        self._stream_codec = codec
        self._stream_rate = sample_rate
        self._stream_channels = channels
        self._stream_bit_depth = bit_depth

        fmt = "s16le" if bit_depth == 16 else None
        if fmt is None:
            _LOGGER.warning(
                "Sendspin: unsupported bit depth %s (only 16 supported); requesting PCM 16-bit fallback",
                bit_depth,
            )
            self._stream_active = False
            await self.request_pcm_fallback(
                reason=f"unsupported_bit_depth:{bit_depth}",
                sample_rate=sample_rate,
                channels=channels,
                bit_depth=16,
            )
            return

        player_cfg = self._cfg_get_section(self._cfg, "player")

        mpv_path = self._cfg_get(player_cfg, "mpv_path", "mpv") if player_cfg else "mpv"
        mpv_extra_args = self._cfg_get(player_cfg, "mpv_extra_args", []) if player_cfg else []
        if isinstance(mpv_extra_args, (str, bytes, bytearray)):
            mpv_extra_args_list = [str(mpv_extra_args)]
        elif isinstance(mpv_extra_args, (list, tuple)):
            mpv_extra_args_list = [str(x) for x in mpv_extra_args if x is not None]
        else:
            mpv_extra_args_list = []

        async def _spawn_mpv_with_prune(cmd_tokens: list[str]) -> Optional[asyncio.subprocess.Process]:
            """Start mpv, pruning any unrecognized flags from the command.

            This is best-effort: if mpv errors on startup due to an unknown option,
            we remove the offending token and retry a few times.
            """
            tokens = list(cmd_tokens)
            for _ in range(6):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *tokens,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except FileNotFoundError:
                    _LOGGER.error("Sendspin: mpv executable not found: %r", tokens[0])
                    return None
                except Exception:
                    _LOGGER.exception("Sendspin: failed to start mpv")
                    return None

                # Give mpv a moment to fail-fast on bad options.
                await asyncio.sleep(0.15)
                if proc.returncode is None:
                    return proc

                # mpv exited immediately. Try to identify a bad flag and retry.
                stderr_bytes = b""
                try:
                    if proc.stderr is not None:
                        stderr_bytes = await proc.stderr.read()  # type: ignore[func-returns-value]
                except Exception:
                    pass

                stderr_s = stderr_bytes.decode("utf-8", errors="replace")
                m = re.search(r"(?:Error parsing option|unknown option|Unrecognized option)\s+'?\s*(--[^\s'\"\)]+)", stderr_s, re.IGNORECASE)
                bad = m.group(1) if m else None

                if bad:
                    removed = False
                    new_tokens: list[str] = []
                    for t in tokens:
                        if t == bad or t.startswith(bad + "="):
                            removed = True
                            continue
                        new_tokens.append(t)
                    if removed:
                        _LOGGER.warning("Sendspin: mpv rejected flag %r; ignoring it and retrying", bad)
                        tokens = new_tokens
                        continue

                # Can't identify the bad option; stop retrying.
                _LOGGER.error("Sendspin: mpv failed to start (rc=%s). stderr=%r", proc.returncode, stderr_s[-400:])
                return None

            _LOGGER.error("Sendspin: mpv failed to start after pruning attempts")
            return None

        mpv_ao = self._cfg_get(player_cfg, "mpv_ao", None) if player_cfg else None
        mpv_audio_device = self._cfg_get(player_cfg, "mpv_audio_device", None) if player_cfg else None

        sid = self._sanitize_id(self._client_id)
        self._mpv_ipc_path = f"/tmp/lva_sendspin_mpv_{sid}.sock"
        self._mpv_ipc_ready = False
        try:
            if os.path.exists(self._mpv_ipc_path):
                os.remove(self._mpv_ipc_path)
        except Exception:
            pass

        cmd = [
            str(mpv_path),
            "--no-video",
            "--really-quiet",
            "--profile=low-latency",
            "--cache=no",
            f"--input-ipc-server={self._mpv_ipc_path}",
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-rate={sample_rate}",
            f"--demuxer-rawaudio-channels={channels}",
            f"--demuxer-rawaudio-format={fmt}",
        ]

        # Optional device/backend selectors
        if mpv_ao:
            cmd.append(f"--ao={mpv_ao}")
        if mpv_audio_device:
            cmd.append(f"--audio-device={mpv_audio_device}")

        # User-provided pass-through args (best-effort; bad ones are pruned at runtime)
        cmd.extend(mpv_extra_args_list)

        cmd.append("-")

        _LOGGER.info(
            "Sendspin: starting PCM sink via mpv (rate=%s ch=%s depth=%s ao=%s dev=%s ipc=%s)",
            sample_rate,
            channels,
            bit_depth,
            mpv_ao or "auto",
            mpv_audio_device or "auto",
            self._mpv_ipc_path,
        )

        self._mpv_stderr_tail.clear()

        self._pcm_proc = await _spawn_mpv_with_prune(cmd)
        if self._pcm_proc is None:
            self._stream_active = False
            self._sink_failed = True
            self._disconnect_event.set()
            return

        self._pcm_queue = asyncio.Queue(maxsize=200)
        self._pcm_writer_task = self._loop.create_task(self._pcm_writer_loop())
        self._pcm_stderr_task = self._loop.create_task(self._pcm_stderr_loop())
        self._pcm_wait_task = self._loop.create_task(self._pcm_waiter_loop())


        # Initialize decoder for negotiated codec (if needed).
        if codec == "opus":
            if not self._init_opus_decoder(sample_rate=sample_rate, channels=channels):
                self._stream_active = False
                await self._stop_stream(reason="opus_decoder_failed")
                await self.request_pcm_fallback(
                    reason="opus_decoder_failed",
                    sample_rate=sample_rate,
                    channels=channels,
                    bit_depth=bit_depth,
                )
                return
        else:
            self._opus_decoder = None
            self._opus_max_frame_size = 0

        if codec == "flac":
            ok = await self._start_flac_decoder(sample_rate=sample_rate, channels=channels)
            if not ok:
                self._stream_active = False
                await self._stop_stream(reason="flac_decoder_failed")
                await self.request_pcm_fallback(
                    reason="flac_decoder_failed",
                    sample_rate=sample_rate,
                    channels=channels,
                    bit_depth=bit_depth,
                )
                return
        else:
            await self._stop_decoder(reason="codec_not_flac")

        self._loop.create_task(self._apply_mpv_audio_state())

    async def _stop_stream(self, *, reason: str) -> None:
        if self._stream_active or self._pcm_proc is not None:
            _LOGGER.info("Sendspin: stopping stream (%s)", reason)

        self._stream_active = False
        self._set_stream(codec=None, rate=None, ch=None, depth=None)

        # Stop any active decoder (ffmpeg) and clear codec-specific state.
        self._opus_decoder = None
        self._opus_max_frame_size = 0
        await self._stop_decoder(reason=reason)

        if self._pcm_writer_task is not None:
            self._pcm_writer_task.cancel()
            await asyncio.gather(self._pcm_writer_task, return_exceptions=True)
            self._pcm_writer_task = None

        if self._pcm_stderr_task is not None:
            self._pcm_stderr_task.cancel()
            await asyncio.gather(self._pcm_stderr_task, return_exceptions=True)
            self._pcm_stderr_task = None

        if self._pcm_wait_task is not None:
            self._pcm_wait_task.cancel()
            await asyncio.gather(self._pcm_wait_task, return_exceptions=True)
            self._pcm_wait_task = None

        proc = self._pcm_proc
        self._pcm_proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except Exception:
                _LOGGER.debug("Sendspin: error stopping PCM sink", exc_info=True)

        try:
            if self._mpv_ipc_path and os.path.exists(self._mpv_ipc_path):
                os.remove(self._mpv_ipc_path)
        except Exception:
            pass
        self._mpv_ipc_path = None
        self._mpv_ipc_ready = False

        if self._pcm_frame_count:
            first = self._pcm_first_frame_at
            last = self._pcm_last_frame_at
            dur = (last - first) if (first is not None and last is not None) else None
            _LOGGER.info(
                "Sendspin: stream stats (frames=%s first=%s last=%s duration_s=%s sink_failed=%s)",
                self._pcm_frame_count,
                f"{first:.3f}" if first else None,
                f"{last:.3f}" if last else None,
                f"{dur:.3f}" if dur is not None else None,
                self._sink_failed,
            )

    async def _pcm_waiter_loop(self) -> None:
        proc = self._pcm_proc
        if proc is None:
            return
        try:
            rc = await proc.wait()
            if self._stream_active and not self._stop_event.is_set():
                self._sink_failed = True
                _LOGGER.warning(
                    "Sendspin: mpv exited unexpectedly (returncode=%s). Requesting reconnect. stderr_tail=%r",
                    rc,
                    list(self._mpv_stderr_tail),
                )
                self._disconnect_event.set()
            else:
                _LOGGER.debug("Sendspin: mpv exited (returncode=%s) during normal stop", rc)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Sendspin: mpv waiter loop error", exc_info=True)

    async def _pcm_writer_loop(self) -> None:
        proc = self._pcm_proc
        if proc is None or proc.stdin is None:
            return

        unexpected = False

        try:
            while self._stream_active and not self._stop_event.is_set():
                chunk = await self._pcm_queue.get()
                if not chunk:
                    continue
                try:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    unexpected = True
                    self._sink_failed = True
                    _LOGGER.warning("Sendspin: PCM sink pipe closed unexpectedly (broken pipe)")
                    break
                except Exception:
                    unexpected = True
                    self._sink_failed = True
                    _LOGGER.debug("Sendspin: PCM sink write error", exc_info=True)
                    break

        except asyncio.CancelledError:
            raise
        finally:
            if unexpected and not self._stop_event.is_set():
                _LOGGER.warning(
                    "Sendspin: requesting reconnect due to PCM sink failure (stderr_tail=%r)",
                    list(self._mpv_stderr_tail),
                )
                self._disconnect_event.set()

    async def _pcm_stderr_loop(self) -> None:
        proc = self._pcm_proc
        if proc is None or proc.stderr is None:
            return
        try:
            while not self._stop_event.is_set():
                line = await proc.stderr.readline()
                if not line:
                    return
                s = line.decode("utf-8", errors="replace").rstrip()
                self._mpv_stderr_tail.append(s)
                _LOGGER.debug("Sendspin mpv: %s", s)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Sendspin: stderr loop error", exc_info=True)

    def _codec_available(self, codec: str) -> bool:
        c = str(codec or "").lower().strip()
        if c == "pcm":
            return True
        if c == "opus":
            return bool(self._opus_available)
        if c == "flac":
            return bool(self._ffmpeg_available)
        return False

    def _init_opus_decoder(self, *, sample_rate: int, channels: int) -> bool:
        if not self._opus_available:
            return False
        try:
            import opuslib  # type: ignore
            self._opus_decoder = opuslib.Decoder(int(sample_rate), int(channels))
            # Opus packets can represent up to 120ms of audio.
            self._opus_max_frame_size = max(960, int(int(sample_rate) * 0.12))
            return True
        except Exception:
            _LOGGER.warning("Sendspin: failed to initialize opus decoder; falling back to PCM", exc_info=True)
            self._opus_decoder = None
            self._opus_max_frame_size = 0
            return False

    def _decode_opus_packet(self, packet: bytes) -> Optional[bytes]:
        if not packet:
            return b""
        dec = self._opus_decoder
        if dec is None or self._opus_max_frame_size <= 0:
            return None
        try:
            return dec.decode(packet, self._opus_max_frame_size, decode_fec=False)  # type: ignore[attr-defined]
        except Exception:
            return None

    async def _start_flac_decoder(self, *, sample_rate: int, channels: int) -> bool:
        # Start ffmpeg to decode FLAC bitstream from stdin -> raw PCM on stdout
        if not self._ffmpeg_available:
            _LOGGER.warning("Sendspin: ffmpeg not available; cannot decode FLAC")
            return False

        await self._stop_decoder(reason="restart_decoder")

        player_cfg = self._cfg_get_section(self._cfg, "player")
        ffmpeg_path = str(self._cfg_get(player_cfg, "ffmpeg_path", self._ffmpeg_path) or self._ffmpeg_path) if player_cfg else self._ffmpeg_path
        extra = self._cfg_get(player_cfg, "ffmpeg_extra_args", []) if player_cfg else []
        if isinstance(extra, (str, bytes, bytearray)):
            extra_args = [str(extra)]
        elif isinstance(extra, (list, tuple)):
            extra_args = [str(x) for x in extra if x is not None]
        else:
            extra_args = []

        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "flac",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            str(int(channels)),
            "-ar",
            str(int(sample_rate)),
            *extra_args,
            "pipe:1",
        ]

        try:
            self._decoder_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            _LOGGER.error("Sendspin: ffmpeg executable not found: %r", ffmpeg_path)
            self._decoder_proc = None
            return False
        except Exception:
            _LOGGER.exception("Sendspin: failed to start ffmpeg decoder")
            self._decoder_proc = None
            return False

        # Reset queue and start loops
        self._encoded_queue = asyncio.Queue(maxsize=200)
        self._decoder_writer_task = self._loop.create_task(self._decoder_writer_loop())
        self._decoder_reader_task = self._loop.create_task(self._decoder_reader_loop())
        self._decoder_stderr_task = self._loop.create_task(self._decoder_stderr_loop())
        _LOGGER.info("Sendspin: FLAC decoder started via ffmpeg (rate=%s ch=%s)", sample_rate, channels)
        return True

    async def _stop_decoder(self, *, reason: str) -> None:
        if self._decoder_writer_task is not None:
            self._decoder_writer_task.cancel()
            await asyncio.gather(self._decoder_writer_task, return_exceptions=True)
            self._decoder_writer_task = None
        if self._decoder_reader_task is not None:
            self._decoder_reader_task.cancel()
            await asyncio.gather(self._decoder_reader_task, return_exceptions=True)
            self._decoder_reader_task = None
        if self._decoder_stderr_task is not None:
            self._decoder_stderr_task.cancel()
            await asyncio.gather(self._decoder_stderr_task, return_exceptions=True)
            self._decoder_stderr_task = None

        proc = self._decoder_proc
        self._decoder_proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            except Exception:
                _LOGGER.debug("Sendspin: error stopping decoder (%s)", reason, exc_info=True)

    async def _decoder_writer_loop(self) -> None:
        proc = self._decoder_proc
        if proc is None or proc.stdin is None:
            return

        unexpected = False

        try:
            while self._stream_active and not self._stop_event.is_set():
                chunk = await self._encoded_queue.get()
                if not chunk:
                    continue
                try:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    unexpected = True
                    self._sink_failed = True
                    _LOGGER.warning("Sendspin: decoder stdin pipe closed unexpectedly (broken pipe)")
                    break
                except Exception:
                    unexpected = True
                    self._sink_failed = True
                    _LOGGER.debug("Sendspin: decoder stdin write error", exc_info=True)
                    break

        except asyncio.CancelledError:
            raise
        finally:
            if unexpected and not self._stop_event.is_set():
                _LOGGER.warning("Sendspin: requesting reconnect due to decoder failure")
                self._disconnect_event.set()

    async def _decoder_reader_loop(self) -> None:
        proc = self._decoder_proc
        if proc is None or proc.stdout is None:
            return
        try:
            while self._stream_active and not self._stop_event.is_set():
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    return
                try:
                    self._pcm_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    _LOGGER.debug("Sendspin: PCM queue full; dropping decoded audio (%d bytes)", len(chunk))
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Sendspin: decoder stdout read error", exc_info=True)
        finally:
            _LOGGER.debug("Sendspin: decoder reader loop ending")

    async def _decoder_stderr_loop(self) -> None:
        proc = self._decoder_proc
        if proc is None or proc.stderr is None:
            return
        try:
            while not self._stop_event.is_set():
                line = await proc.stderr.readline()
                if not line:
                    return
                s = line.decode("utf-8", errors="replace").rstrip()
                _LOGGER.debug("Sendspin ffmpeg: %s", s)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Sendspin: decoder stderr loop error", exc_info=True)

    def _extract_pcm_payload(self, frame: bytes) -> bytes:
        if len(frame) <= _BINARY_HEADER_LEN:
            return b""
        return frame[_BINARY_HEADER_LEN:]

    # ---------------------------------------------------------------------
    # Loops
    # ---------------------------------------------------------------------

    async def _recv_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        try:
            while not self._stop_event.is_set():
                msg = await ws.recv()

                if isinstance(msg, (bytes, bytearray)):
                    self._last_bin_at = time.monotonic()

                    if not self._stream_active:
                        self._pcm_frame_count += 1
                        if self._pcm_frame_count == 1 or (self._pcm_frame_count % 500) == 0:
                            _LOGGER.debug("Sendspin: received binary frame while stream inactive (%d bytes)", len(msg))
                        continue

                    payload_bytes = self._extract_pcm_payload(bytes(msg))
                    if not payload_bytes:
                        continue

                    # Decode / route based on negotiated codec
                    if self._stream_codec == "pcm":
                        pcm = payload_bytes
                    elif self._stream_codec == "opus":
                        pcm = self._decode_opus_packet(payload_bytes)
                        if pcm is None:
                            _LOGGER.warning("Sendspin: opus decode failed; requesting PCM fallback")
                            await self.request_pcm_fallback(
                                reason="opus_decode_failed",
                                sample_rate=self._stream_rate,
                                channels=self._stream_channels,
                                bit_depth=self._stream_bit_depth,
                            )
                            await self._stop_stream(reason="opus_decode_failed")
                            continue
                    elif self._stream_codec == "flac":
                        if self._decoder_proc is None:
                            _LOGGER.warning("Sendspin: FLAC decoder not running; requesting PCM fallback")
                            await self.request_pcm_fallback(
                                reason="flac_decoder_missing",
                                sample_rate=self._stream_rate,
                                channels=self._stream_channels,
                                bit_depth=self._stream_bit_depth,
                            )
                            await self._stop_stream(reason="flac_decoder_missing")
                            continue
                        try:
                            self._encoded_queue.put_nowait(payload_bytes)
                        except asyncio.QueueFull:
                            _LOGGER.debug("Sendspin: encoded queue full; dropping FLAC frame (%d bytes)", len(payload_bytes))
                        continue
                    else:
                        continue

                    if not pcm:
                        continue

                    now = time.monotonic()
                    self._pcm_frame_count += 1
                    self._pcm_last_frame_at = now
                    if self._pcm_first_frame_at is None:
                        self._pcm_first_frame_at = now
                        _LOGGER.info("Sendspin: first PCM frame received (%d bytes)", len(pcm))

                    if (self._pcm_frame_count % 500) == 0:
                        _LOGGER.debug(
                            "Sendspin: PCM frames received=%d (last_chunk=%d bytes)",
                            self._pcm_frame_count,
                            len(pcm),
                        )

                    try:
                        self._pcm_queue.put_nowait(pcm)
                    except asyncio.QueueFull:
                        _LOGGER.debug("Sendspin: PCM queue full; dropping frame (%d bytes)", len(pcm))
                    continue

                try:
                    data = json.loads(msg)
                except Exception:
                    _LOGGER.debug("Sendspin: received non-JSON message")
                    continue

                mtype = data.get("type")
                payload = data.get("payload") or {}

                self._last_rx_type = str(mtype) if mtype else None
                self._last_rx_at = time.monotonic()

                if mtype == "server/time":
                    continue

                if mtype == "server/state":
                    # player: per-device state updates (volume/mute)
                    player = payload.get("player") or {}
                    if isinstance(player, dict):
                        if "volume" in player:
                            try:
                                self._set_volume(int(player.get("volume")), publish=True)
                            except Exception:
                                pass
                        if "muted" in player:
                            try:
                                self._set_muted(bool(player.get("muted")))
                            except Exception:
                                pass

                    # controller: group-level state (supported_commands + volume/mute)
                    controller = payload.get("controller") or {}
                    if isinstance(controller, dict):
                        sc = controller.get("supported_commands")
                        if isinstance(sc, list):
                            try:
                                self._controller_supported_commands = {str(x) for x in sc}
                            except Exception:
                                self._controller_supported_commands = set()

                        if "volume" in controller:
                            try:
                                # Mirror group volume to local player volume.
                                self._set_volume(int(controller.get("volume")), publish=True)
                            except Exception:
                                pass
                        if "muted" in controller:
                            try:
                                self._set_muted(bool(controller.get("muted")))
                            except Exception:
                                pass

                    # metadata can be delivered via server/state per spec
                    meta = payload.get("metadata")
                    if isinstance(meta, dict):
                        self._state.metadata = copy.deepcopy(meta)
                        self._emit_metadata(meta)

                elif mtype == "server/command":
                    player = payload.get("player") or {}
                    if isinstance(player, dict):
                        cmd = player.get("command")

                        if cmd == "volume" and "volume" in player:
                            try:
                                self._set_volume(int(player.get("volume")), publish=True)
                            except Exception:
                                pass

                        elif cmd == "mute":
                            if "muted" in player:
                                try:
                                    self._set_muted(bool(player.get("muted")))
                                except Exception:
                                    pass
                            elif "mute" in player:
                                try:
                                    self._set_muted(bool(player.get("mute")))
                                except Exception:
                                    pass
                            else:
                                _LOGGER.debug("Sendspin: mute command missing 'muted'/'mute' key (keys=%s)", sorted(player.keys()))

                        await self._send_player_state(ws)

                elif mtype == "group/update":
                    if isinstance(payload, dict) and "playback_state" in payload:
                        try:
                            self._set_playback(str(payload.get("playback_state") or "unknown"))
                        except Exception:
                            pass

                elif mtype == "stream/start":
                    p = payload.get("player") or {}
                    codec = str(p.get("codec", "pcm"))
                    rate = int(p.get("sample_rate", 48000) or 48000)
                    ch = int(p.get("channels", 2) or 2)
                    depth = int(p.get("bit_depth", 16) or 16)

                    self._set_playback("playing")
                    await self._start_stream(codec=codec, sample_rate=rate, channels=ch, bit_depth=depth)

                elif mtype == "stream/stop":
                    _LOGGER.info("Sendspin: recv stream/stop")
                    await self._stop_stream(reason="stream_stop")
                    if self._state.playback.playback_state == "playing":
                        self._set_playback("paused")

                elif mtype == "stream/end":
                    _LOGGER.info("Sendspin: recv stream/end")
                    await self._stop_stream(reason="stream_end")
                    if self._state.playback.playback_state == "playing":
                        self._set_playback("paused")

                elif mtype in ("metadata/update", "metadata", "server/metadata", "media/metadata"):
                    if isinstance(payload, dict):
                        self._state.metadata = copy.deepcopy(payload)
                        self._emit_metadata(payload)
                        _LOGGER.info("Sendspin: metadata updated (keys=%s)", sorted(payload.keys()))
                    else:
                        _LOGGER.debug("Sendspin: metadata message had non-dict payload")

                else:
                    _LOGGER.debug(
                        "Sendspin: recv type=%s (payload_keys=%s)",
                        mtype,
                        sorted(payload.keys()) if isinstance(payload, dict) else None,
                    )

        except ConnectionClosed as e:
            code = getattr(e, "code", None)
            reason = getattr(e, "reason", None)
            _LOGGER.info(
                "Sendspin: websocket closed (code=%s reason=%r last_rx_type=%s)",
                code,
                reason,
                self._last_rx_type,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Sendspin: recv loop error")
        finally:
            _LOGGER.debug("Sendspin: recv loop ending")
            self._disconnect_event.set()
            await self._stop_stream(reason="recv_loop_end")

    async def _time_sync_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        conn = self._cfg_get_section(self._cfg, "connection") or {}
        interval_s = float(self._cfg_get(conn, "time_sync_interval_seconds", 5.0) or 5.0)

        try:
            while not self._stop_event.is_set():
                t_us = int(time.monotonic() * 1_000_000)
                await self._send_json(ws, {"type": "client/time", "payload": {"client_transmitted": t_us}})
                await asyncio.sleep(interval_s)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Sendspin: time sync loop error", exc_info=True)
        finally:
            _LOGGER.debug("Sendspin: time sync loop ending")

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        conn = self._cfg_get_section(self._cfg, "connection") or {}
        interval_s = float(self._cfg_get(conn, "state_heartbeat_seconds", 5.0) or 5.0)

        try:
            await asyncio.sleep(0.25)
            while not self._stop_event.is_set():
                await self._send_player_state(ws)
                await asyncio.sleep(interval_s)
        except ConnectionClosed:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.debug("Sendspin: heartbeat loop error", exc_info=True)
        finally:
            _LOGGER.debug("Sendspin: heartbeat loop ending")
