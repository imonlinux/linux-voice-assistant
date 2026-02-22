"""Media player using mpv in a subprocess.

This wrapper focuses on:
- Simple playback control (play / pause / resume / stop)
- Volume control and ducking
- Notifying a done_callback when playback finishes
- Letting mpv choose the best audio backend by default

If a specific audio device is provided, it is passed directly to mpv as
`audio-device`. Otherwise, mpv's own automatic backend/device selection is used.

Note about volume:
- mpv has its own per-player volume (0..100).
- PipeWire/PulseAudio/ALSA also has a system output volume.

LVA persists a single user volume (0.0..1.0) in preferences.json. To avoid
"mystery caps" where the OS sink is stuck at e.g. 40% while LVA shows 100%,
LVA treats the OS output volume as the "master" and keeps mpv at 100% for normal
playback. Ducking still uses mpv's per-player volume.
"""

from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock
from typing import Callable, List, Optional, Sequence, Union

# Note: python-mpv must be installed; imported at runtime.
from mpv import MPV

from .audio_volume import set_output_volume

_LOGGER = logging.getLogger(__name__)


class MpvMediaPlayer:
    """A media player class that wraps the python-mpv library."""

    def __init__(
        self,
        loop: Optional[asyncio.AbstractEventLoop],
        device: Optional[str] = None,
        initial_volume: float = 1.0,
    ) -> None:
        """Initialize the mpv player.

        :param loop: The asyncio loop used to schedule done_callback.
                     May be None in one-off utility contexts (e.g. listing devices).
        :param device: Optional mpv audio device name (e.g.
                       "pipewire/alsa_output.pci-0000_00_1f.3.analog-stereo").
        :param initial_volume: Initial volume as a float 0.0–1.0. This is now
                               treated as the *master* OS output volume. mpv's
                               internal volume is set to 100% for normal playback.
        """
        self.loop = loop
        self.device = device
        self.initial_volume = max(0.0, min(1.0, float(initial_volume)))

        self.player = MPV(
            video=False,
            terminal=False,
            log_handler=self._mpv_log,
            audio_samplerate=44100,
            audio_channels="stereo",
            keep_open="no",
            network_timeout=7,
            ytdl=False,
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
                    f"{dev.get('name')} ({dev.get('description')})" for dev in dev_list
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

        # Keep mpv at 100% for normal playback. Master volume is handled at the OS level.
        self.set_volume(100)

        self.is_playing: bool = False
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
        # Ensure player is in a clean state
        self.stop()

        with self._done_callback_lock:
            self._done_callback = done_callback

        playlist: List[str] = []
        if isinstance(url, (list, tuple)):
            playlist = list(url)
        elif isinstance(url, bytes):
            playlist = [url.decode(errors="ignore")]
        elif isinstance(url, str):
            playlist = [url]
        else:
            _LOGGER.error("play() expected str, bytes, or sequence, got %r", type(url))
            self._run_done_callback()
            return

        if not playlist:
            self._run_done_callback()
            return

        # Load the full playlist into mpv
        self.player.playlist_clear()
        for item in playlist:
            self.player.playlist_append(item)

        self.is_playing = True
        self.player.playlist_pos = 0  # Start playing from the first item

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

    def stop(self) -> None:
        """Stops playback and clears the playlist."""
        if self.is_playing:
            self.is_playing = False
            try:
                self.player.playlist_clear()
                self.player.command("stop")
            except Exception:
                _LOGGER.exception("stop() failed")
            finally:
                self._run_done_callback()

    def set_volume(self, volume: int) -> None:
        """Sets the player (mpv) volume from 0 to 100."""
        try:
            self.player.volume = max(0, min(100, volume))
        except Exception:
            _LOGGER.exception("set_volume() failed")

    def set_master_volume(self, volume_level: float) -> bool:
        """Sets the *OS output* volume (PipeWire/PulseAudio/ALSA).

        :param volume_level: 0.0–1.0
        :returns: True if any backend succeeded.
        """
        return set_output_volume(volume_level=volume_level, output_device=self.device)

    def duck(self, target_percent: int = 20) -> None:
        """Lowers the mpv volume for an announcement."""
        if self._pre_duck_volume is not None:
            return
        try:
            self._pre_duck_volume = int(self.player.volume)
            self.set_volume(target_percent)
        except Exception:
            _LOGGER.exception("duck() failed")

    def unduck(self) -> None:
        """Restores the mpv volume after an announcement."""
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
            self._run_done_callback()

    def _run_done_callback(self) -> None:
        """Safely runs the done_callback on the main asyncio loop (if any)."""
        with self._done_callback_lock:
            cb = self._done_callback
            self._done_callback = None

        if not cb:
            return

        # If we have an asyncio loop, schedule callback there.
        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(cb)
            except Exception:
                _LOGGER.exception("Error scheduling done_callback on loop")
        else:
            # Fallback: call directly (used in contexts where no loop is passed).
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
