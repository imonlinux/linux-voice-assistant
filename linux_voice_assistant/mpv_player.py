# Copyright (c) 2025
# mpv-based media player with PulseAudio/ALSA support and no-progress watchdog
from __future__ import annotations

import logging
import os
import time
from threading import Timer
from typing import Callable, Optional, Sequence, Union

try:
    from mpv import MPV
except Exception as e:  # pragma: no cover
    # Keep import error visible during service startup
    raise

_LOGGER = logging.getLogger(__name__)


class MpvMediaPlayer:
    """Thin wrapper around python-mpv suited for short TTS clips.

    Features:
      - Supports PulseAudio and ALSA devices
      - Accepts plain sink names (assumed Pulse): e.g. 'alsa_output.foo.bar'
      - End-of-playback detection via 'idle-active' and 'eof-reached'
      - No-progress watchdog: trips only if time-pos doesn't advance
      - Optional duck/unduck helpers using mpv volume property
    """

    def __init__(
        self,
        device: Optional[str] = None,
        watchdog_sec: float = 8.0,
    ) -> None:
        self._done_cb: Optional[Callable[[], None]] = None
        self._watchdog: Optional[Timer] = None
        self._watchdog_sec: float = float(watchdog_sec)
        self._last_progress: float = 0.0
        self._duck_prev_volume: Optional[float] = None
        self._stopped: bool = False

        # Decide backend and device
        ao, audio_device = self._select_backend_and_device(device)

        # mpv msg level
        msg_level = os.getenv("LVA_MPV_MSG_LEVEL", "all=warn")

        # Create player instance tuned for TTS
        # NOTE: video disabled; keep-open=no so idle triggers quickly
        self.player = MPV(
            video=False,
            ao=ao,
            audio_device=audio_device if audio_device else None,
            audio_samplerate="44100",
            audio_channels="stereo",
            keep_open="no",
            msg_level=msg_level,
            input_default_bindings=False,
            input_vo_keyboard=False,
            ytdl=False,
            log_handler=self._mpv_log,
            network_timeout=7,
        )

        # Bind property observers
        self._bind_observers()

    # -------------------- public API --------------------

    def play(
        self,
        source: Union[str, Sequence[str]],
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """Play a single URL/path or a sequence of them.
        Calls done_callback exactly once on clean EOF or on failure/stop.
        """
        self._stopped = False
        self._done_cb = done_callback
        self._last_progress = time.monotonic()  # seed progress clock

        try:
            # Clear any prior state
            self.stop(silent=True)

            if isinstance(source, (list, tuple)):
                if not source:
                    raise ValueError("source list is empty")
                first, *rest = source
                self.player.play(str(first))
                for item in rest:
                    # Append subsequent items to playlist
                    self.player.command("loadfile", str(item), "append-play")
            else:
                self.player.play(str(source))

            # Arm the no-progress watchdog
            self._arm_watchdog()

        except Exception:
            _LOGGER.exception("mpv failed to play %r", source)
            self._finish()

    def stop(self, silent: bool = False) -> None:
        """Stop playback immediately."""
        try:
            self._stopped = True
            self._cancel_watchdog()
            # 'stop' stops current file; 'playlist-clear' ensures nothing pending
            self.player.command("stop")
            self.player.command("playlist-clear")
        except Exception:
            if not silent:
                _LOGGER.exception("mpv stop() failed")
        finally:
            # Ensure completion callback is fired when explicitly stopping
            if not silent:
                self._finish()

    def set_volume(self, percent: float) -> None:
        """Set mpv volume 0-100."""
        try:
            p = max(0.0, min(100.0, float(percent)))
            self.player.volume = p
        except Exception:
            _LOGGER.exception("mpv set_volume(%s) failed", percent)

    def get_volume(self) -> float:
        try:
            v = float(self.player.volume)
        except Exception:
            _LOGGER.exception("mpv get_volume() failed")
            v = 0.0
        return v

    # ---- simple duck/unduck helpers (independent of Pulse role ducking) ----

    def duck(self, to_percent: float = 25.0) -> None:
        try:
            if self._duck_prev_volume is None:
                self._duck_prev_volume = self.get_volume()
            self.set_volume(to_percent)
        except Exception:
            _LOGGER.exception("duck() failed")

    def unduck(self) -> None:
        try:
            if self._duck_prev_volume is not None:
                self.set_volume(self._duck_prev_volume)
        except Exception:
            _LOGGER.exception("unduck() failed")
        finally:
            self._duck_prev_volume = None

    def close(self) -> None:
        try:
            self._cancel_watchdog()
            self.player.terminate()
        except Exception:
            _LOGGER.exception("mpv terminate failed")

    # -------------------- internal helpers --------------------

    def _bind_observers(self) -> None:
        # Idle means no file is playing; ideal for short TTS EOF detection
        @self.player.property_observer("idle-active")
        def _idle_active(_name, value):
            try:
                self._on_idle(value)
            except Exception:
                _LOGGER.exception("idle-active observer failed")

        # Some builds also toggle eof-reached at end of a file
        @self.player.property_observer("eof-reached")
        def _eof(_name, value):
            try:
                if value:
                    self._on_eof()
            except Exception:
                _LOGGER.exception("eof-reached observer failed")

        # Progress while playing; used by the no-progress watchdog
        @self.player.property_observer("time-pos")
        def _timepos(_name, value):
            try:
                self._on_timepos(value)
            except Exception:
                _LOGGER.exception("time-pos observer failed")

    def _on_idle(self, value: bool) -> None:
        if value:
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self._finish()

    def _on_eof(self) -> None:
        _LOGGER.debug("mpv reported eof-reached")
        self._finish()

    def _on_timepos(self, value: Optional[float]) -> None:
        # Called frequently during active playback; update progress clock
        if value is not None:
            self._last_progress = time.monotonic()
            # keep watchdog fresh during active playback
            self._arm_watchdog()

    def _finish(self) -> None:
        # Stop watchdog and call done callback once
        self._cancel_watchdog()
        cb, self._done_cb = self._done_cb, None
        if cb is not None:
            try:
                cb()
            except Exception:
                _LOGGER.exception("done_callback raised")

    # -------------------- watchdog (no-progress) --------------------

    def _arm_watchdog(self) -> None:
        self._cancel_watchdog()
        if self._watchdog_sec <= 0:
            return
        # seed if never set
        if self._last_progress == 0.0:
            self._last_progress = time.monotonic()

        def _trip():
            try:
                stalled_for = time.monotonic() - self._last_progress
                if stalled_for >= self._watchdog_sec and not self._stopped:
                    _LOGGER.warning(
                        "mpv watchdog (no progress) fired after %.1fs; stopping playback",
                        self._watchdog_sec,
                    )
                    self.stop(silent=False)
                else:
                    # progress happened; keep monitoring
                    self._arm_watchdog()
            except Exception:
                _LOGGER.exception("watchdog trip failed")

        self._watchdog = Timer(self._watchdog_sec, _trip)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _cancel_watchdog(self) -> None:
        t = self._watchdog
        self._watchdog = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    # -------------------- backend/device selection --------------------

    @staticmethod
    def _select_backend_and_device(device: Optional[str]) -> tuple[str, Optional[str]]:
        """Return (ao, audio_device) based on a friendly device string.

        Accepted forms:
          - None or 'default'      -> (ao='pulse', device=None)  [Pulse default sink]
          - 'pulse/<sink-name>'    -> (ao='pulse', device='pulse/<sink-name>')
          - '<sink-name>'          -> (ao='pulse', device='pulse/<sink-name>')
          - 'alsa/<hw-spec>'       -> (ao='alsa',  device='alsa/<hw-spec>')
        """
        if not device or device == "default":
            return "pulse", None

        d = str(device).strip()
        if d.startswith("pulse/"):
            return "pulse", d
        if d.startswith("alsa/"):
            return "alsa", d

        # Unprefixed: assume Pulse sink name
        return "pulse", f"pulse/{d}"

    # -------------------- mpv log bridge --------------------

    def _mpv_log(self, level: str, component: str, message: str) -> None:
        # Map mpv levels to Python logging
        lvl = (level or "").lower()
        log = _LOGGER.debug
        if lvl in ("fatal", "error"):
            log = _LOGGER.error
        elif lvl in ("warn", "warning"):
            log = _LOGGER.warning
        elif lvl in ("info",):
            log = _LOGGER.info
        # keep mpv chatter short; component can be verbose
        log("mpv[%s]: %s", component, message)
