"""Sendspin playback pipeline (stream lifecycle + mpv/decoder management).

Milestone 5 extraction:
- Move mpv sink lifecycle, decoder lifecycle (ffmpeg + opuslib), and binary frame
  routing into a focused module.
- Keep behavior identical to the previous monolithic implementation.

Clock-sync / multi-room enhancement:
- Sendspin binary audio frames include a server timestamp (us) for the first sample.
- This pipeline can accept a clock-sync mapping (server -> local) and schedule
  PCM writes to mpv so multiple clients align to the server timeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import time
from collections import deque
from typing import Any, Deque, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

_BINARY_HEADER_LEN = 9  # 1 byte header + 8 byte server timestamp (us)


def _now_us() -> int:
    return int(time.monotonic_ns() // 1_000)


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

        # Clock sync (optional)
        self._clock: Any = None

        # Scheduling config
        player_cfg = self._cfg_get_section(self._cfg, "player")
        # Add a constant playout delay to build a jitter buffer. Same value on all clients preserves sync.
        self._sync_target_latency_ms: int = int(self._cfg_get(player_cfg, "sync_target_latency_ms", 250) or 250)
        # Drop PCM chunks if they arrive too late (relative to due time).
        self._sync_late_drop_ms: int = int(self._cfg_get(player_cfg, "sync_late_drop_ms", 150) or 150)
        # Static output timing adjustment (ms).
        #
        # This is the LVA analogue to sendspin-cli's `--static-delay-ms`.
        # Based on practical testing, values are often *negative* (e.g. -100 or -150)
        # to compensate for fixed buffering in the audio output stack.
        #
        # Convention:
        #   +N => play later by N ms
        #   -N => play earlier by N ms
        self._output_latency_ms: int = int(self._cfg_get(player_cfg, "output_latency_ms", 0) or 0)

        # After a stream/clear, we can optionally drop "stale" in-flight audio
        # for a short window. This helps avoid playing pre-seek tail audio that
        # arrives late after the server requested a clear.
        self._clear_drop_window_ms: int = int(self._cfg_get(player_cfg, "clear_drop_window_ms", 200) or 200)
        self._clear_drop_window_us: int = max(0, self._clear_drop_window_ms * 1000)

        # Clear semantics:
        # - We implement Option A (drop queued audio only) for stream/clear.
        # - We maintain an epoch counter that tags queued audio.
        #   When the epoch changes, the writer/decoder ignore old-epoch items.
        self._clear_epoch: int = 0
        self._clear_drop_until_us: int = 0
        self._clear_cutoff_due_us: int = 0

        self._sync_target_latency_us = max(0, self._sync_target_latency_ms * 1000)
        self._sync_late_drop_us = max(0, self._sync_late_drop_ms * 1000)
        # IMPORTANT: allow negative values (earlier playout) to match sendspin-cli behavior.
        self._output_latency_us = int(self._output_latency_ms * 1000)

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

        # Queue items are (epoch, due_local_us, pcm_bytes)
        self._pcm_queue: "asyncio.Queue[Tuple[int, int, bytes]]" = asyncio.Queue(maxsize=500)

        # Decoder / transcoder state (for non-PCM codecs)
        self._decoder_proc: Optional[asyncio.subprocess.Process] = None
        self._decoder_writer_task: Optional[asyncio.Task] = None
        self._decoder_reader_task: Optional[asyncio.Task] = None
        self._decoder_stderr_task: Optional[asyncio.Task] = None
        # Encoded queue items are (epoch, payload_bytes)
        self._encoded_queue: "asyncio.Queue[Tuple[int, bytes]]" = asyncio.Queue(maxsize=400)

        # Timestamp anchors for ffmpeg decoded output: (epoch, server_ts_us)
        self._encoded_ts_queue: Deque[Tuple[int, int]] = deque()
        self._decoder_due_us: Optional[float] = None
        self._decoder_due_frac: float = 0.0  # fractional carry

        # Count binary frames received while stream inactive (diagnostics only)
        self._inactive_bin_count: int = 0

        # Opus decoder backend selection
        self._opus_backend: str = "none"  # none|opuslib|ffmpeg
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

    def set_clock_sync(self, clock: Any) -> None:
        """Provide a clock sync object with server_to_local(server_us)->local_us and is_synced."""
        self._clock = clock

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

    async def clear_buffer(self) -> None:
        """Handle `stream/clear`: drop queued audio without restarting the sink.

        This keeps the mpv process alive (Option A) but clears all queued in-memory
        audio and resets decoder scheduling anchors.
        """
        async with self._stream_lock:
            await self._clear_buffer_locked()

    async def handle_binary_frame(self, frame: bytes) -> None:
        """Handle a binary audio frame from the websocket."""
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

        server_ts_us, payload_bytes = self._extract_server_ts_and_payload(frame)
        if not payload_bytes:
            return

        # Decode / route based on negotiated codec
        if self._stream_codec == "pcm":
            await self._enqueue_pcm(payload_bytes, server_ts_us=server_ts_us)
            return

        if self._stream_codec == "opus":
            pcm = await self._handle_opus_payload(payload_bytes)
            if pcm is None:
                # ffmpeg-backed opus path uses encoded queue + decoder timestamps
                if self._opus_backend == "ffmpeg":
                    await self._handle_encoded_payload(payload_bytes, codec="opus", server_ts_us=server_ts_us)
                return
            await self._enqueue_pcm(pcm, server_ts_us=server_ts_us)
            return

        if self._stream_codec == "flac":
            await self._handle_encoded_payload(payload_bytes, codec="flac", server_ts_us=server_ts_us)
            return

    async def shutdown(self) -> None:
        """Best-effort shutdown of the pipeline."""
        await self.stop_stream(reason="shutdown")

    # ---------------------------------------------------------------------
    # stream/clear implementation (Option A)
    # ---------------------------------------------------------------------

    @staticmethod
    def _drain_queue(q: asyncio.Queue) -> None:
        """Best-effort drain of an asyncio.Queue without blocking."""
        try:
            while True:
                q.get_nowait()
        except asyncio.QueueEmpty:
            return

    async def _clear_buffer_locked(self) -> None:
        """Clear buffered audio without restarting mpv/decoder.

        Caller must hold `_stream_lock`.
        """
        # Advance epoch so any already-dequeued items are ignored by writer/decoder loops.
        self._clear_epoch += 1

        now_us = _now_us()
        if self._clear_drop_window_us > 0:
            self._clear_cutoff_due_us = int(now_us)
            self._clear_drop_until_us = int(now_us + self._clear_drop_window_us)
        else:
            self._clear_cutoff_due_us = 0
            self._clear_drop_until_us = 0

        # Drain in-memory queues.
        self._drain_queue(self._pcm_queue)
        self._drain_queue(self._encoded_queue)
        self._encoded_ts_queue.clear()

        # Reset decoder scheduling anchors so next decoded bytes re-anchor cleanly.
        self._decoder_due_us = None
        self._decoder_due_frac = 0.0

        _LOGGER.debug("Sendspin: stream/clear applied (epoch=%d)", int(self._clear_epoch))

    # ---------------------------------------------------------------------
    # Timestamp extraction / scheduling
    # ---------------------------------------------------------------------

    @staticmethod
    def _extract_server_ts_and_payload(frame: bytes) -> Tuple[int, bytes]:
        """
        Binary frame header (spec):
          - 1 byte type/flags
          - 8 bytes server timestamp (us), big-endian
          - remaining bytes: codec payload

        If timestamp is absent/zero, returns 0.
        """
        if len(frame) <= _BINARY_HEADER_LEN:
            return 0, b""
        try:
            ts = int.from_bytes(frame[1:9], byteorder="big", signed=False)
        except Exception:
            ts = 0
        payload = frame[_BINARY_HEADER_LEN:]
        return ts, payload

    def _server_to_local_due_us(self, server_ts_us: int) -> int:
        """Compute the local monotonic time (us) when this chunk should be played.

        This is where multi-room sync happens.

        Key behavior:
        - Always applies `sync_target_latency_ms` and `output_latency_ms`, even if
          clock sync is not yet "perfect". This prevents the "play ASAP when not
          synced" behavior that can make this client appear ahead/behind others.
        - Uses the clock sync mapping as soon as it has *any* samples.
        - Includes a conservative clamp to avoid pathological due-times if a
          timestamp glitch occurs, but allows multi-second *intentional* offsets.
        """
        now = _now_us()

        # Even without timestamps or a clock, honor configured latency knobs.
        fallback_due = now + int(self._sync_target_latency_us) + int(self._output_latency_us)

        if server_ts_us <= 0:
            return fallback_due

        clk = self._clock
        if clk is None:
            return fallback_due

        try:
            samples = int(getattr(clk, "samples", 0) or 0)
            if samples <= 0:
                return fallback_due

            local_base = int(clk.server_to_local(int(server_ts_us)))
            due = local_base + int(self._sync_target_latency_us) + int(self._output_latency_us)

            # Clamp to keep scheduling sane if the timestamp basis changes or glitches.
            # Allow multi-second intentional delays (e.g., +5000ms) and typical
            # negative static delays (e.g., -100..-150ms). Also allow larger
            # calibrations (seconds) if the local audio pipeline is very buffered.
            max_future_us = 30_000_000  # 30s into the future
            max_past_us = 10_000_000    # 10s into the past (lets output_latency_ms tune in seconds)

            if due > now + max_future_us:
                _LOGGER.debug("Sendspin: due time clamped (too far future, %+dus)", due - now)
                return now + max_future_us
            if due < now - max_past_us:
                _LOGGER.debug("Sendspin: due time clamped (too far past, %+dus)", due - now)
                return now - max_past_us

            return due
        except Exception:
            return fallback_due

    async def _enqueue_pcm(self, pcm: bytes, *, server_ts_us: int) -> None:
        if not pcm:
            return

        due_us = self._server_to_local_due_us(server_ts_us)
        now_us = _now_us()

        # After a stream/clear, drop in-flight chunks that would play "before" the clear.
        if self._clear_drop_until_us and now_us < self._clear_drop_until_us:
            if due_us < self._clear_cutoff_due_us:
                return

        # Drop if already too late (helps prevent “catch up” bursts)
        if due_us + self._sync_late_drop_us < now_us:
            _LOGGER.debug("Sendspin: dropping late PCM chunk (late_by=%dus)", now_us - due_us)
            return

        self._pcm_frame_count += 1
        self._pcm_last_frame_at = time.monotonic()
        if self._pcm_first_frame_at is None:
            self._pcm_first_frame_at = self._pcm_last_frame_at
            _LOGGER.info("Sendspin: first PCM frame received (%d bytes)", len(pcm))

        if (self._pcm_frame_count % 500) == 0:
            _LOGGER.debug(
                "Sendspin: PCM frames received=%d (last_chunk=%d bytes)",
                self._pcm_frame_count,
                len(pcm),
            )

        try:
            epoch = int(self._clear_epoch)
            self._pcm_queue.put_nowait((epoch, due_us, pcm))
        except asyncio.QueueFull:
            _LOGGER.debug("Sendspin: PCM queue full; dropping frame (%d bytes)", len(pcm))

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

        # Reset queues / timestamp mapping
        self._pcm_queue = asyncio.Queue(maxsize=500)
        self._encoded_queue = asyncio.Queue(maxsize=400)
        self._encoded_ts_queue.clear()
        self._decoder_due_us = None
        self._decoder_due_frac = 0.0
        self._opus_backend = "none"

        # Reset clear epoch/window for a fresh stream.
        self._clear_epoch = 0
        self._clear_drop_until_us = 0
        self._clear_cutoff_due_us = 0

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
            "Sendspin: stream start codec=%s rate=%s ch=%s depth=%s (sync_latency_ms=%d output_latency_ms=%d late_drop_ms=%d)",
            self._stream_codec,
            self._stream_rate,
            self._stream_channels,
            self._stream_bit_depth,
            self._sync_target_latency_ms,
            self._output_latency_ms,
            self._sync_late_drop_ms,
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

        # Clear timestamp mapping
        self._encoded_ts_queue.clear()
        self._decoder_due_us = None
        self._decoder_due_frac = 0.0

    # ---------------------------------------------------------------------
    # mpv raw PCM sink
    # ---------------------------------------------------------------------

    async def _spawn_pcm_sink_mpv(self, *, sample_rate: int, channels: int, bit_depth: int) -> None:
        mpv_path = shutil.which("mpv")
        if not mpv_path:
            raise RuntimeError("mpv not found in PATH")

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
                epoch, due_us, chunk = await self._pcm_queue.get()
                if not chunk:
                    continue

                # If a clear occurred after this chunk was queued, drop it.
                if int(epoch) != int(self._clear_epoch):
                    continue

                # Pacing: wait until due time
                now_us = _now_us()
                if due_us > now_us:
                    await asyncio.sleep((due_us - now_us) / 1_000_000.0)
                else:
                    # Late: drop if too late
                    now2 = _now_us()
                    if due_us + self._sync_late_drop_us < now2:
                        continue

                # Re-check epoch after sleeping. A clear can happen while we wait.
                if int(epoch) != int(self._clear_epoch):
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

        if self._stream_active and rc not in (0, None):
            self._sink_failed = True
            self._disconnect_event.set()
            tail = " | ".join(list(self._mpv_stderr_tail)[-10:])
            _LOGGER.warning("Sendspin: mpv exited unexpectedly rc=%s tail=%s", rc, tail)

    # ---------------------------------------------------------------------
    # Decoder helpers (ffmpeg)
    # ---------------------------------------------------------------------

    async def _select_opus_backend(self) -> None:
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
                epoch, chunk = await self._encoded_queue.get()
                if not chunk:
                    continue

                # If a clear occurred after this payload was queued, drop it.
                if int(epoch) != int(self._clear_epoch):
                    continue

                try:
                    # Re-check epoch right before write to reduce race window.
                    if int(epoch) != int(self._clear_epoch):
                        continue
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
            _LOGGER.warning("Sendspin: decoder writer failed")

    def _pcm_bytes_to_duration_us(self, pcm_bytes: int) -> float:
        bps = 2 if int(self._stream_bit_depth) == 16 else 4
        frame_bytes = bps * max(1, int(self._stream_channels))
        if frame_bytes <= 0:
            return 0.0
        samples = pcm_bytes / float(frame_bytes)
        rate = float(max(1, int(self._stream_rate)))
        return (samples / rate) * 1_000_000.0

    async def _decoder_reader_loop(self) -> None:
        proc = self._decoder_proc
        if not proc or not proc.stdout:
            return

        try:
            while not self._stop_event.is_set() and self._stream_active:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break

                # Establish / advance due time using encoded timestamp anchors
                if self._decoder_due_us is None:
                    # Drop stale anchors from previous clear epochs.
                    while self._encoded_ts_queue and self._encoded_ts_queue[0][0] != int(self._clear_epoch):
                        self._encoded_ts_queue.popleft()

                    if self._encoded_ts_queue:
                        _epoch, anchor_server_ts = self._encoded_ts_queue.popleft()
                        self._decoder_due_us = float(self._server_to_local_due_us(anchor_server_ts))
                        self._decoder_due_frac = 0.0
                    else:
                        self._decoder_due_us = float(_now_us())
                        self._decoder_due_frac = 0.0

                due_us_int = int(self._decoder_due_us)

                try:
                    epoch = int(self._clear_epoch)
                    self._pcm_queue.put_nowait((epoch, due_us_int, chunk))
                except asyncio.QueueFull:
                    _LOGGER.debug("Sendspin: PCM queue full (decoder output); dropping")
                    # Still advance timing
                    pass

                dur_us = self._pcm_bytes_to_duration_us(len(chunk))
                self._decoder_due_us = float(self._decoder_due_us) + dur_us

                # If we have additional anchors, and drifted far, snap gently (keeps long FLAC streams aligned)
                # Drop stale anchors from previous clear epochs.
                while self._encoded_ts_queue and self._encoded_ts_queue[0][0] != int(self._clear_epoch):
                    self._encoded_ts_queue.popleft()

                if self._encoded_ts_queue:
                    _epoch2, next_anchor_server_ts = self._encoded_ts_queue[0]
                    next_anchor_local = self._server_to_local_due_us(next_anchor_server_ts)
                    # If our predicted timeline is >200ms away from anchor, snap to anchor.
                    if abs(next_anchor_local - int(self._decoder_due_us)) > 200_000:
                        self._decoder_due_us = float(next_anchor_local)
                        self._decoder_due_frac = 0.0

        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.debug("Sendspin: decoder reader error", exc_info=True)

    async def _handle_encoded_payload(self, payload: bytes, *, codec: str, server_ts_us: int) -> None:
        if not self._decoder_proc:
            return

        # Drop stale in-flight payloads shortly after a clear.
        if self._clear_drop_until_us and _now_us() < self._clear_drop_until_us and server_ts_us > 0:
            due_us = self._server_to_local_due_us(server_ts_us)
            if due_us < self._clear_cutoff_due_us:
                return

        epoch = int(self._clear_epoch)

        if server_ts_us > 0:
            self._encoded_ts_queue.append((epoch, int(server_ts_us)))

        try:
            self._encoded_queue.put_nowait((epoch, payload))
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
            return None

        return None
