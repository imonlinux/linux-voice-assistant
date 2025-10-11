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
    if not value:
        return None
    v = value.strip().lower()
    v = _AO_ENV_ALIASES.get(v, v)
    return v if v in _AO_ALLOWED else None


def _set_player_opt(player: MPV, key: str, value) -> None:
    try:
        player[key] = value
    except Exception:
        _LOGGER.exception("Failed to set mpv option %r=%r", key, value, exc_info=True)


def _select_backend(player: MPV, device: Optional[str], env_ao: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
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
                _LOGGER.warning("No suitable audio output available for 'default'")
        else:
            tried = False
            if env_ao:
                tried = try_candidates([(env_ao, f"{env_ao}/{d if env_ao != 'alsa' else 'default'}")])
            if not tried:
                try_candidates([("pipewire", f"pipewire/{d}"), ("pulse", f"pulse/{d}"), ("alsa", "alsa/default")])
    else:
        order: list[Tuple[str, str]] = []
        if env_ao:
            order.append((env_ao, f"{env_ao}/default"))
        order += [("pipewire", "pipewire/default"), ("pulse", "pulse/default"), ("alsa", "alsa/default")]
        try_candidates(order)

    if ao:
        _set_player_opt(player, "ao", ao)
    if norm:
        _set_player_opt(player, "audio-device", norm)
    if ao or norm:
        _LOGGER.debug("mpv backend selected: ao=%s, audio-device=%s", ao, norm)
    return ao, norm


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

        self._playlist: List[str] = []

        self.player.observe_property("eof-reached", self._on_eof)
        self.player.observe_property("idle-active", self._on_idle_active)

    # -------------------- public API --------------------

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]] = None,
        stop_first: bool = False,
    ) -> None:
        if stop_first:
            try:
                self.stop()
            except Exception:
                _LOGGER.debug("stop_first=True: stop() raised", exc_info=True)

        with self._done_callback_lock:
            self._done_callback = done_callback

        if isinstance(url, (list, tuple)):
            self._playlist = list(url)
        elif isinstance(url, bytes):
            try:
                self._playlist = [url.decode("utf-8")]
            except Exception:
                self._playlist = [url.decode(errors="ignore")]
        elif isinstance(url, str):
            self._playlist = [url]
        else:
            _LOGGER.error("mpv play() expected str/seq/bytes, got %r", type(url))
            self._run_done_callback()
            return

        if not self._playlist:
            _LOGGER.error("Empty playlist; nothing to play")
            self._run_done_callback()
            return

        next_url = self._playlist.pop(0)
        self._play_single(next_url)

    def _play_single(self, url: str) -> None:
        _LOGGER.debug("Playing %s", url)
        try:
            self.player["mute"] = "no"
        except Exception:
            _LOGGER.debug("Failed to unmute before playback", exc_info=True)

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
        try:
            try:
                self.player.command("stop")
            except Exception:
                self.player.command("loadfile", "null://", "replace")
        finally:
            self._cancel_watchdog()
            self._playlist.clear()
            was_playing = self.is_playing
            self.is_playing = False
            if was_playing:
                self._run_done_callback()

    def set_volume(self, *args, **kwargs) -> None:
        vol_arg = args[0] if args else kwargs.get("volume")
        if vol_arg is None:
            _LOGGER.error("set_volume() requires an integer (0..100)")
            return

        try:
            vol = max(0, min(100, int(vol_arg)))
            self.player.volume = vol
        except Exception:
            _LOGGER.exception("set_volume() failed", exc_info=True)

    def duck(self, target_percent: int = 20) -> None:
        if self._pre_duck_volume is not None:
            return
        try:
            current = int(round(float(self.player.volume)))
        except Exception:
            current = 100
        self._pre_duck_volume = current
        try:
            self.player.volume = max(0, min(100, int(target_percent)))
        except Exception:
            _LOGGER.exception("duck() failed", exc_info=True)

    def unduck(self) -> None:
        if self._pre_duck_volume is None:
            return
        try:
            self.player.volume = self._pre_duck_volume
        except Exception:
            _LOGGER.exception("unduck() failed", exc_info=True)
        finally:
            self._pre_duck_volume = None

    # -------------------- callbacks & watchdog --------------------

    def _on_eof(self, _name: str, reached: bool) -> None:
        if not reached:
            return
        self._cancel_watchdog()
        self.is_playing = False

        if self._playlist:
            next_url = self._playlist.pop(0)
            self._play_single(next_url)
            return

        self._run_done_callback()

    def _on_idle_active(self, _name: str, active: bool) -> None:
        if not self.is_playing:
            return
        if bool(active):
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self._cancel_watchdog()
            self.is_playing = False
            if self._playlist:
                next_url = self._playlist.pop(0)
                self._play_single(next_url)
                return
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
            _LOGGER.warning("mpv watchdog fired; stopping playback")
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
