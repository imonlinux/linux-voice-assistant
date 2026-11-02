#!/usr/bin/env python3
"""Wake word arbitration (fork).

When several voice satellites hear the same wake word, the closest one
should answer — not the fastest one. Each device that detects the wake
word broadcasts a claim carrying how loud the wake word reached it over
its own noise floor, holds for a short window, and only answers when no
rival heard it louder. A claim that never arrives changes nothing: both
devices answer, which is the pre-arbitration behavior.

The wire protocol is shared with other satellites on the network
(UDP port 2330, JSON envelopes), so arbitration settles across device
kinds, not just within a fleet of identical hardware. The distance
metric — dB over the receiver's own noise floor — cancels microphone
gain: a device with a hot mic hears the room louder too, so raw level
would always win. Levels are measured before WebRTC AGC/noise
suppression for the same reason: the suppressor silences the noise
floor and would let a suppressed device win from across the room.

This is an independent implementation of the documented protocol facts
(port, envelope, decision rule); no code was ported from any other
project. See docs/wake-word-arbitration.md for the protocol contract.
"""

import json
import logging
import math
import random
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)

# --- Wire protocol constants -------------------------------------------------

ARBITRATION_PORT = 2330
_PROTOCOL_NAME = "wake"  # envelope "ks" field
_PROTOCOL_VERSION = 1
_MAX_ID_LENGTH = 64

# Claims are older than any window after this; rivals must arrive within
# +-window of the own detection, so nothing older can matter.
_CLAIM_KEEP_MS = 2000

# How often a device announces itself, so the others can address claims
# to it (unicast survives Wi-Fi power save better than broadcast) before
# it has claimed anything itself.
_HELLO_INTERVAL_S = 300
# A peer not heard from in this long is no longer addressed directly.
_PEER_TIMEOUT_MS = 30 * 60 * 1000

# Broadcast is never acknowledged, so each claim also goes out twice more,
# plus addressed copies to every known peer: Wi-Fi acknowledges unicast
# and an access point hands it to a sleeping device sooner.
_CLAIM_REPEAT_DELAYS_MS = (15, 30)

# --- Energy metric constants -------------------------------------------------

_FRAME_SAMPLES = 320  # 20 ms at 16 kHz
# How much recent audio the metric reads: the wake word plus the quiet
# before it, which is where the noise floor comes from.
_LEVELS_WINDOW_FRAMES = 150  # 3 s
# Speech level: average of the loudest 10 of the last 75 frames (the wake
# word, whichever engine fired and however late).
_SPEECH_FRAMES = 10
_SPEECH_LOOKBACK_FRAMES = 75
_FLOOR_PERCENTILE = 0.2
_FLOOR_MIN_DB = -75.0  # clamp: a noise suppressor cannot claim endless margin
_SILENCE_DB = -120.0
_MIN_FRAMES = 10


def frame_level_db(frame: np.ndarray) -> float:
    """dBFS of one 20 ms float frame."""
    rms = math.sqrt(float(np.mean(np.square(frame, dtype=np.float64))))
    return 20.0 * math.log10(rms) if rms > 0.0 else _SILENCE_DB


def energy_from_levels(levels: List[float]) -> Optional[float]:
    """How loud the wake word reached this device, dB over its noise floor.

    ``levels`` are dBFS per 20 ms frame, oldest first, from before any
    gain or suppression. ``None`` when there is too little audio.
    """
    count = len(levels)
    if count < _MIN_FRAMES:
        return None
    recent = sorted(levels[max(0, count - _SPEECH_LOOKBACK_FRAMES) :], reverse=True)
    loud = recent[:_SPEECH_FRAMES]
    speech = sum(loud) / len(loud)
    ordered = sorted(levels)
    floor = max(ordered[int(count * _FLOOR_PERCENTILE)], _FLOOR_MIN_DB)
    return speech - floor


def round_energy(value: float) -> float:
    """Round to 0.1 dB, halves away from zero.

    Both sides must compare the very numbers that went on the wire, so
    the rounding has to be identical everywhere; halves away from zero
    is what the other device kind uses.
    """
    scaled = value * 10.0
    rounded = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
    return rounded / 10.0


