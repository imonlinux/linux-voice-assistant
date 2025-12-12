"""
Media player using mpv in a subprocess.

This wrapper focuses on:
- Simple playback control (play / pause / resume / stop)
- Volume control and ducking
- Notifying a done_callback when playback finishes
- Letting mpv choose the best audio backend by default

If a specific audio device is provided, it is passed directly to mpv as
`audio-device`. Otherwise, mpv's own automatic backend/device selection is used.
"""
from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock
from typing import Callable, List, Optional, Sequence, Union

# Note: python-mpv must be installed; imported at runtime.
from mpv import MPV

_LOGGER = logging.getLogger(__name__)


class MpvMediaPlayer:
    """A media player class that wraps the python-mpv library."""

    def __init__(
        self,
        loop: Optional[asyncio.AbstractEventLoop],
        device: Optional[str] = None,
        initial_volume: float = 1.0,
    ) -> None:
        """
        :param loop: The asyncio loop used to schedule done_callback.
                     May be None in one-off utility contexts (e.g. listing devices).
        :param device: Optional mpv audio device name (e.g. "pulse/alsa_output.pci-0000_00_1f.3.analog-stereo").
        :param initial_volume: Initial volume as a float 0.0–1.0.
        """
        self.loop = loop

        self.player = MPV(
            video=False,
            terminal=False,
            log_handler=self._mpv_log,
            audio_samplerate=44100,
            audio_channels="stereo",
            keep_open="no",
            network_timeout=7,
            msg_level=os.environ.get("LVA_MPV_MSG_LEVEL", "all=warn"),
        )

        # Optional: allow forcing ao via environment for power users/debugging.
        ao_env = os.environ.get("LVA_AO")
        if ao_env:
            try:
                self.player["ao"] = ao_env
                _LOGGER.info("Forcing mpv ao=%r from LVA_AO", ao_env)
            except Exception:
                _LOGGER.exception("Failed to set mpv ao=%r", ao_env)

        # If the caller provided a specific device, honor it directly.
        if device:
            try:
                self.player["audio-device"] = device
                _LOGGER.info("Using mpv audio-device=%r", device)
            except Exception:
                _LOGGER.exception("Failed to set mpv audio-device %r", device)

        # Log final backend/device selection and available devices (best-effort)
        try:
            ao_effective = self.player["ao"]
            # python-mpv may expose an unset option as [] – normal for "auto"
            if isinstance(ao_effective, list) and not ao_effective:
                ao_display = "<unset/auto>"
            else:
                ao_display = ao_effective

            audio_device_effective = self.player["audio-device"]

            try:
                dev_list = self.player.audio_device_list or []
                dev_summary = [
                    f"{dev.get('name')} ({dev.get('description')})"
                    for dev in dev_list
                ]
            except Exception:
                dev_summary = ["<unavailable>"]

            _LOGGER.debug(
                "mpv audio config: ao=%r, audio-device=%r, devices=%s",
                ao_display,
                audio_device_effective,
                dev_summary,
            )
        except Exception:
            _LOGGER.exception("Failed to query mpv audio properties")

        # Volume is 0–100 in mpv, we accept 0.0–1.0 here.
        self.set_volume(int(initial_volume * 100))

        self.is_playing: bool = False

        # One callback per "logical playback session" (music or announce)
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()

        self._pre_duck_volume: Optional[int] = None

        # When mpv becomes idle, we treat it as end-of-playback.
        self.player.observe_property("idle-active", self._on_idle_active)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """Plays a URL or sequence of URLs.

        :param url: A single URL (str/bytes) or a sequence of URLs.
        :param done_callback: Called once when playback finishes or is stopped.
        """
        # IMPORTANT:
        # When starting a new playback session (e.g., Music Assistant "next track"),
        # we must NOT fire the previous session's done_callback (it would set HA to IDLE
        # even though a new track is starting).
        self.stop(run_done_callback=False)

        playlist: List[str] = []
        if isinstance(url, (list, tuple)):
            playlist = list(url)
        elif isinstance(url, bytes):
            playlist = [url.decode(errors="ignore")]
        elif isinstance(url, str):
            playlist = [url]
        else:
            _LOGGER.error("play() expected str, bytes, or sequence, got %r", type(url))
            if done_callback:
                self._call_done_callback(done_callback)
            return

        if not playlist:
            if done_callback:
                self._call_done_callback(done_callback)
            return

        with self._done_callback_lock:
            self._done_callback = done_callback

        # Load the full playlist into mpv
        try:
            self.player.playlist_clear()
            for item in playlist:
                self.player.playlist_append(item)
            self.player.playlist_pos = 0  # Start playing from the first item
            self.is_playing = True
        except Exception:
            _LOGGER.exception("Failed to start playback")
            self.is_playing = False
            # pop callback and run it, since we failed to start
            cb = None
            with self._done_callback_lock:
                cb = self._done_callback
                self._done_callback = None
            if cb:
                self._call_done_callback(cb)

    def pause(self) -> None:
        """Pauses playback."""
        try:
            self.player.pause = True
        except Exception:
            _LOGGER.exception("pause() failed")

    def resume(self) -> None:
        """Resumes playback."""
        try:
            self.player.pause = False
        except Exception:
            _LOGGER.exception("resume() failed")

    def stop(self, run_done_callback: bool = True) -> None:
        """Stops playback and clears the playlist.

        :param run_done_callback: If False, clears any prior done_callback without running it.
                                  This is critical when replacing one track with another.
        """
        # Always clear the stored callback so an "old session" callback can't fire later.
        cb: Optional[Callable[[], None]] = None
        with self._done_callback_lock:
            if run_done_callback:
                cb = self._done_callback
            self._done_callback = None

        was_playing = self.is_playing
        self.is_playing = False

        # Best-effort stop. Even if mpv throws, we still want callback behavior.
        try:
            self.player.playlist_clear()
            self.player.command("stop")
        except Exception:
            # If this was an external stop request, log it; otherwise keep quiet.
            if was_playing:
                _LOGGER.exception("stop() failed")

        if cb:
            self._call_done_callback(cb)

    def set_volume(self, volume: int) -> None:
        """Sets the player volume from 0 to 100."""
        try:
            self.player.volume = max(0, min(100, volume))
        except Exception:
            _LOGGER.exception("set_volume() failed")

    def duck(self, target_percent: int = 20) -> None:
        """Lowers the volume for an announcement."""
        if self._pre_duck_volume is not None:
            return
        try:
            self._pre_duck_volume = int(self.player.volume)
            self.set_volume(target_percent)
        except Exception:
            _LOGGER.exception("duck() failed")

    def unduck(self) -> None:
        """Restores the volume after an announcement."""
        if self._pre_duck_volume is None:
            return
        try:
            self.set_volume(self._pre_duck_volume)
        except Exception:
            _LOGGER.exception("unduck() failed")
        finally:
            self._pre_duck_volume = None

    # -------------------------------------------------------------------------
    # Internal callbacks
    # -------------------------------------------------------------------------

    def _on_idle_active(self, _name: str, active: bool) -> None:
        """Callback triggered when mpv enters or leaves the idle state."""
        if active and self.is_playing:
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self.is_playing = False

            cb = None
            with self._done_callback_lock:
                cb = self._done_callback
                self._done_callback = None

            if cb:
                self._call_done_callback(cb)

    def _call_done_callback(self, cb: Callable[[], None]) -> None:
        """Runs a done_callback on the main asyncio loop (if any)."""
        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(cb)
            except Exception:
                _LOGGER.exception("Error scheduling done_callback on loop")
        else:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error running done_callback directly")

    def _mpv_log(self, level: str, prefix: str, text: str) -> None:
        """Routes mpv's internal logs to our logger."""
        msg = f"mpv[{prefix}]: {text}".rstrip()
        if level == "error":
            _LOGGER.error(msg)
        elif level == "warn":
            _LOGGER.warning(msg)
        elif level == "info":
            _LOGGER.info(msg)
        else:
            _LOGGER.debug(msg)
