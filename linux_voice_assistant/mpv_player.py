# linux_voice_assistant/mpv_player.py
"""
Media player using mpv in a subprocess.

- Supports ALSA, PulseAudio, and PipeWire (native)
- Env override for backend: LVA_AO=pipewire|pulse|alsa (aliases: native/pw -> pipewire, pulseaudio -> pulse)
- Duck/unduck
- Robust URL handling (str | list/tuple | bytes)
- Watchdog is **disabled by default** (set watchdog_sec > 0 to enable)
- End-of-playback detection uses BOTH `eof-reached` and `idle-active`
- mpv log level configurable via env var LVA_MPV_MSG_LEVEL (e.g. "all=warn", "all=info")
"""

from __future__ import annotations

import logging
import os
from threading import Lock, Timer
from typing import Callable, Iterable, Optional, Sequence, Tuple, Union

from mpv import MPV

_LOGGER = logging.getLogger(__name__)

# ---- constants & helpers -----------------------------------------------------

_DEFAULT_MSG_LEVEL = "all=warn"
# Map friendly env tokens to canonical mpv `ao` names
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
    """
    Decide and apply (ao, audio-device) after probing candidates in order.

    Returns:
        (ao, audio_device) — the values successfully applied (or (None, None)).
    """
    ao: Optional[str] = None
    norm: Optional[str] = None

    def try_candidates(candidates: Iterable[Tuple[str, str]]) -> bool:
        nonlocal ao, norm
        for cand_ao, cand_norm in candidates:
            try:
                player["ao"] = cand_ao  # probe
                ao, norm = cand_ao, cand_norm
                return True
            except Exception:
                # Keep trying next backend
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
            # Preference: env override -> PipeWire -> Pulse -> ALSA
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
            # No explicit prefix: respect env override first; otherwise PW -> Pulse -> ALSA
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
        # No device specified: env override first, then PW -> Pulse -> ALSA
        order: list[Tuple[str, str]] = []
        if env_ao:
            order.append((env_ao, f"{env_ao}/default"))
        order += [
            ("pipewire", "pipewire/default"),
            ("pulse", "pulse/default"),
            ("alsa", "alsa/default"),
        ]
        try_candidates(order)

    # Apply selections (safe setters log on failure)
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
        # Bridge mpv logs into Python logging
        self.player = MPV(video=False, terminal=False, log_handler=self._mpv_log)

        # mpv defaults (errors are logged, not swallowed)
        _set_player_opt(self.player, "audio-samplerate", 44100)
        _set_player_opt(self.player, "audio-channels", "stereo")
        _set_player_opt(self.player, "keep-open", "no")
        _set_player_opt(self.player, "network-timeout", 7)
        msg_level = os.environ.get("LVA_MPV_MSG_LEVEL", _DEFAULT_MSG_LEVEL)
        _set_player_opt(self.player, "msg-level", msg_level)

        # Env-driven AO override
        env_ao = _normalize_env_ao(os.environ.get("LVA_AO"))

        # Backend selection
        self._ao, self._audio_device = _select_backend(self.player, device, env_ao)

        # State
        self.is_playing: bool = False
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()
        self._pre_duck_volume: Optional[int] = None  # stores 0..100 when ducked
        self._watchdog: Optional[Timer] = None
        self._watchdog_sec: float = float(watchdog_sec)

        # Playback end detection (both EOF and idle)
        self.player.observe_property("eof-reached", self._on_eof)
        self.player.observe_property("idle-active", self._on_idle_active)

    # -------------------- public API --------------------

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = False,
    ) -> None:
        """Begin playback of a URL or local file. Replaces any existing playback.

        Accepts a single URL (str/bytes) or a sequence of URLs; if a sequence is given,
        the first item is played.
        """
        if stop_first:
            try:
                self.stop()
            except Exception:
                _LOGGER.debug("stop_first=True: stop() raised, continuing", exc_info=True)

        # Store callback
        with self._done_callback_lock:
            self._done_callback = done_callback

        # Normalize URL input
        if isinstance(url, (list, tuple)):
            if not url:
                _LOGGER.error("mpv play() received empty URL list")
                self._run_done_callback()
                return
            url = url[0]

        if isinstance(url, bytes):
            try:
                url = url.decode("utf-8")
            except Exception:
                url = url.decode(errors="ignore")

        if not isinstance(url, str):
            _LOGGER.error("mpv play() expected str URL, got %r", type(url))
            self._run_done_callback()
            return

        # Ensure not muted
        try:
            self.player["mute"] = "no"
        except Exception:
            _LOGGER.debug("Failed to clear mute before play()", exc_info=True)

        # Begin playback
        self.is_playing = True
        try:
            self.player.play(url)
            self._arm_watchdog()
        except Exception:
            self.is_playing = False
            _LOGGER.exception("mpv failed to play %r", url, exc_info=True)
            self._run_done_callback()

    def pause(self) -> None:
        try:
            self.player.pause = True
        except Exception:
            _LOGGER.exception("mpv pause() failed", exc_info=True)

    def resume(self) -> None:
        try:
            self.player.pause = False
        except Exception:
            _LOGGER.exception("mpv resume() failed", exc_info=True)

    def stop(self) -> None:
        """Stop playback immediately and clear playing state; run done callback."""
        try:
            try:
                self.player.command("stop")
            except Exception:
                # Fallback: load a null source to replace playback
                self.player.command("loadfile", "null://", "replace")
        finally:
            self._cancel_watchdog()
            was_playing = self.is_playing
            self.is_playing = False
            if was_playing:
                self._run_done_callback()

    def set_volume(self, *args, **kwargs) -> None:
        """Set volume 0..100. Accepts positional or keyword 'volume' (back-compat)."""
        vol_arg = args[0] if args else kwargs.get("volume")
        if vol_arg is None:
            _LOGGER.error("set_volume() requires an integer (0..100)")
            return

        try:
            vol = max(0, min(100, int(vol_arg)))
        except Exception:
            _LOGGER.exception("set_volume(): invalid value %r", vol_arg, exc_info=True)
            return

        try:
            self.player.volume = vol
        except Exception:
            _LOGGER.exception("mpv set_volume(%s) failed", vol, exc_info=True)

    def duck(self, target_percent: int = 20) -> None:
        """Temporarily lower volume for TTS/alerts; call unduck() to restore."""
        if self._pre_duck_volume is not None:
            return  # already ducked

        try:
            current = int(round(float(self.player.volume)))
        except Exception:
            current = 100

        self._pre_duck_volume = max(0, min(100, current))

        try:
            self.player.volume = max(0, min(100, int(target_percent)))
        except Exception:
            _LOGGER.exception("duck(): failed to set duck volume", exc_info=True)

    def unduck(self) -> None:
        """Restore volume after duck()."""
        if self._pre_duck_volume is None:
            return

        try:
            self.player.volume = self._pre_duck_volume
        except Exception:
            _LOGGER.exception("unduck(): failed to restore volume", exc_info=True)
        finally:
            self._pre_duck_volume = None

    # -------------------- callbacks & watchdog --------------------

    def _on_eof(self, _name: str, reached: bool) -> None:
        if not reached:
            return
        self._cancel_watchdog()
        self.is_playing = False
        self._run_done_callback()

    def _on_idle_active(self, _name: str, active: bool) -> None:
        # When playback ends, mpv enters idle; treat that as completion.
        if not self.is_playing:
            return
        if bool(active):
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self._cancel_watchdog()
            self.is_playing = False
            self._run_done_callback()

    def _arm_watchdog(self) -> None:
        self._cancel_watchdog()
        if self._watchdog_sec <= 0:
            return
        self._watchdog = Timer(self._watchdog_sec, self._watchdog_trip)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _cancel_watchdog(self) -> None:
        if self._watchdog is not None:
            try:
                self._watchdog.cancel()
            except Exception:
                _LOGGER.debug("watchdog cancel raised", exc_info=True)
            self._watchdog = None

    def _watchdog_trip(self) -> None:
        if self.is_playing:
            _LOGGER.warning(
                "mpv watchdog fired after %.1fs; stopping playback", self._watchdog_sec
            )
            try:
                self.player.command("stop")
            except Exception:
                _LOGGER.debug("watchdog stop raised", exc_info=True)
            self.is_playing = False
            self._run_done_callback()

    def _run_done_callback(self) -> None:
        cb: Optional[Callable[[], None]] = None
        with self._done_callback_lock:
            cb = self._done_callback
            self._done_callback = None

        if cb is not None:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Unexpected error running done callback", exc_info=True)

    # -------------------- mpv log bridge --------------------
    def _mpv_log(self, level: str, prefix: str, text: str) -> None:
        msg = f"mpv[{level}] {prefix}: {text}".rstrip()
        if level in ("fatal", "error"):
            _LOGGER.error(msg)
        elif level in ("warn", "warning"):
            _LOGGER.warning(msg)
        elif level in ("info",):
            _LOGGER.info(msg)
        else:
            _LOGGER.debug(msg)

