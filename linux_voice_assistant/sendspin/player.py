"""Sendspin playback pipeline (stream lifecycle + mpv/decoder management).

Milestone 5 extraction:
- Move mpv sink lifecycle, decoder lifecycle (ffmpeg + opuslib), and binary frame
  routing into a focused module.
- Keep behavior identical to the previous monolithic implementation.

The pipeline does not know about websockets. The SendspinClient owns connection
logic and calls into this class on stream events and binary frames.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections import deque
from typing import Any, Deque, Optional

_LOGGER = logging.getLogger(__name__)

_BINARY_HEADER_LEN = 9


class SendspinPlayerPipeline:
    """Low-latency raw PCM sink using mpv + optional decode stage.

    Supported negotiated codecs:
    - pcm: payload is raw PCM s16le frames
    - flac: payload is FLAC bitstream frames -> decoded by ffmpeg to PCM
    - opus: payload is Opus packets; decoded by opuslib when possible, else
            by ffmpeg when needed.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        config: Any,
        client_id: str,
        stop_event: asyncio.Event,
        disconnect_event: asyncio.Event,
    ) -> None:
        self._loop = loop
        self._cfg = config
        self._client_id = client_id
        self._stop_event = stop_event
        self._disconnect_event = disconnect_event

        # Streaming state (local)
        self._stream_active: bool = False
        self._stream_codec: str = "pcm"
        self._stream_rate: int = 48000
        self._stream_channels: int = 2
        self._stream_bit_depth: int = 16

        # mpv process (PCM sink)
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

        # Prebuffer for codecs that require an external decoder (e.g., FLAC via ffmpeg).
        self._flac_prebuffer: Deque[bytes] = deque()
        self._flac_prebuffer_bytes: int = 0
        self._flac_prebuffer_max_bytes: int = 512 * 1024

        # Opus decoder backend selection
        self._opus_backend: str = "none"  # none|opuslib|ffmpeg
        self._opus_prebuffer: Deque[bytes] = deque()
        self._opus_prebuffer_bytes: int = 0
        self._opus_prebuffer_max_bytes: int = 256 * 1024

        # Count binary frames received while stream inactive (diagnostics only)
        self._inactive_bin_count: int = 0

        # Opus decoder (optional dependency)
        self._opus_decoder: Any = None
        self._opus_max_frame_size: int = 0
        self._opus_available: bool = False

        # FLAC / Opus ffmpeg decoder availability
        self._ffmpeg_path: str = "ffmpeg"
        self._ffmpeg_available: bool = False

        # mpv stderr tail buffer (for post-mortem)
        self._mpv_stderr_tail: Deque[str] = deque(maxlen=40)

        # mpv IPC
        self._mpv_ipc_path: Optional[str] = None
        self._mpv_ipc_ready: bool = False
        self._mpv_ipc_lock = asyncio.Lock()

        # Serialize heavy stream stop/start so recv loop never blocks.
        self._stream_lock = asyncio.Lock()

        # Diagnostics for binary frame flow
        self._pcm_frame_count: int = 0
        self._pcm_first_frame_at: Optional[float] = None
        self._pcm_last_frame_at: Optional[float] = None

        # IMPORTANT: stream stop is normal on pause/track-change.
        # We only trigger websocket disconnect when the PCM sink fails unexpectedly.
        self._sink_failed: bool = False

        # Current audio controls (set by client)
        self._muted: bool = False
        self._effective_volume: int = 100

        # Cache decoder availability for codec advertisement and runtime decisions
        try:
            player_cfg = self._cfg_get_section(self._cfg, "player")
            if player_cfg is not None:
                self._ffmpeg_path = str(
                    self._cfg_get(player_cfg, "ffmpeg_path", self._ffmpeg_path) or self._ffmpeg_path
                )
        except Exception:
            pass

        try:
            import opuslib  # type: ignore

            self._opus_available = True
        except Exception:
            self._opus_available = False

        self._ffmpeg_available = shutil.which(self._ffmpeg_path) is not None

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
    # Public API
    # ---------------------------------------------------------------------

    @property
    def stream_active(self) -> bool:
        return bool(self._stream_active)

    @property
    def sink_failed(self) -> bool:
        return bool(self._sink_failed)

    def set_audio_state(self, *, muted: bool, effective_volume: int) -> None:
        """Update audio controls that will be applied to mpv when active."""
        self._muted = bool(muted)
        self._effective_volume = max(0, min(100, int(effective_volume)))
        if self._stream_active:
            self._loop.create_task(self._apply_mpv_audio_state())

    async def start_stream(
        self,
        *,
        codec: str,
        sample_rate: int,
        channels: int,
        bit_depth: int,
    ) -> None:
        """Start the local sink/decoder for an incoming stream."""
        async with self._stream_lock:
            await self._start_stream(codec=codec, sample_rate=sample_rate, channels=channels, bit_depth=bit_depth)

    async def stop_stream(self, *, reason: str) -> None:
        """Stop the local sink/decoder."""
        async with self._stream_lock:
            await self._stop_stream(reason=reason)

    async def handle_binary_frame(self, frame: bytes) -> None:
        """Handle a binary audio frame from the websocket.

        This method is designed to be called directly from the websocket receive
        loop; heavy start/stop work is protected by internal locks.
        """
        if not frame:
            return

        if not self._stream_active:
            self._inactive_bin_count += 1
            if self._inactive_bin_count == 1 or (self._inactive_bin_count % 500) == 0:
                _LOGGER.debug(
                    "Sendspin: received binary frame while stream inactive (%d bytes)",
                    len(frame),
                )
            return

        payload_bytes = self._extract_pcm_payload(frame)
        if not payload_bytes:
            return

        # Decode / route based on negotiated codec
        if self._stream_codec == "pcm":
            pcm = payload_bytes
        elif self._stream_codec == "opus":
            pcm = await self._handle_opus_payload(payload_bytes)
            if pcm is None:
                return
        elif self._stream_codec == "flac":
            await self._handle_encoded_payload(payload_bytes, codec="flac")
            return
        else:
            return

        if not pcm:
            return

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

    async def shutdown(self) -> None:
        """Best-effort shutdown of the pipeline."""
        await self.stop_stream(reason="shutdown")

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

            await self._mpv_ipc_send({"command": ["set_property", "mute", bool(self._muted)]})
            await self._mpv_ipc_send({"command": ["set_property", "volume", int(self._effective_volume)]})

            _LOGGER.debug(
                "Sendspin: applied mpv audio state (muted=%s eff_vol=%s)",
                self._muted,
                self._effective_volume,
            )

    # ---------------------------------------------------------------------
    # Stream start/stop
    # ---------------------------------------------------------------------

    @staticmethod
    def _extract_pcm_payload(frame: bytes) -> bytes:
        if len(frame) <= _BINARY_HEADER_LEN:
            return b""
        return frame[_BINARY_HEADER_LEN:]

    async def _start_stream(
        self,
        *,
        codec: str,
        sample_rate: int,
        channels: int,
        bit_depth: int,
    ) -> None:
        # If already active, restart (this matches the prior behavior).
        if self._stream_active:
            await self._stop_stream(reason="restart")

        self._stream_codec = (codec or "pcm").lower()
        self._stream_rate = int(sample_rate or 48000)
        self._stream_channels = int(channels or 2)
        self._stream_bit_depth = int(bit_depth or 16)

        self._inactive_bin_count = 0
        self._pcm_frame_count = 0
        self._pcm_first_frame_at = None
        self._pcm_last_frame_at = None
        self._sink_failed = False

        # Reset queues
        self._pcm_queue = asyncio.Queue(maxsize=200)
        self._encoded_queue = asyncio.Queue(maxsize=200)
        self._flac_prebuffer.clear()
        self._flac_prebuffer_bytes = 0
        self._opus_prebuffer.clear()
        self._opus_prebuffer_bytes = 0
        self._opus_backend = "none"

        # mpv IPC socket path
        sock_id = self._sanitize_id(self._client_id)
        self._mpv_ipc_path = f"/tmp/lva_sendspin_mpv_{sock_id}.sock"
        self._mpv_ipc_ready = False
        try:
            if self._mpv_ipc_path and os.path.exists(self._mpv_ipc_path):
                os.remove(self._mpv_ipc_path)
        except Exception:
            pass

        # Spawn PCM sink (mpv)
        await self._spawn_pcm_sink_mpv(
            sample_rate=self._stream_rate,
            channels=self._stream_channels,
            bit_depth=self._stream_bit_depth,
        )

        self._stream_active = True

        # Start decoder for non-PCM codecs
        if self._stream_codec == "flac":
            await self._start_ffmpeg_decoder(input_codec="flac")
        elif self._stream_codec == "opus":
            await self._select_opus_backend()

        # Apply current audio state as soon as mpv IPC is available.
        self._loop.create_task(self._apply_mpv_audio_state())

        _LOGGER.info(
            "Sendspin: stream start codec=%s rate=%s ch=%s depth=%s",
            self._stream_codec,
            self._stream_rate,
            self._stream_channels,
            self._stream_bit_depth,
        )

    async def _stop_stream(self, *, reason: str) -> None:
        if not self._stream_active and not self._pcm_proc and not self._decoder_proc:
            return

        _LOGGER.info("Sendspin: stream stop (%s)", reason)

        self._stream_active = False

        # Stop decoder first (so its stdout reader stops feeding PCM).
        await self._stop_decoder()

        # Stop PCM sink
        await self._stop_pcm_sink()

        # Clear IPC socket
        try:
            if self._mpv_ipc_path and os.path.exists(self._mpv_ipc_path):
                os.remove(self._mpv_ipc_path)
        except Exception:
            pass
        self._mpv_ipc_ready = False

    # ---------------------------------------------------------------------
    # mpv raw PCM sink
    # ---------------------------------------------------------------------

    async def _spawn_pcm_sink_mpv(self, *, sample_rate: int, channels: int, bit_depth: int) -> None:
        mpv_path = shutil.which("mpv")
        if not mpv_path:
            raise RuntimeError("mpv not found in PATH")

        # mpv demuxer expects "rawaudio" with explicit format/rate/channels
        fmt = "s16le" if int(bit_depth) == 16 else "s32le"

        args = [
            mpv_path,
            "--no-terminal",
            "--really-quiet",
            "--idle=no",
            "--cache=no",
            "--demuxer=rawaudio",
            f"--demuxer-rawaudio-format={fmt}",
            f"--demuxer-rawaudio-rate={int(sample_rate)}",
            f"--demuxer-rawaudio-channels={int(channels)}",
            f"--input-ipc-server={self._mpv_ipc_path}",
            "fd://0",
        ]

        self._pcm_proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        self._pcm_writer_task = self._loop.create_task(self._pcm_writer_loop())
        self._pcm_stderr_task = self._loop.create_task(self._stderr_reader_loop(self._pcm_proc, name="mpv"))
        self._pcm_wait_task = self._loop.create_task(self._pcm_wait_loop())

    async def _stop_pcm_sink(self) -> None:
        proc = self._pcm_proc
        self._pcm_proc = None

        for t in (self._pcm_writer_task, self._pcm_wait_task, self._pcm_stderr_task):
            if t:
                t.cancel()

        self._pcm_writer_task = None
        self._pcm_wait_task = None
        self._pcm_stderr_task = None

        if not proc:
            return

        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            proc.terminate()
        except Exception:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    async def _pcm_writer_loop(self) -> None:
        assert self._pcm_proc is not None
        proc = self._pcm_proc
        assert proc.stdin is not None

        try:
            while not self._stop_event.is_set():
                chunk = await self._pcm_queue.get()
                if not chunk:
                    continue
                try:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    raise
                except Exception:
                    _LOGGER.debug("Sendspin: mpv stdin write error", exc_info=True)
                    raise
        except asyncio.CancelledError:
            return
        except Exception:
            # Any exception here indicates the sink is wedged.
            self._sink_failed = True
            self._disconnect_event.set()
            _LOGGER.warning("Sendspin: mpv sink writer failed; triggering reconnect")

    async def _stderr_reader_loop(self, proc: asyncio.subprocess.Process, *, name: str) -> None:
        if not proc.stderr:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore").rstrip()
                if text:
                    self._mpv_stderr_tail.append(text)
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: %s stderr reader error", name, exc_info=True)

    async def _pcm_wait_loop(self) -> None:
        proc = self._pcm_proc
        if not proc:
            return
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: mpv wait failed", exc_info=True)
            return

        # If mpv exits while a stream is active, it's unexpected.
        if self._stream_active and rc not in (0, None):
            self._sink_failed = True
            self._disconnect_event.set()
            tail = " | ".join(list(self._mpv_stderr_tail)[-10:])
            _LOGGER.warning("Sendspin: mpv exited unexpectedly rc=%s tail=%s", rc, tail)

    # ---------------------------------------------------------------------
    # Decoder helpers (ffmpeg)
    # ---------------------------------------------------------------------

    async def _select_opus_backend(self) -> None:
        # Prefer opuslib when available.
        if self._opus_available:
            try:
                import opuslib  # type: ignore

                self._opus_decoder = opuslib.Decoder(self._stream_rate, self._stream_channels)
                self._opus_max_frame_size = int(self._stream_rate * 0.12)  # 120ms
                self._opus_backend = "opuslib"
                _LOGGER.info("Sendspin: opus decode backend=opuslib")
                return
            except Exception:
                _LOGGER.debug("Sendspin: opuslib init failed", exc_info=True)

        # Fallback: ffmpeg if available.
        if self._ffmpeg_available:
            await self._start_ffmpeg_decoder(input_codec="opus")
            self._opus_backend = "ffmpeg"
            _LOGGER.info("Sendspin: opus decode backend=ffmpeg")
            return

        self._opus_backend = "none"
        _LOGGER.warning("Sendspin: no Opus decode backend available; dropping audio")

    async def _start_ffmpeg_decoder(self, *, input_codec: str) -> None:
        if not self._ffmpeg_available:
            raise RuntimeError("ffmpeg not available")

        # Feed encoded bytes on stdin; get PCM s16le on stdout.
        # NOTE: This assumes the incoming stream is a continuous bytestream that
        # ffmpeg can demux. MA's implementation should match this.
        args = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            str(self._stream_channels),
            "-ar",
            str(self._stream_rate),
            "pipe:1",
        ]

        self._decoder_proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._decoder_writer_task = self._loop.create_task(self._decoder_writer_loop())
        self._decoder_reader_task = self._loop.create_task(self._decoder_reader_loop())
        self._decoder_stderr_task = self._loop.create_task(self._stderr_reader_loop(self._decoder_proc, name="ffmpeg"))

    async def _stop_decoder(self) -> None:
        proc = self._decoder_proc
        self._decoder_proc = None

        for t in (self._decoder_writer_task, self._decoder_reader_task, self._decoder_stderr_task):
            if t:
                t.cancel()

        self._decoder_writer_task = None
        self._decoder_reader_task = None
        self._decoder_stderr_task = None

        if not proc:
            return

        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            proc.terminate()
        except Exception:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

        self._opus_decoder = None
        self._opus_backend = "none"

    async def _decoder_writer_loop(self) -> None:
        proc = self._decoder_proc
        if not proc or not proc.stdin:
            return

        try:
            while not self._stop_event.is_set() and self._stream_active:
                chunk = await self._encoded_queue.get()
                if not chunk:
                    continue
                try:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    raise
                except Exception:
                    _LOGGER.debug("Sendspin: decoder stdin write error", exc_info=True)
                    raise
        except asyncio.CancelledError:
            return
        except Exception:
            # Decoder failure does not necessarily require websocket reconnect,
            # but it does mean this stream is not playable.
            _LOGGER.warning("Sendspin: decoder writer failed")

    async def _decoder_reader_loop(self) -> None:
        proc = self._decoder_proc
        if not proc or not proc.stdout:
            return

        try:
            while not self._stop_event.is_set() and self._stream_active:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                try:
                    self._pcm_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    _LOGGER.debug("Sendspin: PCM queue full (decoder output); dropping")
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: decoder reader error", exc_info=True)

    async def _handle_encoded_payload(self, payload: bytes, *, codec: str) -> None:
        # For ffmpeg-backed codecs, route bytes to the decoder queue.
        if not self._decoder_proc:
            return
        try:
            self._encoded_queue.put_nowait(payload)
        except asyncio.QueueFull:
            _LOGGER.debug("Sendspin: encoded queue full; dropping %s payload (%d bytes)", codec, len(payload))

    async def _handle_opus_payload(self, payload: bytes) -> Optional[bytes]:
        if self._opus_backend == "none":
            return None

        if self._opus_backend == "opuslib" and self._opus_decoder is not None:
            try:
                pcm = self._opus_decoder.decode(payload, self._opus_max_frame_size, decode_fec=False)
                return pcm
            except Exception:
                _LOGGER.debug("Sendspin: opuslib decode failed", exc_info=True)
                return None

        if self._opus_backend == "ffmpeg":
            await self._handle_encoded_payload(payload, codec="opus")
            return None

        return None

