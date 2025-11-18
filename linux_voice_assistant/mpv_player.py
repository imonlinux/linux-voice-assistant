"""
Media player using mpv in a subprocess.
Includes logic to detect the audio server being used (Pirewire, PulseAudio, Alsa).
This refactored version simplifies the audio backend selection logic,
improves readability with docstrings, and standardizes method signatures.
"""
from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock, Timer
from typing import Callable, Iterable, List, Optional, Sequence, Tuple, Union

from mpv import MPV

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------

def _set_player_opt(player: MPV, key: str, value) -> bool:
    """Safely set an mpv option, logging any errors."""
    try:
        player[key] = value
        return True
    except Exception:
        _LOGGER.warning("Failed to set mpv option %r=%r", key, value, exc_info=True)
        return False

def _select_backend(player: MPV, device: Optional[str]) -> None:
    """
    Selects the best available audio output backend for mpv.
    
    1. If a specific 'device' is given (e.g., 'alsa/plughw:CARD=...'), it is used directly.
    2. If 'device' is None, it queries mpv for available devices and picks the first
       available backend in the preferred order (pipewire > pulse > alsa).
    3. If all else fails, it falls back to 'auto'.
    """
    
    # 1. Explicit Path: User provided a specific device. Use it directly.
    if device:
        _LOGGER.debug(f"User specified audio device: {device}")
        try:
            # If it's a full path like 'alsa/plughw:...'
            if '/' in device:
                ao, _ = device.split('/', 1)
                player["ao"] = ao
                player["audio-device"] = device
                _LOGGER.info(f"mpv backend set (explicit): ao={ao}, audio-device={device}")
                return
            else:
                # Ambiguous name like 'default'. Fall through to auto-detection.
                _LOGGER.debug(f"Ambiguous device name '{device}', falling back to auto-detection.")
        except Exception as e:
            _LOGGER.warning(f"Failed to parse explicit audio device {device}: {e}. Falling back to auto-detection.")

    # 2. Automatic Path: No device specified or fallback from ambiguous name.
    _LOGGER.debug("Starting audio backend auto-detection.")
    
    available_device_names = set()
    try:
        available_devices = player.audio_device_list
        if not available_devices:
            _LOGGER.warning("mpv returned an empty list of audio devices. Will try defaults.")
        else:
            available_device_names = {dev['name'] for dev in available_devices}
            _LOGGER.debug(f"Available audio devices found: {available_device_names}")
    except Exception as e:
        _LOGGER.error(f"Failed to query mpv for audio devices: {e}. Will try defaults.")

    # Our preferred order of drivers
    preferred_drivers = ["pipewire", "pulse", "alsa"]
    
    if available_device_names:
        for driver in preferred_drivers:
            # Check if ANY device in the list starts with this driver's prefix
            prefix = f"{driver}/"
            # Also check for the simple driver name, e.g., 'alsa'
            if any(name.startswith(prefix) for name in available_device_names) or (driver in available_device_names):
                try:
                    # --- THIS IS THE FIX ---
                    # For pulse and pipewire, set audio-device to "default"
                    # For alsa, it's safer to use "alsa" as the device
                    device_name = "default" if driver in ["pipewire", "pulse"] else driver
                    
                    player["ao"] = driver
                    player["audio-device"] = device_name
                    _LOGGER.info(f"Auto-detected and set active backend: ao={driver}, audio-device={device_name}")
                    return
                except Exception as e:
                    _LOGGER.warning(f"Failed to set auto-detected driver {driver}: {e}")

    # 3. Fallback Path: No devices found, or no prefix matched.
    _LOGGER.warning("Could not find a preferred driver in mpv's list. Telling mpv to use 'auto'.")
    player["ao"] = "auto"
    player["audio-device"] = "auto"


class MpvMediaPlayer:
    """A media player class that wraps the python-mpv library."""
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        device: Optional[str] = None,
        initial_volume: float = 1.0
    ) -> None:
        self.loop = loop
        self.player = MPV(
            video=False,
            terminal=False,
            log_handler=self._mpv_log,
            # Set options directly in the constructor
            audio_samplerate=44100,
            audio_channels="stereo",
            keep_open="no",
            network_timeout=7,
            msg_level=os.environ.get("LVA_MPV_MSG_LEVEL", "all=warn"),
        )

        _select_backend(self.player, device) # <-- This is the updated function
        self.set_volume(int(initial_volume * 100))

        self.is_playing: bool = False
        self._playlist: List[str] = []
        self._done_callback: Optional[Callable[[], None]] = None
        self._done_callback_lock = Lock()
        self._pre_duck_volume: Optional[int] = None

        self.player.observe_property("idle-active", self._on_idle_active)

    def play(
        self,
        url: Union[str, Sequence[str], bytes],
        done_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """Plays a URL or a sequence of URLs."""
        self.stop() # Ensure player is in a clean state

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
        self.player.playlist_pos = 0 # Start playing from the first item
        
    def pause(self) -> None:
        """Pauses playback."""
        self.player.pause = True

    def resume(self) -> None:
        """Resumes playback."""
        self.player.pause = False

    def stop(self) -> None:
        """Stops playback and clears the playlist."""
        if self.is_playing:
            self.is_playing = False
            self.player.playlist_clear()
            self.player.command("stop")
            self._run_done_callback()
            
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
        finally:
            self._pre_duck_volume = None

    def _on_idle_active(self, _name: str, active: bool) -> None:
        """Callback triggered when mpv enters or leaves the idle state."""
        if active and self.is_playing:
            _LOGGER.debug("mpv became idle; treating as end-of-playback")
            self.is_playing = False
            self._run_done_callback()

    def _run_done_callback(self) -> None:
        """Safely runs the done_callback on the main asyncio loop."""
        with self._done_callback_lock:
            cb = self._done_callback
            self._done_callback = None
        if cb:
            try:
                # Use call_soon_threadsafe to run the callback on the main loop
                self.loop.call_soon_threadsafe(cb)
            except Exception:
                _LOGGER.exception("Error scheduling done_callback")

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
