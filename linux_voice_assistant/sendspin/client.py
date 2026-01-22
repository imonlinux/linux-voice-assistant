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

Milestone 3:
- Handle stream/clear (clear buffered audio immediately without necessarily ending stream)
- Tighten teardown rules (no orphan mpv, queue drained on stop, disconnect always stops stream)
- Publish playback transitions (playing->stopped) on stop/end/disconnect
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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

        # Operational state per Sendspin spec: synchronized|error|external_source
        self._op_state: str = "synchronized"

        # Server identity (from server/hello)
        self._server_id: Optional[str] = None
        self._server_name: Optional[str] = None
        self._active_roles: Tuple[str, ...] = ()

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
            player_cfg = self._cfg_get_section(self._cfg, "player")
            if player_cfg is not None:
                self._duck_percent = int(
                    self._cfg_get(player_cfg, "duck_volume_percent", self._duck_percent) or self._duck_percent
                )
        except Exception:
            pass
        self._duck_percent = max(0, min(100, int(self._duck_percent)))

        # Pull coordination settings from config
        self._refresh_coordination_settings(log=True)

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
        payload = {"metadata": metadata}
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
        # Per M3: disconnect always stops stream + publishes stopped.
        await self._stop_stream(reason=reason, update_playback=True)

        ws = self._ws
        self._ws = None
        try:
            if ws is not None:
                await ws.close(code=1000, reason=reason)
        except Exception:
            _LOGGER.debug("Sendspin: ws close failed", exc_info=True)

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

            # Helpful diagnostic: what ended the session?
            for t in done:
                if t is stop_task:
                    _LOGGER.info("Sendspin: session ending (stop requested)")
                elif t is disc_task:
                    _LOGGER.warning(
                        "Sendspin: session ending (disconnect requested; sink_failed=%s stream_active=%s last_rx_type=%s last_rx_age_s=%s)",
                        self._sink_failed,
                        self._stream_active,
                        self._last_rx_type,
                        f"{(time.monotonic() - self._last_rx_at):.3f}" if self._last_rx_at else None,
                    )
                elif t is recv_task:
                    _LOGGER.info("Sendspin: session ending (recv loop ended)")
                elif t is time_task:
                    _LOGGER.info("Sendspin: session ending (time sync ended)")
                elif t is hb_task:
                    _LOGGER.info("Sendspin: session ending (heartbeat ended)")

            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, ConnectionClosed):
                    _LOGGER.debug("Sendspin: task ended with exception: %r", exc, exc_info=True)

            # If the server closed us, websockets keeps these attributes
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

        # Per M3: session end implies not playing.
        self._set_playback("stopped")
        await self._stop_stream(reason="ws_closed", update_playback=False)

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
        buffer_capacity = int(self._cfg_get(player_cfg, "buffer_capacity_bytes", 1048576) or 1048576) if player_cfg else 1048576

        return {
            "supported_formats": [
                {"codec": "pcm", "channels": channels, "sample_rate": sample_rate, "bit_depth": bit_depth}
            ],
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
                _LOGGER.debug("Sendspin: received non-JSON during handshake: %r", msg)
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

            _LOGGER.debug("Sendspin: ignoring pre-hello message type=%s payload=%s", mtype, payload)

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

    def _drain_pcm_queue(self) -> int:
        """Drain queued audio immediately (used by stream/clear and stop)."""
        drained = 0
        try:
            while True:
                _ = self._pcm_queue.get_nowait()
                drained += 1
        except asyncio.QueueEmpty:
            pass
        return drained

    async def _handle_stream_clear(self, payload: dict) -> None:
        """
        Sendspin spec: stream/clear clears buffers without ending stream (used for seek).
        We treat our local asyncio queue as the buffer and drain it immediately.
        """
        roles = payload.get("roles")
        if roles is None:
            clear_player = True
        else:
            clear_player = False
            try:
                if isinstance(roles, list):
                    clear_player = ("player" in roles) or ("player@v1" in roles)
            except Exception:
                clear_player = True

        if not clear_player:
            _LOGGER.info("Sendspin: stream/clear received (roles=%r) - nothing to clear for player", roles)
            return

        drained = self._drain_pcm_queue()
        # Reset diagnostics so post-clear flow is obvious in logs.
        self._pcm_frame_count = 0
        self._pcm_first_frame_at = None
        self._pcm_last_frame_at = None

        _LOGGER.info("Sendspin: stream/clear -> drained %d queued chunk(s)", drained)

    async def _start_stream(self, *, codec: str, sample_rate: int, channels: int, bit_depth: int) -> None:
        _LOGGER.info(
            "Sendspin: stream/start (codec=%s rate=%s ch=%s depth=%s)",
            codec,
            sample_rate,
            channels,
            bit_depth,
        )

        # Update publishable stream state immediately
        self._set_stream(codec=str(codec), rate=int(sample_rate), ch=int(channels), depth=int(bit_depth))

        if codec != "pcm":
            _LOGGER.warning("Sendspin: unsupported codec '%s' (only pcm supported)", codec)
            return

        await self._stop_stream(reason="restart_stream", update_playback=False)

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
            _LOGGER.warning("Sendspin: unsupported bit depth %s (only 16 supported right now)", bit_depth)
            self._stream_active = False
            return

        player_cfg = self._cfg_get_section(self._cfg, "player")
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
            "mpv",
            "--no-video",
            "--really-quiet",
            "--profile=low-latency",
            "--cache=no",
            f"--input-ipc-server={self._mpv_ipc_path}",
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-rate={sample_rate}",
            f"--demuxer-rawaudio-channels={channels}",
            f"--demuxer-rawaudio-format={fmt}",
            "-",
        ]
        if mpv_ao:
            cmd.insert(1, f"--ao={mpv_ao}")
        if mpv_audio_device:
            cmd.insert(1, f"--audio-device={mpv_audio_device}")

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

        self._pcm_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        self._pcm_queue = asyncio.Queue(maxsize=200)
        self._pcm_writer_task = self._loop.create_task(self._pcm_writer_loop())
        self._pcm_stderr_task = self._loop.create_task(self._pcm_stderr_loop())
        self._pcm_wait_task = self._loop.create_task(self._pcm_waiter_loop())

        self._loop.create_task(self._apply_mpv_audio_state())

    async def _stop_stream(self, *, reason: str, update_playback: bool = False) -> None:
        if self._stream_active or self._pcm_proc is not None:
            _LOGGER.info("Sendspin: stopping stream (%s)", reason)

        # Stop accepting new frames first.
        self._stream_active = False

        # Drain queued audio to satisfy M3 teardown rules.
        drained = self._drain_pcm_queue()
        if drained:
            _LOGGER.debug("Sendspin: drained %d queued chunk(s) on stop", drained)

        # When stream stops, clear stream format fields.
        self._set_stream(codec=None, rate=None, ch=None, depth=None)

        if update_playback:
            self._set_playback("stopped")

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
        """
        Watches mpv for unexpected exit.
        This is critical for diagnosing "silent" disconnects during pause/next.
        """
        proc = self._pcm_proc
        if proc is None:
            return
        try:
            rc = await proc.wait()
            # If we're still "stream_active", mpv exiting is unexpected.
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
            # Normal during stream stop/pause/track-change.
            raise
        finally:
            # IMPORTANT:
            # Do NOT tear down the websocket for a normal stream stop.
            # Only request disconnect when the PCM sink failed unexpectedly.
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
                        # Server might still send a little audio during transitions; log once in a while.
                        self._pcm_frame_count += 1
                        if self._pcm_frame_count == 1 or (self._pcm_frame_count % 500) == 0:
                            _LOGGER.debug("Sendspin: received binary frame while stream inactive (%d bytes)", len(msg))
                        continue

                    pcm = self._extract_pcm_payload(bytes(msg))
                    if not pcm:
                        continue

                    now = time.monotonic()
                    self._pcm_frame_count += 1
                    self._pcm_last_frame_at = now
                    if self._pcm_first_frame_at is None:
                        self._pcm_first_frame_at = now
                        _LOGGER.info("Sendspin: first PCM frame received (%d bytes)", len(pcm))

                    # Low-noise periodic debug
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
                    _LOGGER.debug("Sendspin: received non-JSON message: %r", msg)
                    continue

                mtype = data.get("type")
                payload = data.get("payload") or {}

                # Track last received message type/timestamp for session post-mortem
                self._last_rx_type = str(mtype) if mtype else None
                self._last_rx_at = time.monotonic()

                if mtype == "server/time":
                    continue

                if mtype == "server/state":
                    _LOGGER.debug("Sendspin: server/state %s", payload)
                    player = payload.get("player") or payload.get("controller") or {}
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

                    # Metadata sometimes arrives via server/state on MA
                    meta = payload.get("metadata")
                    if isinstance(meta, dict):
                        self._state.metadata = meta
                        self._emit_metadata(meta)

                elif mtype == "server/command":
                    _LOGGER.debug("Sendspin: server/command %s", payload)
                    player = payload.get("player") or {}
                    if isinstance(player, dict):
                        cmd = player.get("command")

                        if cmd == "volume" and "volume" in player:
                            try:
                                self._set_volume(int(player.get("volume")), publish=True)
                            except Exception:
                                pass

                        elif cmd == "mute":
                            # MA may send either {"muted": true} or {"mute": true}
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
                                _LOGGER.debug("Sendspin: mute command missing 'muted'/'mute' key: %s", player)

                        await self._send_player_state(ws)

                elif mtype == "group/update":
                    # Example: {'playback_state': 'stopped', 'group_id': '...'}
                    _LOGGER.debug("Sendspin: group/update %s", payload)
                    if isinstance(payload, dict) and "playback_state" in payload:
                        try:
                            self._set_playback(str(payload.get("playback_state") or "unknown"))
                        except Exception:
                            pass

                elif mtype == "stream/start":
                    _LOGGER.debug("Sendspin: recv type=stream/start payload=%s", payload)
                    p = payload.get("player") or {}
                    codec = str(p.get("codec", "pcm"))
                    rate = int(p.get("sample_rate", 48000) or 48000)
                    ch = int(p.get("channels", 2) or 2)
                    depth = int(p.get("bit_depth", 16) or 16)

                    # Stream start generally implies "playing"
                    self._set_playback("playing")

                    await self._start_stream(codec=codec, sample_rate=rate, channels=ch, bit_depth=depth)

                elif mtype == "stream/clear":
                    # Spec: clear buffers without ending stream (seek).
                    _LOGGER.info("Sendspin: recv stream/clear")
                    if isinstance(payload, dict):
                        await self._handle_stream_clear(payload)
                    else:
                        await self._handle_stream_clear({})

                elif mtype == "stream/stop":
                    _LOGGER.info("Sendspin: recv stream/stop")
                    await self._stop_stream(reason="stream_stop", update_playback=True)

                elif mtype == "stream/end":
                    _LOGGER.info("Sendspin: recv stream/end")
                    await self._stop_stream(reason="stream_end", update_playback=True)

                # Some servers use metadata events; preserve structure without normalizing.
                elif mtype in ("metadata/update", "metadata", "server/metadata", "media/metadata"):
                    if isinstance(payload, dict):
                        self._state.metadata = payload
                        self._emit_metadata(payload)
                        _LOGGER.info("Sendspin: metadata updated (keys=%s)", sorted(payload.keys()))
                    else:
                        _LOGGER.debug("Sendspin: metadata message had non-dict payload: %r", payload)

                else:
                    _LOGGER.debug("Sendspin: recv type=%s payload=%s", mtype, payload)

        except ConnectionClosed as e:
            # This should always be visible when the server closes us.
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
            await self._stop_stream(reason="recv_loop_end", update_playback=True)

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