def normalize_phrase(phrase: str) -> str:
    """Lowercase, trimmed, whitespace collapsed — the claim's phrase key."""
    return " ".join(phrase.split()).lower()


def _now_ms() -> int:
    return time.monotonic_ns() // 1_000_000


# --- Transport ---------------------------------------------------------------


class ArbitrationTransport:
    """Carries claims between devices. UDP on the real network; tests hand
    in a fake."""

    def open(self, on_packet: Callable[[bytes, str], None]) -> bool:
        raise NotImplementedError

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    def send_to(self, data: bytes, address: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class UdpArbitrationTransport(ArbitrationTransport):
    """One datagram to the whole subnet per claim, on a port of its own: no
    roster, no pairing and nothing else on the device has to be turned on."""

    def __init__(self, port: int = ARBITRATION_PORT) -> None:
        self.port = port
        self._socket: Optional[socket.socket] = None

    def open(self, on_packet: Callable[[bytes, str], None]) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.5)
        except OSError as err:
            sock.close()
            _LOGGER.warning("Wake arbitration cannot listen on UDP %d: %s", self.port, err)
            return False
        self._socket = sock
        threading.Thread(target=self._receive_loop, args=(sock, on_packet), daemon=True, name="wake-arbitration-rx").start()
        return True

    def _receive_loop(self, sock: socket.socket, on_packet: Callable[[bytes, str], None]) -> None:
        while self._socket is sock:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            on_packet(data, addr[0])

    def send(self, data: bytes) -> None:
        sock = self._socket
        if sock is None:
            return
        try:
            sock.sendto(data, ("255.255.255.255", self.port))
        except OSError as err:
            _LOGGER.debug("Wake arbitration broadcast failed: %s", err)

    def send_to(self, data: bytes, address: str) -> None:
        sock = self._socket
        if sock is None:
            return
        try:
            sock.sendto(data, (address, self.port))
        except OSError as err:
            _LOGGER.debug("Wake arbitration send to %s failed: %s", address, err)

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()


# --- Messages ----------------------------------------------------------------


@dataclass
class _Claim:
    id: str
    phrase: str
    energy: float
    from_addr: str
    at: int  # local monotonic ms of the first copy's arrival


@dataclass
class ArbitrationResult:
    won: bool
    rivals: int
    winner: Optional[str] = None  # address of the rival that beat this device
    winner_energy: Optional[float] = None
    phrase: str = ""
    energy: float = 0.0
    claimed_at: int = 0
    heard: List[str] = field(default_factory=list)  # per-rival log lines


@dataclass
class LateClaim:
    """A louder claim that arrived after this device had decided."""

    from_addr: str
    energy: float


# --- Arbiter -----------------------------------------------------------------


