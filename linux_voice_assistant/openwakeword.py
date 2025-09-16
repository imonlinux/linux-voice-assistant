import json
import logging
import socket
import threading
from typing import Callable, Optional, List, Dict, Any
from urllib.parse import urlparse
from pathlib import Path
import time
import re

from .base_detector import BaseDetector, AvailableWakeWord

# Models that should NOT appear in UI dropdowns
EXCLUDED_OWW_MODELS = {"embedding_model", "melspectrogram"}

def _prettify_model_name(name: str) -> str:
    """
    Convert 'hey_jarvis_v0.1' -> 'Hey Jarvis', 'ok_nabu_v0.1' -> 'OK Nabu',
    'hal_v2' -> 'Hal', 'marvin_v2' -> 'Marvin'.
    """
    stem = Path(name).stem
    # Strip common version suffixes
    stem = re.sub(r"(?:_v\d+(?:\.\d+)?)$", "", stem)
    parts = stem.split("_")
    if parts and parts[0].lower() == "ok":
        parts[0] = "OK"
    else:
        parts = [p.capitalize() for p in parts]
    return " ".join(parts)


_LOGGER = logging.getLogger(__name__)


def _jsonl(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class WyomingWakeClient:
    """Minimal client for Wyoming wake detection (openWakeWord)."""

    def __init__(self, uri: str, rate=16000, width=2, channels=1):
        self.uri = uri
        self.rate, self.width, self.channels = rate, width, channels
        self._paused = False
        self._suppress_until_ms = 0
        self._refractory_ms = 0
        self._sock: Optional[socket.socket] = None
        self._reader: Optional[threading.Thread] = None
        self._on_detect: Optional[Callable[[str, Optional[int]], None]] = None
        self._on_models_listed: Optional[Callable[[List[Dict[str, Any]]], None]] = None
        self._closed = False
        # model discovery cache
        self._models_cache: List[Dict[str, Any]] = []
        self._info_event = threading.Event()

    def connect(self, on_detect: Callable[[str, Optional[int]], None], on_models_listed: Callable[[List[Dict[str, Any]]], None]) -> None:
        self._on_detect = on_detect
        self._on_models_listed = on_models_listed
        parsed = urlparse(self.uri)
        if parsed.scheme != "tcp":
            raise ValueError(f"Only tcp:// URIs are supported (got {self.uri})")
        self._sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 10400))
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Start audio stream
        self._send({"type": "audio-start", "data": {"rate": self.rate, "width": self.width, "channels": self.channels}})

        # Ask for model info
        try:
            self._send({"type": "describe"})
        except Exception:
            pass
        
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        _LOGGER.debug("Connected to Wyoming wake server at %s", self.uri)

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

    def send_audio_chunk(self, pcm: bytes, timestamp_ms: Optional[int] = None) -> None:
        if self._sock is None or self._closed:
            return
        hdr = {"type": "audio-chunk",
               "data": {"rate": self.rate, "width": self.width, "channels": self.channels},
               "payload_length": len(pcm)}
        if timestamp_ms is not None:
            hdr["data"]["timestamp"] = timestamp_ms
        self._sock.sendall(_jsonl(hdr))
        self._sock.sendall(pcm)

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def suppress(self, ms: int) -> None:
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            now_ms = 0
        self._suppress_until_ms = max(self._suppress_until_ms, now_ms + int(ms))

    def set_detect(self, names: List[str]) -> None:
        if self._sock is None:
            raise RuntimeError("Not connected")
        filtered = [n for n in list(names) if n]
        if not filtered:
            _LOGGER.warning("Ignoring empty detect() request (no model names)")
            return
        _LOGGER.debug("Requesting Wyoming detect models: %s", filtered)
        self._send({"type": "detect", "data": {"names": filtered}})


    # internals
    def _send(self, obj: dict) -> None:
        assert self._sock is not None
        self._sock.sendall(_jsonl(obj))

    def _read_loop(self) -> None:
        assert self._sock is not None
        f = self._sock.makefile("rb")
        buf = ""  # text buffer for concatenated JSON objects

        def _yield_objects_from_text(text: str):
            """Yield JSON objects from `text` even if they are concatenated like {}{}..."""
            start = 0
            depth = 0
            in_str = False
            escape = False
            for i, ch in enumerate(text):
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == "{":
                        if depth == 0:
                            start = i
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            # complete object from start..i
                            yield text[start : i + 1], i + 1
            # return here with any remainder unparsed
            return

        while not self._closed:
            chunk = f.readline()
            if not chunk:
                break

            try:
                piece = chunk.decode("utf-8")
            except Exception:
                try:
                    _LOGGER.debug("Wyoming raw (non-utf8): %r", chunk[:200])
                except Exception:
                    pass
                continue

            buf += piece

            # Try to peel off as many complete JSON objects as we can
            pos = 0
            progressed = True
            while progressed and pos < len(buf):
                progressed = False
                for obj_text, end_pos in _yield_objects_from_text(buf[pos:]):
                    progressed = True
                    pos += end_pos
                    try:
                        msg = json.loads(obj_text)
                    except Exception:
                        try:
                            _LOGGER.debug("Wyoming raw (bad-json): %r", obj_text[:200])
                        except Exception:
                            pass
                        continue

                    mtype = str(msg.get("type") or "").lower()
                    try:
                        _LOGGER.debug("Received Wyoming message type=%s", mtype or "<?>")
                        if mtype and mtype not in ("info", "describe"):
                            _LOGGER.debug("Wyoming raw: %r", obj_text[:200])
                    except Exception:
                        pass

                    # ----- model info path -----
                    if mtype in ("info", "describe"):
                        data_len = int(msg["data_length"])
                        payload = f.read(data_len).decode("utf-8")

                        try:
                            data = json.loads(payload)
                        except Exception:
                            _LOGGER.exception("Bad JSON in Wyoming info payload")
                            continue

                        wake = data.get("wake", [])

                        if wake and isinstance(wake, list):
                            models = wake[0].get("models", [])
                            norm = []
                            for m in models:
                                if isinstance(m, str):
                                    norm.append({"name": m})
                                elif isinstance(m, dict):
                                    norm.append(m)

                            if norm:
                                self._models_cache = norm
                                self._info_event.set()

                                if self._on_models_listed:
                                    self._on_models_listed(norm)
                        continue

                    if mtype == "detection":
                        data_len = int(msg["data_length"])
                        payload = f.read(data_len).decode("utf-8")

                        try:
                            data = json.loads(payload)
                        except Exception:
                            _LOGGER.exception("Bad JSON in Wyoming info payload")
                            continue

                        name = data.get("name") or data.get("model") or data.get("wakeword")
                        ts = data.get("timestamp") or data.get("time") or data.get("ts")
                        _LOGGER.debug("Wake detected: %s ts=%s", name, ts)

                        if self._on_detect:
                            try:
                                now_ms = int(time.time() * 1000)
                                if self._paused or (self._suppress_until_ms and now_ms < self._suppress_until_ms):
                                    _LOGGER.debug("Detection suppressed (paused=%s until=%s)", self._paused, getattr(self, '_suppress_until_ms', 0))
                                else:
                                    now_ms = int(time.time() * 1000)
                                if self._paused or (self._suppress_until_ms and now_ms < self._suppress_until_ms):
                                    _LOGGER.debug("Detection suppressed (paused=%s until=%s)", self._paused, getattr(self, '_suppress_until_ms', 0))
                                else:
                                    self._on_detect(name, ts)
                                    if getattr(self, '_refractory_ms', 0):
                                        self.suppress(self._refractory_ms)
                            except Exception:
                                _LOGGER.exception("Error in detection callback")

            # keep any unconsumed remainder in buffer
            buf = buf[pos:]

