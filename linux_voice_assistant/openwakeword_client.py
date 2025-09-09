import json
import logging
import socket
import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)

def _jsonl(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")

class WyomingWakeClient:
    """Minimal client for Wyoming wake detection (openWakeWord) with extra debug."""

    def __init__(self, uri: str, name: str, rate=16000, width=2, channels=1):
        self.uri, self.name = uri, name
        self.rate, self.width, self.channels = rate, width, channels
        self._sock: Optional[socket.socket] = None
        self._reader: Optional[threading.Thread] = None
        self._on_detect: Optional[Callable[[str, Optional[int]], None]] = None
        self._closed = False
        self._chunks = 0
        self._last_log_time = 0.0

    def connect(self, on_detect: Callable[[str, Optional[int]], None]) -> None:
        self._on_detect = on_detect
        parsed = urlparse(self.uri)
        if parsed.scheme != "tcp":
            raise ValueError(f"Only tcp:// URIs are supported (got {self.uri})")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 10400
        _LOGGER.debug("[OWW] Connecting to %s:%s", host, port)
        self._sock = socket.create_connection((host, port))
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._send({ "type": "audio-start", "data": { "rate": self.rate, "width": self.width, "channels": self.channels }})
        self._arm_detect()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        _LOGGER.info("[OWW] Connected to Wyoming wake server at %s", self.uri)

    def close(self) -> None:
        self._closed = True
        try:
            self._send({"type": "audio-stop"})
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        finally:
            self._sock = None

    def _arm_detect(self) -> None:
        _LOGGER.debug("[OWW] Arming detect for name=%s", self.name)
        self._send({"type": "detect", "data": {"names": [self.name]}})

    def send_audio_chunk(self, pcm: bytes, timestamp_ms: Optional[int] = None) -> None:
        if self._sock is None or self._closed:
            return
        hdr = {
            "type": "audio-chunk",
            "data": {"rate": self.rate, "width": self.width, "channels": self.channels},
            "payload_length": len(pcm),
        }
        if timestamp_ms is not None:
            hdr["data"]["timestamp"] = timestamp_ms
        try:
            self._sock.sendall(_jsonl(hdr))
            self._sock.sendall(pcm)
        except Exception:
            _LOGGER.exception("[OWW] Failed to send audio chunk")
            return

        self._chunks += 1
        if (self._chunks % 1000) == 0:
            # ~64ms * 1000 = ~64s of audio
            _LOGGER.debug("[OWW] Sent %d chunks (~%.1fs)", self._chunks, self._chunks * 0.064)

    # internals
    def _send(self, obj: dict) -> None:
        assert self._sock is not None
        try:
            self._sock.sendall(_jsonl(obj))
        except Exception:
            _LOGGER.exception("[OWW] send failed (%s)", obj.get("type"))

    def _cycle_stream(self) -> None:
        """Some servers require restarting the audio stream after a detection."""
        _LOGGER.debug("[OWW] Cycling audio stream")
        try:
            self._send({"type": "audio-stop"})
        except Exception:
            _LOGGER.exception("[OWW] audio-stop failed")
        try:
            self._send({"type": "audio-start", "data": {"rate": self.rate, "width": self.width, "channels": self.channels}})
        except Exception:
            _LOGGER.exception("[OWW] audio-start failed")

    def _read_loop(self) -> None:
        assert self._sock is not None
        f = self._sock.makefile("rb")
        _LOGGER.debug("[OWW] Reader loop started")
        buf = b""
        decoder = json.JSONDecoder()
        while not self._closed:
            chunk = f.readline()
            if not chunk:
                _LOGGER.debug("[OWW] Reader EOF")
                break
            buf += chunk
            try:
                # There might be multiple JSON objects in one line; parse until exhausted.
                while buf:
                    s = buf.decode("utf-8")
                    obj, idx = decoder.raw_decode(s)
                    buf = s[idx:].lstrip().encode("utf-8")
                    mtype = obj.get("type")
                    if mtype and mtype != "detection":
                        _LOGGER.debug("[OWW] recv %s", mtype)
                    if mtype == "detection":
                        data = obj.get("data", {}) or {}
                        name = data.get("name") or self.name
                        ts = data.get("timestamp")
                        _LOGGER.info("[OWW] Detection: name=%s ts=%s (chunks=%d)", name, ts, self._chunks)
                        if self._on_detect:
                            try:
                                self._on_detect(name, ts)
                            except Exception:
                                _LOGGER.exception("[OWW] Error in detection callback")
                        # Some servers need an audio stream restart before re-arming
                        try:
                            self._cycle_stream()
                        except Exception:
                            _LOGGER.exception("[OWW] Failed to cycle stream")
                        # Re-arm detect so subsequent detections work
                        try:
                            self._arm_detect()
                        except Exception:
                            _LOGGER.exception("[OWW] Failed to re-arm detect")
            except json.JSONDecodeError:
                # Need more bytes; continue accumulating
                pass