class WakeArbiter:
    """Settles which device answers a wake word several of them heard.

    Every device that detects the wake word broadcasts a claim (the phrase
    and its dB-over-own-floor energy) and holds the wake for the window.
    Claims for the same phrase that arrive within the window either side of
    its own detection compete: the highest energy wins and the lower id
    breaks an exact tie. All devices that see all claims reach the same
    answer without a second round.
    """

    port = ARBITRATION_PORT

    def __init__(
        self,
        device_id: Optional[str] = None,
        transport: Optional[ArbitrationTransport] = None,
    ) -> None:
        self._transport = transport if transport is not None else UdpArbitrationTransport()
        # Random per run: only needs to order consistently on both sides of
        # a tie, and lowercase hex so ids compare the same everywhere.
        self.id = device_id if device_id is not None else secrets.token_hex(4)
        self._claims: Dict[str, _Claim] = {}  # "id:nonce" -> first arrival
        self._peers: Dict[str, int] = {}  # address -> last seen (ms)
        self._running = False
        self._lock = threading.Lock()
        self._random = random.Random()
        self._hello_stop = threading.Event()

    @property
    def running(self) -> bool:
        return self._running

    # -- lifecycle --

    def start(self) -> None:
        if self._running:
            return
        self._hello_stop.clear()
        self._running = self._transport.open(self._on_packet)
        if not self._running:
            return
        self._send_hello()
        threading.Thread(target=self._hello_loop, daemon=True, name="wake-arbitration-hello").start()
        _LOGGER.info("Wake arbitration listening on UDP %d (id %s)", self.port, self.id)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._hello_stop.set()
        self._claims.clear()
        self._peers.clear()
        self._transport.close()
        _LOGGER.info("Wake arbitration stopped")

    def _hello_loop(self) -> None:
        while not self._hello_stop.wait(_HELLO_INTERVAL_S):
            if self._running:
                self._send_hello()

    def _hello_packet(self) -> bytes:
        return json.dumps({"ks": _PROTOCOL_NAME, "v": _PROTOCOL_VERSION, "hello": True, "id": self.id}).encode()

    def _send_hello(self) -> None:
        if self._running:
            self._transport.send(self._hello_packet())

    def _learn(self, address: str) -> None:
        now = _now_ms()
        with self._lock:
            if address not in self._peers:
                _LOGGER.info("Wake arbitration: heard from peer %s", address)
            self._peers[address] = now
            stale = [a for a, seen in self._peers.items() if now - seen > _PEER_TIMEOUT_MS]
            for a in stale:
                del self._peers[a]

    # -- receive --

    def _on_packet(self, data: bytes, from_addr: str) -> None:
        if not self._running:
            return
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict):
            return
        if msg.get("ks") != _PROTOCOL_NAME or msg.get("v") != _PROTOCOL_VERSION:
            return
        device_id = msg.get("id")
        if not isinstance(device_id, str) or device_id == self.id or len(device_id) > _MAX_ID_LENGTH:
            return
        if msg.get("hello") is True:
            known = from_addr in self._peers
            self._learn(from_addr)
            # A peer heard for the first time gets a hello back, addressed,
            # so both sides know each other from the start.
            if not known:
                self._transport.send_to(self._hello_packet(), from_addr)
            return
        phrase = msg.get("p")
        energy = msg.get("e")
        nonce = msg.get("n")
        if not isinstance(phrase, str) or not isinstance(energy, (int, float)) or isinstance(energy, bool):
            return
        if not isinstance(nonce, int) or isinstance(nonce, bool) or not math.isfinite(float(energy)):
            return
        self._learn(from_addr)
        key = f"{device_id}:{nonce}"
        with self._lock:
            self._prune_locked()
            # Each claim goes out more than once; the first copy sets the time.
            if key not in self._claims:
                self._claims[key] = _Claim(
                    id=device_id,
                    phrase=normalize_phrase(phrase),
                    energy=round_energy(float(energy)),
                    from_addr=from_addr,
                    at=_now_ms(),
                )

    def _prune_locked(self) -> None:
        now = _now_ms()
        stale = [k for k, c in self._claims.items() if now - c.at > _CLAIM_KEEP_MS]
        for k in stale:
            del self._claims[k]

    # -- contend --

    def contend(self, phrase: str, energy: float, window_s: float) -> ArbitrationResult:
        """Claim this wake and wait out ``window_s``. Won when no other
        device heard the same phrase louder."""
        if not self._running:
            return ArbitrationResult(won=True, rivals=0)
        mine = normalize_phrase(phrase)
        level = round_energy(energy)
        packet = json.dumps(
            {
                "ks": _PROTOCOL_NAME,
                "v": _PROTOCOL_VERSION,
                "id": self.id,
                "n": self._random.randint(0, (1 << 30) - 1),
                "p": mine,
                "e": level,
            }
        ).encode()

        def send_all() -> None:
            if not self._running:
                return
            self._transport.send(packet)
            with self._lock:
                peers = list(self._peers)
            for peer in peers:
                self._transport.send_to(packet, peer)

        at = _now_ms()
        send_all()
        for delay_ms in _CLAIM_REPEAT_DELAYS_MS:
            remaining = (at + delay_ms) - _now_ms()
            if remaining > 0:
                time.sleep(remaining / 1000.0)
            send_all()

        span_ms = int(window_s * 1000)
        waited = 0
        while waited < span_ms and self._running:
            step = min(25, span_ms - waited)
            time.sleep(step / 1000.0)
            waited += step

        with self._lock:
            rivals = [c for c in self._claims.values() if c.phrase == mine and abs(c.at - at) <= span_ms]
            self._prune_locked()

        best: Optional[_Claim] = None
        for claim in rivals:
            if best is None or claim.energy > best.energy or (claim.energy == best.energy and claim.id < best.id):
                best = claim
        won = best is None or level > best.energy or (level == best.energy and self.id < best.id)
        result = ArbitrationResult(
            won=won,
            rivals=len(rivals),
            winner=None if won or best is None else best.from_addr,
            winner_energy=None if won or best is None else best.energy,
            phrase=mine,
            energy=level,
            claimed_at=at,
            heard=[f"{c.from_addr} {c.energy} dB at {'+' if c.at >= at else ''}{c.at - at} ms" for c in rivals],
        )
        if won:
            log_line = f"Wake arbitration: answering at {level:.1f} dB"
        else:
            log_line = f"Wake arbitration: {result.winner} answers instead " f"({result.winner_energy} dB), this device heard {level:.1f} dB"
        if result.heard:
            log_line += "; heard " + ", ".join(result.heard)
        _LOGGER.info(log_line)
        return result

    def overtaken(self, result: ArbitrationResult, window_s: float) -> Optional[LateClaim]:
        """After a win: wait out one more window and return a louder claim
        for the same wake that arrived after this device's window closed,
        or None.

        A device that detected the wake word later sends its claim later,
        and the trip over Wi-Fi comes on top. That device heard this one's
        claim inside its own window and answers, so this one has to stand
        down. Call this on a worker thread; it sleeps up to one window.
        """
        if not self._running or not result.won:
            return None
        span_ms = int(window_s * 1000)
        wait = result.claimed_at + 2 * span_ms - _now_ms()
        if wait > 0:
            time.sleep(wait / 1000.0)
        louder: Optional[_Claim] = None
        with self._lock:
            for claim in self._claims.values():
                after = claim.at - result.claimed_at
                if claim.phrase != result.phrase or after <= span_ms or after > 2 * span_ms:
                    continue
                beats = claim.energy > result.energy or (claim.energy == result.energy and claim.id < self.id)
                if beats and (louder is None or claim.energy > louder.energy):
                    louder = claim
        return None if louder is None else LateClaim(louder.from_addr, louder.energy)