class OpenWakeWordDetector(BaseDetector):
    """OpenWakeWord detector implementation using Wyoming protocol."""
    
    def _initialize(self, **kwargs) -> None:
        """Initialize OpenWakeWord-specific setup."""
        self.wake_uri = kwargs.get('wake_uri')
        if not self.wake_uri:
            raise ValueError("wake_uri is required for OpenWakeWord")
        
        self._wyoming_client = WyomingWakeClient(self.wake_uri)
        self.stop_active = False
    
    def connect_if_needed(self, on_detect: Callable[[str, Optional[int]], None]) -> None:
        """Connect to Wyoming wake server."""        
        def on_models_listed(models: List[Dict[str, Any]]):
            self.available_wake_words.clear()

            for m in models:
                name = (m.get('name') or '').strip()
                langs = m.get('languages') or m.get('trained_languages') or []
                if not name:
                    continue
                label = m.get('label') or _prettify_model_name(name)
                self.available_wake_words[name] = AvailableWakeWord(
                    id=name,
                    wake_word=label,
                    trained_languages=langs,
                    config_path=Path(f"wyoming:{name}"),
                )

            wake_word_keys = self.available_wake_words.keys()
            _LOGGER.debug("Wyoming model list updated: %s", [m for m in wake_word_keys])
            
            wake_id = self._resolve_model_id(self.wake_model_id)
            self.wake_model_id = wake_id if wake_id else next(iter(wake_word_keys))

            stop_id = self._resolve_model_id(self.stop_model_id)
            self.stop_model_id = stop_id if stop_id else next(iter(wake_word_keys))

            try:
                self._wyoming_client.set_detect([self.wake_model_id, self.stop_model_id])
            except Exception as e:
                _LOGGER.exception("Failed to set initial detection target: %s", e)
        
        self._wyoming_client.connect(on_detect, on_models_listed)
    
    def process_audio(self, audio_chunk: bytes) -> tuple[bool, bool]:
        """Process audio chunk by sending to Wyoming server."""
        if self._wyoming_client:
            try:
                self._wyoming_client.send_audio_chunk(audio_chunk)
            except Exception as e:
                _LOGGER.exception("Error sending audio to Wyoming server: %s", e)
        
        # OpenWakeWord detection happens asynchronously via callback
        return False, False
    
    def set_wake_model(self, wake_word_id: str) -> bool:
        """Set the active wake word model on Wyoming server."""        
        if wake_word_id not in self.available_wake_words:
            _LOGGER.warning("Wake model not found: %s", wake_word_id)
            return False
        
        if not self._wyoming_client:
            _LOGGER.warning("Wyoming client not connected")
            return False
        
        try:
            self._wyoming_client.set_detect([wake_word_id, self.stop_model_id])
            self.wake_model_id = wake_word_id
            _LOGGER.info("Switched Wyoming wake model to: %s", wake_word_id)
            return True
        except Exception as e:
            _LOGGER.error("Failed to switch Wyoming wake model: %s", e)
            return False
        
    def _resolve_model_id(self, model_id: str):
        if not model_id:
            return None

        # Direct match
        if model_id in self.available_wake_words:
            return model_id

        # Try matching without version suffix
        base = model_id.split("_v")[0]
        for key in self.available_wake_words:
            if key == base or key.startswith(base + "_v"):
                return key

        return None