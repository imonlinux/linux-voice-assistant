# linux_voice_assistant/mpv_player.py
"""
Media player using mpv in a subprocess.

Merged version:
- Keeps LVA's backend auto-selection (PipeWire, PulseAudio, ALSA)
- Keeps watchdog, env-based config, robust URL handling, and logging
- Adds OHF-style playlist chaining (_playlist)
"""

from __future__ import annotations

import logging
import os
from threading import Lock, Timer
from typing import Callable, Iterable, Optional, Sequence, Tuple, Union, List

from mpv import MPV

_LOGGER = logging.getLogger(__name__)

# ---- constants & helpers -----------------------------------------------------

_DEFAULT_MSG_LEVEL = "all=warn"

_AO_ENV_ALIASES = {
    "native": "pipewire",
    "pw": "pipewire",
    "pulseaudio": "pulse",
}
_AO_ALLOWED = {"pipewire", "pulse", "alsa"}


def _normalize_env_ao(value: Optional[str]) -> Optional[str]:
    """Normalize LVA_AO environment override into one of _AO_ALLOWED or None."""
    if not value:
        return None
    v = value.strip().lower()
    v = _AO_ENV_ALIASES.get(v, v)
    return v if v in _AO_ALLOWED else None


def _set_player_opt(player: MPV, key: str, value) -> None:
    """Set an mpv option with logging on failure."""
    try:
        player[key] = value
    except Exception:
        _LOGGER.exception("Failed to set mpv option %r=%r", key, value, exc_info=True)


def _select_backend(
    player: MPV,
    device: Optional[str],
    env_ao: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Decide and apply (ao, audio-device) after probing candidates."""
    ao: Optional[str] = None
    norm: Optional[str] = None

    def try_candidates(candidates: Iterable[Tuple[str, str]]) -> bool:
        nonlocal ao, norm
        for cand_ao, cand_norm in candidates:
            try:
                player["ao"] = cand_ao
                ao, norm = cand_ao, cand_norm
                return True
            except Exception:
                continue
        return False

    if device:
        d = device.strip()
        if d.startswith("alsa/"):
            ao, norm = "alsa", d
        elif d.startswith("pulse/"):
            ao, norm = "pulse", d
        elif d.startswith(("pipewire/", "pw/")):
            ao, norm = "pipewire", d.replace("pw/", "pipewire/", 1)
        elif d == "default":
            order: list[Tuple[str, str]] = []
            if env_ao:
                order.append((env_ao, f"{env_ao}/default"))
            order += [
                ("pipewire", "pipewire/default"),
                ("pulse", "pulse/default"),
                ("alsa", "alsa/default"),
            ]
            if not try_candidates(order):
                _LOGGER.warning(
                    "No suitable audio output (PipeWire/Pulse/ALSA) available for 'default'"
                )
        else:
            tried = False
            if env_ao:
                tried = try_candidates(
                    [(env_ao, f"{env_ao}/{d if env_ao != 'alsa' else 'default'}")]
                )
            if not tried:
                try_candidates(
                    [
                        ("pipewire", f"pipewire/{d}"),
                        ("pulse", f"pulse/{d}"),
                        ("alsa", "alsa/default"),
                    ]
                )
    else:
        order: list[Tuple[str, str]] = []
        if env_ao:
            order.append((env_ao, f"{env_ao}/default"))
        order += [
            ("pipewire", "pipewire/default"),
            ("pulse", "pulse/default"),
            ("alsa", "alsa/default"),
        ]
        try_candidates(order)

    if ao:
        _set_player_opt(player, "ao", ao)
    if norm:
        _set_player_opt(player, "audio-device", norm)

    if ao or norm:
        _LOGGER.debug("mpv backend selected: ao=%s, audio-device=%s", ao, norm)
    return ao, norm


# ---- class -------------------------------------------------------------------


class MpvMediaPlayer:
    def __init__(self, device: Optional[str] = None, watchdog_sec: float = 0.0) -> None:
        self.player = MPV(video=False, terminal=False, log_handler=self._mpv_log)

        _set_player_opt(self.player, "audio-samplerate", 44100)
        _set_player_opt(self.player, "audio-channels", "stereo")
        _set_player_opt(self.player, "keep-open", "no")
        _set_player_opt(self.player, "network-timeout", 7)
        msg_level = os.environ.get("LVA_MPV_MSG_LEVEL", _DEFAULT_MSG_LEVEL)
        _set_player_opt(self.player, "msg-level", msg_level)

        env_ao = _normalize_env_ao(os.environ.get("LVA_AO"))
        self._ao, self._audio_device = _select_backend(self.player, device, env_ao)

        self.is_playing: bool = False
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()
        self._pre_duck_volume: Optional[int] = None
        self._watchdog: Optional[Timer] = None
        self._watchdog_sec: float = float(watchdog_sec)

        # Playlist support (OHF addition)
        self._playlist: List[str] = []

        self.player.observe_property("eof-reached", self._on_eof)
        self.player.observe_property("idle-active", self._on_idle_active)

    # -------------------- public API --------------------

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]()]()_
