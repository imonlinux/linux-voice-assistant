#!/usr/bin/env python3
"""Sendspin client (LVA -> Music Assistant).

Milestone 5: refactor into maintainable modules.

This file focuses on:
- Connection lifecycle (discover -> connect -> handshake -> reconnect)
- Protocol routing (JSON messages vs binary audio frames)
- Publishable state/events (Milestone 2 contract)
- Delegating playback/decoder work to :mod:`linux_voice_assistant.sendspin.player`

Compatibility notes:
- The LVA main entrypoint expects a SendspinClient interface with:
    - async run()
    - stop()  (non-async)
    - async disconnect(reason=...)
  This module implements that API for backwards compatibility.

Protocol compatibility notes:
- The Sendspin spec describes messages as `{type, payload:{...}}`, but some servers
  place fields at the top-level. This client:
  - *Sends* messages with both `payload` and duplicated top-level fields.
  - *Receives* messages by unwrapping `payload` when present, otherwise treating
    all non-`type`/`payload` keys as the payload.

websockets compatibility:
- Newer websockets releases on Python 3.13 may return a ClientConnection object
  without a `.closed` attribute. Use `_ws_is_closed()` instead of `ws.closed`.

Clock sync + multi-room timing:
- Sendspin binary audio frames include a server timestamp (us) for first sample.
- We maintain a Kalman clock sync to convert server time -> local time.
- The player uses this mapping to schedule playout for improved multi-room sync.

Time sync robustness (Sendspin CLI-inspired):
- Optional *burst* probing (N client/time probes spaced by ~50ms) and choose the
  lowest-RTT sample to update the Kalman model.
- Optional adaptive polling interval based on estimated offset variance.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple, List

import websockets
from websockets.exceptions import ConnectionClosed

from ..event_bus import EventBus
from .clock_sync import KalmanClockSync
from .controller import SendspinControllerCommandHandler, SendspinDuckingHandler
from .discovery import discover_sendspin_servers
from .models import SendspinInternalState
from .player import SendspinPlayerPipeline

_LOGGER = logging.getLogger(__name__)

# Silence websockets frame dumps (keeps our Sendspin debug logs intact)
for _name in (
    "websockets",
    "websockets.client",
    "websockets.server",
    "websockets.protocol",
    "websockets.frames",
):
    logging.getLogger(_name).setLevel(logging.WARNING)


# Valid reasons for client/goodbye per the Sendspin spec
GOODBYE_REASON_ANOTHER_SERVER = "another_server"
GOODBYE_REASON_SHUTDOWN = "shutdown"
GOODBYE_REASON_RESTART = "restart"
GOODBYE_REASON_USER_REQUEST = "user_request"


def _now_us() -> int:
    """Monotonic microseconds for Sendspin timing messages."""
    return int(time.monotonic_ns() // 1000)


def _ws_is_closed(ws: Any) -> bool:
    """Best-effort closed check across websockets versions / connection types."""
    if ws is None:
        return True

    try:
        closed_attr = getattr(ws, "closed")
        if isinstance(closed_attr, bool):
            return closed_attr
    except Exception:
        pass

    try:
        close_code = getattr(ws, "close_code", None)
        if close_code is not None:
            return True
    except Exception:
        pass

    try:
        state = getattr(ws, "state", None)
        if state is None:
            return False

        try:
            from websockets.protocol import State  # type: ignore

            if isinstance(state, State):
                return state is State.CLOSED
        except Exception:
            pass

        if isinstance(state, str):
            return state.lower() == "closed"
        if isinstance(state, int):
            return state == 3
    except Exception:
        pass

    return False


class SendspinClient:
    """Sendspin client connection + protocol router."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        event_bus: Optional[EventBus],
        config: Any,
        client_id: str,
        client_name: str,
    ) -> None:
        self._loop = loop
        self._event_bus = event_bus
        self._cfg = config
        self._client_id = client_id
        self._client_name = client_name

        self._enabled: bool = bool(self._cfg_get(self._cfg, "enabled", False))

        self._stop_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()
        self._ws: Any = None

        self._server_hello: dict = {}
        self._active_roles: set = set()
        self._supported_controller_commands: set = set()

        # Internal state model (Milestone 2 contract)
        self._state = SendspinInternalState()

        # Volume / mute / ducking
        coord_cfg = self._cfg_get_section(self._cfg, "coordination")
        self._duck_during_voice: bool = bool(self._cfg_get(coord_cfg, "duck_during_voice", True))
        self._duck_gain: float = float(self._cfg_get(coord_cfg, "duck_gain", 0.3) or 0.3)
        self._ducked: bool = False

        # Connection config
        conn_cfg = self._cfg_get_section(self._cfg, "connection")
        self._hello_timeout_s: float = float(
            self._cfg_get(conn_cfg, "hello_timeout_seconds", 8.0) or 8.0
        )
        self._ping_interval_s: float = float(
            self._cfg_get(self._cfg_get_section(self._cfg, "connection"), "ping_interval_seconds", 0)
        )
        self._ping_timeout_s: float = float(
            self._cfg_get(self._cfg_get_section(self._cfg, "connection"), "ping_timeout_seconds", 20.0)
        )

        # Base time sync interval (used as fallback when adaptive is disabled)
        self._time_sync_interval_s: float = float(
            self._cfg_get(conn_cfg, "time_sync_interval_seconds", 5.0)
        )

        # Sendspin CLI-inspired robust time sync options (safe defaults)
        self._time_sync_adaptive: bool = bool(
            self._cfg_get(conn_cfg, "time_sync_adaptive", True)
        )
        self._time_sync_min_interval_s: float = float(
            self._cfg_get(conn_cfg, "time_sync_min_interval_seconds", 0.5)
        )
        self._time_sync_max_interval_s: float = float(
            self._cfg_get(conn_cfg, "time_sync_max_interval_seconds", 10.0)
        )

        self._time_sync_burst_size: int = int(
            self._cfg_get(conn_cfg, "time_sync_burst_size", 8)
        )
        # spacing between probes within a burst
        self._time_sync_burst_spacing_s: float = float(
            self._cfg_get(conn_cfg, "time_sync_burst_spacing_seconds", 0.05)
        )
        # grace period after the last probe to allow responses to arrive
        self._time_sync_burst_grace_s: float = float(
            self._cfg_get(conn_cfg, "time_sync_burst_grace_seconds", 0.15)
        )
        # Allow disabling burst by setting <= 1
        if self._time_sync_burst_size < 1:
            self._time_sync_burst_size = 1

        # Collector for burst samples (protected by lock)
        self._time_sync_collecting: bool = False
        self._time_sync_samples: List[Dict[str, int]] = []
        self._time_sync_lock = asyncio.Lock()
        # Outstanding t1 values in case server doesn't echo client_transmitted (best-effort)
        self._time_sync_pending_t1: List[int] = []

        # Seed initial player state from sendspin.initial (which __main__.py seeds from preferences.json)
        init_cfg = self._cfg_get_section(self._cfg, "initial")
        try:
            v = int(self._cfg_get(init_cfg, "volume", 100))
        except Exception:
            v = 100
        self._user_volume = max(0, min(100, v))
        self._muted = bool(self._cfg_get(init_cfg, "muted", False))

        # Logging toggles
        log_cfg = self._cfg_get_section(self._cfg, "logging")
        self._debug_protocol: bool = bool(self._cfg_get(log_cfg, "debug_protocol", False))
        self._debug_payloads: bool = bool(self._cfg_get(log_cfg, "debug_payloads", False))

        # Clock sync (offset + drift)
        self._clock = KalmanClockSync()
        self._last_time_sync_t1: int = 0

        # Player pipeline (extracted)
        self._player = SendspinPlayerPipeline(
            loop=self._loop,
            config=self._cfg,
            client_id=self._client_id,
            stop_event=self._stop_event,
            disconnect_event=self._disconnect_event,
        )
        self._player.set_clock_sync(self._clock)
        self._player.set_audio_state(muted=self._muted, effective_volume=self._effective_volume())

        # EventBus hooks
        self._ducking_handler = None
        self._controller_handler = None
        if self._event_bus is not None:
            self._ducking_handler = SendspinDuckingHandler(self._event_bus, self)
            self._controller_handler = SendspinControllerCommandHandler(self._event_bus, self)

        self._recv_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._time_task: Optional[asyncio.Task] = None

    # ---------------------------------------------------------------------
    # Back-compat API expected by linux_voice_assistant/__main__.py
    # ---------------------------------------------------------------------

    async def run(self) -> None:
        """Main entrypoint: keep running connect/reconnect loop until stopped."""
        if not self._enabled:
            return
        await self._connect_loop()

    def stop(self) -> None:
        """Non-async stop request (back-compat)."""
        self._stop_event.set()
        self._disconnect_event.set()

    async def disconnect(self, reason: str = "shutdown") -> None:
        """Async disconnect/shutdown (back-compat).

        Per the Sendspin spec, we should send client/goodbye before closing
        the connection to inform the server why we're disconnecting.

        Args:
            reason: One of 'shutdown', 'restart', 'user_request', or 'another_server'.
                    Maps to the Sendspin spec's client/goodbye reason field.
        """
        _LOGGER.debug("Sendspin: disconnect requested (%s)", reason)

        # Map common reason strings to spec-compliant values
        goodbye_reason = self._map_disconnect_reason(reason)

        # Send client/goodbye before closing (per spec)
        await self._send_client_goodbye(goodbye_reason)

        self._stop_event.set()
        self._disconnect_event.set()

        for t in (self._recv_task, self._ping_task, self._time_task):
            if t and not t.done():
                t.cancel()

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            finally:
                self._ws = None

        try:
            await self._player.stop_stream(reason=reason)
        except Exception:
            pass
        try:
            await self._player.shutdown()
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Config helpers (dict-or-object tolerant)
    # ---------------------------------------------------------------------

    @staticmethod
    def _cfg_get(obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _cfg_get_section(cls, obj: Any, key: str) -> Any:
        if obj is None:
            return None
        val = cls._cfg_get(obj, key, None)
        if isinstance(val, dict):
            return val
        if val is None:
            return None
        return val

    # ---------------------------------------------------------------------
    # Message wrapping / unwrapping
    # ---------------------------------------------------------------------

    @staticmethod
    def _build_message(mtype: str, payload: dict) -> dict:
        msg: Dict[str, Any] = {"type": mtype, "payload": payload}
        # Also duplicate payload fields at top level for server compatibility
        for k, v in payload.items():
            if k in ("type", "payload"):
                continue
            if k not in msg:
                msg[k] = v
        return msg

    @staticmethod
    def _unwrap_message(msg: dict) -> Tuple[str, dict]:
        mtype = str(msg.get("type") or "")
        payload = msg.get("payload")
        if isinstance(payload, dict):
            return mtype, payload
        pl: Dict[str, Any] = {k: v for k, v in msg.items() if k not in ("type", "payload")}
        return mtype, pl

    # ---------------------------------------------------------------------
    # client/goodbye support (Sendspin spec compliance)
    # ---------------------------------------------------------------------

    @staticmethod
    def _map_disconnect_reason(reason: str) -> str:
        """Map a disconnect reason string to a spec-compliant client/goodbye reason.

        Per the Sendspin spec, valid reasons are:
        - 'another_server': client is switching to a different Sendspin server
        - 'shutdown': client is shutting down
        - 'restart': client is restarting and will reconnect
        - 'user_request': user explicitly requested to disconnect

        Args:
            reason: The reason string passed to disconnect()

        Returns:
            A spec-compliant reason string
        """
        reason_lower = str(reason).lower().strip()

        # Direct matches
        if reason_lower in (GOODBYE_REASON_ANOTHER_SERVER, GOODBYE_REASON_SHUTDOWN,
                           GOODBYE_REASON_RESTART, GOODBYE_REASON_USER_REQUEST):
            return reason_lower

        # Common aliases
        if reason_lower in ("stop", "exit", "quit", "close"):
            return GOODBYE_REASON_SHUTDOWN
        if reason_lower in ("reboot", "reconnect"):
            return GOODBYE_REASON_RESTART
        if reason_lower in ("user", "manual"):
            return GOODBYE_REASON_USER_REQUEST
        if reason_lower in ("switch", "server_switch", "switch_server"):
            return GOODBYE_REASON_ANOTHER_SERVER

        # Default to shutdown for unknown reasons
        return GOODBYE_REASON_SHUTDOWN

    async def _send_client_goodbye(self, reason: str) -> None:
        """Send client/goodbye message before disconnecting.

        Per the Sendspin spec:
        - Sent by the client before gracefully closing the connection
        - Allows the client to inform the server why it is disconnecting
        - Upon receiving this message, the server should initiate the disconnect

        Args:
            reason: One of 'another_server', 'shutdown', 'restart', 'user_request'
        """
        ws = self._ws
        if ws is None or _ws_is_closed(ws):
            return

        payload = {"reason": reason}
        msg = self._build_message("client/goodbye", payload)

        try:
            await ws.send(json.dumps(msg))
            if self._debug_protocol:
                _LOGGER.debug("Sendspin: sent client/goodbye (reason=%s)", reason)
        except Exception:
            _LOGGER.debug("Sendspin: failed to send client/goodbye", exc_info=True)

    # ---------------------------------------------------------------------
    # External controls (called by EventBus handlers)
    # ---------------------------------------------------------------------

    def set_ducked(self, ducked: bool) -> None:
        if not self._duck_during_voice:
            return
        ducked = bool(ducked)
        if ducked == self._ducked:
            return
        self._ducked = ducked
        self._publish_audio_state()
        self._apply_audio_state_to_player()

    async def send_controller_command(
        self,
        command: str,
        *,
        volume: Optional[int] = None,
        mute: Optional[bool] = None,
    ) -> None:
        if self._ws is None or _ws_is_closed(self._ws):
            return

        cmd = str(command).strip().lower()
        if not cmd:
            return

        if self._supported_controller_commands and cmd not in self._supported_controller_commands:
            _LOGGER.debug("Sendspin: ignoring unsupported controller command %r", cmd)
            return

        controller_obj: Dict[str, Any] = {"command": cmd}
        if volume is not None:
            controller_obj["volume"] = int(volume)
        if mute is not None:
            controller_obj["mute"] = bool(mute)

        payload = {"controller": controller_obj}
        msg = self._build_message("client/command", payload)

        try:
            await self._ws.send(json.dumps(msg))
            if self._debug_protocol:
                _LOGGER.debug("Sendspin: sent client/command controller=%s", controller_obj)
        except Exception:
            _LOGGER.debug("Sendspin: failed to send controller command", exc_info=True)

    # ---------------------------------------------------------------------
    # Volume/audio state helpers
    # ---------------------------------------------------------------------

    def _effective_volume(self) -> int:
        """Compute the effective volume considering ducking."""
        if self._ducked:
            return max(0, min(100, int(self._user_volume * self._duck_gain)))
        return self._user_volume

    def _apply_audio_state_to_player(self) -> None:
        self._player.set_audio_state(muted=self._muted, effective_volume=self._effective_volume())

    # ---------------------------------------------------------------------
    # State publishing (EventBus / Milestone 2 contract)
    # ---------------------------------------------------------------------

    def _publish_connection_state(self) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish("sendspin_connection_state", {
            "connected": self._state.connection.connected,
            "endpoint": self._state.connection.endpoint,
            "server_id": self._state.connection.server_id,
            "server_name": self._state.connection.server_name,
        })

    def _publish_playback_state(self) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish("sendspin_playback_state", {
            "playback_state": self._state.playback.playback_state,
            "codec": self._state.playback.stream.codec,
            "sample_rate": self._state.playback.stream.sample_rate,
            "channels": self._state.playback.stream.channels,
            "bit_depth": self._state.playback.stream.bit_depth,
        })

    def _publish_metadata(self) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish("sendspin_metadata", copy.deepcopy(self._state.metadata))

    def _publish_audio_state(self) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish("sendspin_audio_state", {
            "volume": self._user_volume,
            "muted": self._muted,
            "ducked": self._ducked,
            "effective_volume": self._effective_volume(),
        })

    def _publish_clock_sync(self) -> None:
        if self._event_bus is None:
            return
        stats = self._clock.get_stats()
        self._event_bus.publish("sendspin_clock_sync", {
            "offset_us": stats.offset_us,
            "drift_ppm": stats.drift_ppm,
            "offset_stddev_us": stats.offset_stddev_us,
            "samples": stats.samples,
            "last_rtt_us": stats.last_rtt_us,
            "ewma_rtt_us": stats.ewma_rtt_us,
            "ewma_jitter_us": stats.ewma_jitter_us,
            "synced": stats.synced,
        })

    # ---------------------------------------------------------------------
    # client/state updates
    # ---------------------------------------------------------------------

    async def _send_client_state_update(self) -> None:
        ws = self._ws
        if ws is None or _ws_is_closed(ws):
            return

        roles_cfg = self._cfg_get_section(self._cfg, "roles")
        payload: Dict[str, Any] = {"state": "synchronized"}
        if bool(self._cfg_get(roles_cfg, "player", True)):
            payload["player"] = {"volume": int(self._user_volume), "muted": bool(self._muted)}

        msg = self._build_message("client/state", payload)
        try:
            await ws.send(json.dumps(msg))
        except Exception:
            _LOGGER.debug("Sendspin: failed to send client/state update", exc_info=True)

    # ---------------------------------------------------------------------
    # Discovery + connect loop
    # ---------------------------------------------------------------------

    async def _select_endpoint(self) -> Optional[str]:
        conn_cfg = self._cfg_get_section(self._cfg, "connection")
        mdns = bool(self._cfg_get(conn_cfg, "mdns", True))
        host = self._cfg_get(conn_cfg, "server_host", None)
        port = int(self._cfg_get(conn_cfg, "server_port", 8927) or 8927)
        path = str(self._cfg_get(conn_cfg, "server_path", "/sendspin") or "/sendspin")

        if host:
            return f"ws://{host}:{port}{path}"

        if not mdns:
            return None

        servers = await discover_sendspin_servers(timeout_s=2.5)
        if not servers:
            return None

        return servers[0].ws_url()

    async def _connect_loop(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                endpoint = await self._select_endpoint()
                if not endpoint:
                    await asyncio.sleep(min(5.0, backoff))
                    backoff = min(30.0, backoff * 1.5)
                    continue

                await self._connect_once(endpoint)
                backoff = 1.0

                await self._disconnect_event.wait()
                self._disconnect_event.clear()

            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.debug("Sendspin: connect loop error", exc_info=True)

            await asyncio.sleep(min(10.0, backoff))
            backoff = min(30.0, backoff * 1.5)

    async def _connect_once(self, endpoint: str) -> None:
        self._server_hello = {}
        self._active_roles = set()
        self._supported_controller_commands = set()

        # Reset sync model per connection (server may move / restart)
        self._clock.reset()
        self._player.set_clock_sync(self._clock)

        # Reset time sync burst state
        async with self._time_sync_lock:
            self._time_sync_collecting = False
            self._time_sync_samples = []
            self._time_sync_pending_t1 = []

        self._state.connection.connected = False
        self._state.connection.endpoint = endpoint
        self._state.connection.server_id = None
        self._state.connection.server_name = None
        self._publish_connection_state()

        _LOGGER.info("Sendspin: connecting to %s", endpoint)

        async with websockets.connect(
            endpoint,
            ping_interval=None,  # disable library-level protocol pings
            close_timeout=2,
            max_queue=64,
        ) as ws:
            self._ws = ws
            self._disconnect_event.clear()

            try:
                await ws.send(json.dumps(self._build_client_hello()))
                server_hello = await asyncio.wait_for(self._wait_for_server_hello(ws), timeout=self._hello_timeout_s)
            except asyncio.TimeoutError:
                _LOGGER.warning("Sendspin: timed out waiting for server/hello (%.1fs)", self._hello_timeout_s)
                return

            self._handle_server_hello(endpoint, server_hello)

            # Immediately report our seeded state (preferences-backed volume/mute)
            try:
                await ws.send(json.dumps(self._build_initial_client_state()))
            except Exception:
                pass

            self._recv_task = self._loop.create_task(self._recv_loop(ws))

            # Only start ws ping loop if configured. (M4 regression guard: FLAC pause/idle + ping can kill ws.)
            if self._ping_interval_s and self._ping_interval_s > 0 and self._ping_timeout_s and self._ping_timeout_s > 0:
                self._ping_task = self._loop.create_task(self._ping_loop(ws))
            else:
                self._ping_task = None
                _LOGGER.debug("Sendspin: ws ping loop disabled by config (ping_interval_seconds/ping_timeout_seconds)")

            self._time_task = self._loop.create_task(self._time_sync_loop(ws))

            try:
                await self._recv_task
            finally:
                self._ws = None
                for t in (self._ping_task, self._time_task):
                    if t and not t.done():
                        t.cancel()

                await self._player.stop_stream(reason="disconnect")

                self._state.connection.connected = False
                self._publish_connection_state()
                self._disconnect_event.set()

    async def _wait_for_server_hello(self, ws: Any) -> dict:
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                continue
            try:
                data = json.loads(msg)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            mtype, payload = self._unwrap_message(data)
            if mtype == "server/hello" and isinstance(payload, dict):
                return payload

    # ---------------------------------------------------------------------
    # Message builders / handlers
    # ---------------------------------------------------------------------

    def _build_client_hello(self) -> dict:
        roles_cfg = self._cfg_get_section(self._cfg, "roles")
        supported_roles = []
        if bool(self._cfg_get(roles_cfg, "player", True)):
            supported_roles.append("player@v1")
        if bool(self._cfg_get(roles_cfg, "metadata", True)):
            supported_roles.append("metadata@v1")
        if bool(self._cfg_get(roles_cfg, "controller", True)):
            supported_roles.append("controller@v1")

        client_cfg = self._cfg_get_section(self._cfg, "client")
        device_info = self._cfg_get(client_cfg, "device_info", None)
        if not isinstance(device_info, dict):
            device_info = None

        payload: Dict[str, Any] = {
            "client_id": self._client_id,
            "name": str(self._cfg_get(client_cfg, "name", self._client_name) or self._client_name),
            "version": 1,
            "supported_roles": supported_roles,
        }
        if device_info:
            payload["device_info"] = device_info

        if "player@v1" in supported_roles:
            payload["player@v1_support"] = self._build_player_support_v1()

        return self._build_message("client/hello", payload)

    def _build_player_support_v1(self) -> dict:
        """Advertise player capabilities."""
        player_cfg = self._cfg_get_section(self._cfg, "player")

        # Supported formats
        codecs = self._cfg_get(player_cfg, "supported_codecs", ["pcm"])
        if not isinstance(codecs, list):
            codecs = ["pcm"]

        rate = int(self._cfg_get(player_cfg, "sample_rate", 48000) or 48000)
        ch = int(self._cfg_get(player_cfg, "channels", 2) or 2)
        bd = int(self._cfg_get(player_cfg, "bit_depth", 16) or 16)

        formats = []
        for c in codecs:
            c2 = str(c).lower().strip()
            if not c2:
                continue
            formats.append({
                "codec": c2,
                "channels": ch,
                "sample_rate": rate,
                "bit_depth": bd,
            })
        if not formats:
            formats.append({"codec": "pcm", "channels": 2, "sample_rate": 48000, "bit_depth": 16})

        buffer_cap = int(self._cfg_get(player_cfg, "buffer_capacity_bytes", 1048576) or 1048576)

        supported_cmds = self._cfg_get(player_cfg, "supported_commands", ["volume", "mute"])
        if not isinstance(supported_cmds, list):
            supported_cmds = ["volume", "mute"]
        supported_cmds_norm = []
        for c in supported_cmds:
            c2 = str(c).lower().strip()
            if c2 and c2 not in supported_cmds_norm:
                supported_cmds_norm.append(c2)
        if not supported_cmds_norm:
            supported_cmds_norm = ["volume", "mute"]

        return {
            "supported_formats": formats,
            "buffer_capacity": buffer_cap,
            "supported_commands": supported_cmds_norm,
        }

    def _build_initial_client_state(self) -> dict:
        payload: Dict[str, Any] = {"state": "synchronized"}
        roles_cfg = self._cfg_get_section(self._cfg, "roles")
        if bool(self._cfg_get(roles_cfg, "player", True)):
            payload["player"] = {"volume": int(self._user_volume), "muted": bool(self._muted)}
        return self._build_message("client/state", payload)

    def _handle_server_hello(self, endpoint: str, payload: dict) -> None:
        self._server_hello = payload

        self._state.connection.connected = True
        self._state.connection.endpoint = endpoint
        self._state.connection.server_id = payload.get("server_id")
        self._state.connection.server_name = payload.get("name") or payload.get("server_name")
        self._publish_connection_state()

        roles = payload.get("active_roles")
        if isinstance(roles, list):
            self._active_roles = {str(r) for r in roles}

        if self._debug_protocol:
            _LOGGER.debug("Sendspin: server/hello active_roles=%s", sorted(self._active_roles))

    # ---------------------------------------------------------------------
    # Receive + supporting loops
    # ---------------------------------------------------------------------

    async def _recv_loop(self, ws: Any) -> None:
        try:
            while not self._stop_event.is_set():
                msg = await ws.recv()

                if isinstance(msg, bytes):
                    await self._player.handle_binary_frame(msg)
                    if self._player.sink_failed:
                        self._disconnect_event.set()
                        return
                    continue

                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                if not isinstance(data, dict):
                    continue

                mtype, payload = self._unwrap_message(data)

                if self._debug_protocol:
                    _LOGGER.debug("Sendspin: rx type=%s", mtype)
                if self._debug_payloads:
                    _LOGGER.debug("Sendspin: rx payload=%s", payload)

                await self._handle_json_message(mtype, payload)

        except ConnectionClosed:
            return
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: recv loop error", exc_info=True)

    async def _ping_loop(self, ws: Any) -> None:
        """WebSocket keepalive pings.

        M4/M5 guard:
        - Some MA/Sendspin servers mis-handle protocol ping/pong during long idle
          (e.g., after FLAC stream/end). This loop is optional and can be disabled
          by setting ping_interval_seconds=0 or ping_timeout_seconds=0.
        """
        if not self._ping_interval_s or self._ping_interval_s <= 0:
            return
        if not self._ping_timeout_s or self._ping_timeout_s <= 0:
            return

        try:
            while not self._stop_event.is_set() and not _ws_is_closed(ws):
                ping_fn = getattr(ws, "ping", None)
                if not callable(ping_fn):
                    _LOGGER.debug(
                        "Sendspin: ws ping not supported by connection object; disabling keepalive pings"
                    )
                    return

                try:
                    pong_waiter = ping_fn()
                    if pong_waiter is not None:
                        await asyncio.wait_for(pong_waiter, timeout=self._ping_timeout_s)
                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Sendspin: ws ping timed out (%.1fs); disabling ws pings for this connection",
                        self._ping_timeout_s,
                    )
                    return
                except asyncio.CancelledError:
                    return
                except Exception:
                    _LOGGER.debug(
                        "Sendspin: ws ping failed; disabling ws pings for this connection",
                        exc_info=True,
                    )
                    return

                await asyncio.sleep(self._ping_interval_s)
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: ping loop error", exc_info=True)

    def _compute_time_sync_interval_s(self) -> float:
        """Choose the next time-sync interval based on sync confidence."""
        # Baseline: preserve previous behavior if adaptive is disabled.
        if not self._time_sync_adaptive:
            return max(0.1, float(self._time_sync_interval_s))

        samples = int(self._clock.samples)
        var = float(self._clock.variance)

        # Rapid convergence phase
        if samples < 5:
            interval = 0.5
        else:
            # Map variance bands (us^2) to polling interval tiers.
            # std < 1ms => 10s
            if var < 1e6:
                interval = 10.0
            # std < 2ms => 5s
            elif var < 4e6:
                interval = 5.0
            # std < 5ms => 2s
            elif var < 25e6:
                interval = 2.0
            else:
                interval = 1.0

        interval = max(float(self._time_sync_min_interval_s), min(float(self._time_sync_max_interval_s), float(interval)))
        return max(0.1, interval)

    async def _time_sync_loop(self, ws: Any) -> None:
        """Time sync loop.

        Supports:
        - Single-shot polling (legacy)
        - Burst probing (N probes spaced by ~50ms) with best-RTT sample selection
        - Adaptive polling interval (based on clock variance)
        """
        try:
            while not self._stop_event.is_set() and not _ws_is_closed(ws):
                interval_s = self._compute_time_sync_interval_s()

                # Burst mode is enabled when burst_size > 1
                burst_size = int(self._time_sync_burst_size)
                burst_size = max(1, burst_size)

                if burst_size == 1:
                    # Legacy behavior: send one probe and update on receipt
                    try:
                        t1 = _now_us()
                        self._last_time_sync_t1 = int(t1)
                        msg = self._build_message("client/time", {"client_transmitted": int(t1)})
                        await ws.send(json.dumps(msg))
                    except Exception:
                        return

                    await asyncio.sleep(interval_s)
                    continue

                # Burst probing
                async with self._time_sync_lock:
                    self._time_sync_collecting = True
                    self._time_sync_samples = []
                    self._time_sync_pending_t1 = []

                for _ in range(burst_size):
                    if self._stop_event.is_set() or _ws_is_closed(ws):
                        return
                    try:
                        t1 = _now_us()
                        self._last_time_sync_t1 = int(t1)
                        msg = self._build_message("client/time", {"client_transmitted": int(t1)})
                        async with self._time_sync_lock:
                            self._time_sync_pending_t1.append(int(t1))
                        await ws.send(json.dumps(msg))
                    except Exception:
                        return
                    await asyncio.sleep(self._time_sync_burst_spacing_s)

                # Wait for responses
                await asyncio.sleep(self._time_sync_burst_grace_s)

                # Select best sample
                async with self._time_sync_lock:
                    self._time_sync_collecting = False
                    samples = list(self._time_sync_samples)
                    self._time_sync_samples = []
                    self._time_sync_pending_t1 = []

                if samples:
                    best = min(samples, key=lambda s: s.get("rtt_us", float("inf")))
                    self._clock.update(
                        client_transmitted_us=int(best["t1"]),
                        server_received_us=int(best["t2"]),
                        server_transmitted_us=int(best["t3"]),
                        client_received_us=int(best["t4"]),
                    )
                    self._publish_clock_sync()

                await asyncio.sleep(interval_s)

        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: time sync loop error", exc_info=True)

    # ---------------------------------------------------------------------
    # JSON message dispatch
    # ---------------------------------------------------------------------

    async def _handle_json_message(self, mtype: str, payload: dict) -> None:
        if mtype == "server/state":
            await self._handle_server_state(payload)
            return

        if mtype == "server/time":
            await self._handle_server_time(payload)
            return

        if mtype == "group/update":
            await self._handle_group_update(payload)
            return

        if mtype == "stream/start":
            await self._handle_stream_start(payload)
            return

        if mtype == "stream/clear":
            await self._handle_stream_clear(payload)
            return

        if mtype in ("stream/stop", "stream/end"):
            await self._handle_stream_stop(mtype)
            return

        if mtype == "server/command":
            await self._handle_server_command(payload)
            return

        if mtype == "player/state":
            await self._handle_player_state_legacy(payload)
            return
        if mtype in ("player/metadata", "metadata/update"):
            await self._handle_metadata_legacy(payload)
            return

    async def _handle_server_state(self, payload: dict) -> None:
        """Handle server/state (capabilities + metadata)."""
        ctrl = payload.get("controller")
        if isinstance(ctrl, dict):
            cmds = ctrl.get("supported_commands")
            if isinstance(cmds, list):
                self._supported_controller_commands = {str(c).lower() for c in cmds}

        md = payload.get("metadata")
        if isinstance(md, dict):
            self._state.metadata = md
            self._publish_metadata()

    async def _handle_server_time(self, payload: dict) -> None:
        """
        Handle server/time response to client/time.

        Spec fields (payload):
          - client_transmitted
          - server_received
          - server_transmitted

        We use local receive time as client_received.
        """
        try:
            t1_raw = payload.get("client_transmitted")
            t2_raw = payload.get("server_received")
            t3_raw = payload.get("server_transmitted")

            try:
                t1 = int(t1_raw) if t1_raw is not None else 0
            except Exception:
                t1 = 0
            try:
                t2 = int(t2_raw) if t2_raw is not None else 0
            except Exception:
                t2 = 0
            try:
                t3 = int(t3_raw) if t3_raw is not None else 0
            except Exception:
                t3 = 0

            # Some servers don't echo client_transmitted; use FIFO as best effort.
            if t1 <= 0:
                async with self._time_sync_lock:
                    if self._time_sync_pending_t1:
                        t1 = int(self._time_sync_pending_t1.pop(0))
                    else:
                        t1 = int(self._last_time_sync_t1 or 0)

            if t1 <= 0 or t2 <= 0 or t3 <= 0:
                return

            t4 = _now_us()

            # If we're collecting a burst, store the sample and return.
            async with self._time_sync_lock:
                collecting = bool(self._time_sync_collecting)

            if collecting:
                rtt = (int(t4) - int(t1)) - (int(t3) - int(t2))
                rtt = max(0, int(rtt))
                async with self._time_sync_lock:
                    self._time_sync_samples.append(
                        {"t1": int(t1), "t2": int(t2), "t3": int(t3), "t4": int(t4), "rtt_us": int(rtt)}
                    )
                return

            # Legacy: apply immediately
            self._clock.update(
                client_transmitted_us=int(t1),
                server_received_us=int(t2),
                server_transmitted_us=int(t3),
                client_received_us=int(t4),
            )
            self._publish_clock_sync()
        except Exception:
            _LOGGER.debug("Sendspin: failed to handle server/time", exc_info=True)

    async def _handle_group_update(self, payload: dict) -> None:
        pstate = payload.get("playback_state")
        if isinstance(pstate, str) and pstate:
            if pstate != self._state.playback.playback_state:
                self._state.playback.playback_state = pstate
                self._publish_playback_state()

    async def _handle_server_command(self, payload: dict) -> None:
        player = payload.get("player")
        if not isinstance(player, dict):
            return

        changed = False

        cmd = str(player.get("command") or "").lower().strip()
        if cmd == "volume":
            vol = player.get("volume")
            if isinstance(vol, (int, float)):
                new_vol = max(0, min(100, int(vol)))
                if new_vol != self._user_volume:
                    self._user_volume = new_vol
                    changed = True
                    if self._event_bus is not None:
                        self._event_bus.publish("sendspin_volume_changed", {"volume": self._user_volume})

        elif cmd == "mute":
            mute = player.get("mute")
            if isinstance(mute, bool) and mute != self._muted:
                self._muted = mute
                changed = True

        if changed:
            self._publish_audio_state()
            self._apply_audio_state_to_player()
            await self._send_client_state_update()

    async def _handle_stream_start(self, payload: dict) -> None:
        fmt = payload.get("player")
        if not isinstance(fmt, dict):
            fmt = payload

        codec = str(fmt.get("codec") or "pcm").lower()
        rate = int(fmt.get("sample_rate") or fmt.get("rate") or 48000)
        ch = int(fmt.get("channels") or 2)
        bd = int(fmt.get("bit_depth") or 16)

        self._state.playback.playback_state = "playing"
        self._state.playback.stream.codec = codec
        self._state.playback.stream.sample_rate = rate
        self._state.playback.stream.channels = ch
        self._state.playback.stream.bit_depth = bd
        self._publish_playback_state()

        await self._player.start_stream(codec=codec, sample_rate=rate, channels=ch, bit_depth=bd)
        self._apply_audio_state_to_player()
        self._publish_audio_state()

    async def _handle_stream_clear(self, payload: dict) -> None:
        """Handle server -> client `stream/clear`.

        Spec intent: clear buffers without ending the stream (used for seek).
        Implementation: Option A (drop queued audio only). We keep mpv alive.
        """
        roles = payload.get("roles")
        if isinstance(roles, str):
            roles_list = [roles]
        elif isinstance(roles, list):
            roles_list = [str(r) for r in roles if isinstance(r, (str, int))]
        else:
            roles_list = ["player"]

        # Only clear player buffers if we have the player role enabled.
        roles_cfg = self._cfg_get_section(self._cfg, "roles")
        player_enabled = bool(self._cfg_get(roles_cfg, "player", True))

        if player_enabled and "player" in [r.lower() for r in roles_list]:
            if self._debug_protocol:
                _LOGGER.debug("Sendspin: stream/clear (roles=%s)", roles_list)
            try:
                await self._player.clear_buffer()
            except Exception:
                _LOGGER.debug("Sendspin: failed to clear player buffer", exc_info=True)

    async def _handle_stream_stop(self, reason: str) -> None:
        # Stop local pipeline first.
        await self._player.stop_stream(reason=reason)

        # Nudge control plane after stream stop/end (M4 regression guard).
        self._state.playback.playback_state = "stopped"
        self._publish_playback_state()

    # ---------------------------------------------------------------------
    # Legacy message handlers (older MA versions)
    # ---------------------------------------------------------------------

    async def _handle_player_state_legacy(self, payload: dict) -> None:
        """Handle legacy player/state messages from older servers."""
        vol = payload.get("volume")
        if isinstance(vol, (int, float)):
            new_vol = max(0, min(100, int(vol)))
            if new_vol != self._user_volume:
                self._user_volume = new_vol
                self._publish_audio_state()
                self._apply_audio_state_to_player()

        mute = payload.get("muted") or payload.get("mute")
        if isinstance(mute, bool) and mute != self._muted:
            self._muted = mute
            self._publish_audio_state()
            self._apply_audio_state_to_player()

    async def _handle_metadata_legacy(self, payload: dict) -> None:
        """Handle legacy metadata messages from older servers."""
        self._state.metadata = payload
        self._publish_metadata()