# --- Levels ring -------------------------------------------------------------


class WakeLevelRing:
    """Accumulates dBFS levels per 20 ms frame from raw mic audio.

    Fed from the audio loop with the primary channel *before* software
    gain, AGC and noise suppression, so the metric sees the room the
    microphone actually heard.
    """

    def __init__(self, max_frames: int = _LEVELS_WINDOW_FRAMES) -> None:
        self._max_frames = max_frames
        self._levels: List[float] = []
        self._lock = threading.Lock()
        self._carry = np.empty(0, dtype=np.float32)

    def feed(self, samples: np.ndarray) -> None:
        """Accept float32 mono samples at any block size."""
        if self._carry.size:
            samples = np.concatenate((self._carry, samples))
        whole = samples.size // _FRAME_SAMPLES
        if whole == 0:
            self._carry = samples
            return
        frames = samples[: whole * _FRAME_SAMPLES].reshape(whole, _FRAME_SAMPLES)
        self._carry = samples[whole * _FRAME_SAMPLES :].copy()
        new_levels = [frame_level_db(frames[i]) for i in range(whole)]
        with self._lock:
            self._levels.extend(new_levels)
            if len(self._levels) > self._max_frames:
                del self._levels[: len(self._levels) - self._max_frames]

    def snapshot(self) -> List[float]:
        with self._lock:
            return list(self._levels)

    def wake_energy(self) -> Optional[float]:
        return energy_from_levels(self.snapshot())
