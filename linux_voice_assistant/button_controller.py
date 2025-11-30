"""Hardware button controller (e.g. ReSpeaker 2-Mic HAT momentary switch).

This controller:
- Short press:
    * If audio is playing (TTS or music): stop playback (like Stop wake word).
    * Otherwise: start a new conversation (like a wake word).
- Long press:
    * Toggle microphone mute (via the existing set_mic_mute event).

Implementation note:
- Uses a simple polling loop with RPi.GPIO instead of kernel edge detection.
- On non-Raspberry Pi hosts (or where RPi.GPIO is unusable), the controller
  cleanly disables itself and logs an info message.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .event_bus import EventBus
from .models import ServerState

_LOGGER = logging.getLogger(__name__)

# Try to import RPi.GPIO, but gracefully handle *any* failure.
# On non-RPi hosts, RPi.GPIO may be installed but will raise RuntimeError
# at import time ("This module can only be run on a Raspberry Pi!").
try:  # pragma: no cover - behavior depends on host platform
    import RPi.GPIO as GPIO  # type: ignore[import]
except Exception:  # ImportError, RuntimeError, etc.
    GPIO = None  # type: ignore[assignment]


@dataclass
class ButtonRuntimeConfig:
    """Runtime-safe config wrapper for the hardware button."""
    enabled: bool
    pin: int
    long_press_seconds: float
    poll_interval_seconds: float = 0.01  # 100 Hz polling


class ButtonController:
    """
    Hardware button handler for the ReSpeaker 2-Mic HAT (or any GPIO button).

    This class:
      - Uses a background polling thread to watch the GPIO level.
      - Interprets level transitions as press/release.
      - Schedules actions on the asyncio loop via loop.call_soon_threadsafe().
      - Uses the EventBus to toggle mic mute.

    On non-RPi hosts (or when RPi.GPIO isn't usable), it is effectively a no-op.
    """

    def __init__(
        self,
        loop,
        event_bus: EventBus,
        state: ServerState,
        config,
    ) -> None:
        """
        :param loop: asyncio event loop (state.loop).
        :param event_bus: Global EventBus.
        :param state: Global ServerState.
        :param config: Config.button (ButtonConfig-like object).
        """
        self.loop = loop
        self.event_bus = event_bus
        self.state = state

        self._cfg = ButtonRuntimeConfig(
            enabled=getattr(config, "enabled", False),
            pin=getattr(config, "pin", 17),
            long_press_seconds=float(
                getattr(config, "long_press_seconds", 1.0)
            ),
            poll_interval_seconds=float(
                getattr(config, "poll_interval_seconds", 0.01)
                if hasattr(config, "poll_interval_seconds")
                else 0.01
            ),
        )

        self._thread: threading.Thread | None = None
        self._press_time: float | None = None
        self._last_level: int | None = None

        # If disabled in config, do nothing.
        if not self._cfg.enabled:
            _LOGGER.info(
                "ButtonController disabled in config; not initializing GPIO"
            )
            return

        # If RPi.GPIO is unavailable or not usable on this host, also do nothing.
        if GPIO is None:
            _LOGGER.info(
                "RPi.GPIO not available or not usable on this host; "
                "hardware button support disabled"
            )
            return

        try:
            GPIO.setmode(GPIO.BCM)
            # Assume button wired as active-low with internal pull-up (common for HATs)
            GPIO.setup(self._cfg.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            self._last_level = GPIO.input(self._cfg.pin)
            _LOGGER.info(
                "ButtonController initialized on GPIO pin %s "
                "(long_press_seconds=%.2f, poll_interval=%.3fs)",
                self._cfg.pin,
                self._cfg.long_press_seconds,
                self._cfg.poll_interval_seconds,
            )
        except Exception:
            _LOGGER.exception(
                "Failed to configure GPIO pin %s for button; "
                "hardware button support disabled",
                self._cfg.pin,
            )
            return

        # Start polling thread
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="ButtonControllerThread",
            daemon=True,
        )
        self._thread.start()

    # -------------------------------------------------------------------------
    # Polling loop
    # -------------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Background loop to poll the GPIO level and detect presses."""
        _LOGGER.debug("ButtonController polling thread started")

        while not getattr(self.state, "shutdown", False):
            try:
                level = GPIO.input(self._cfg.pin)  # type: ignore[call-arg]
            except Exception:
                _LOGGER.exception("Error reading GPIO pin %s", self._cfg.pin)
                break

            if self._last_level is None:
                self._last_level = level

            # Active-low: 0 = pressed, 1 = released
            if level != self._last_level:
                if level == 0:
                    self._on_pressed()
                else:
                    self._on_released()
                self._last_level = level

            time.sleep(self._cfg.poll_interval_seconds)

        _LOGGER.debug("ButtonController polling thread exiting")
        # Do NOT call GPIO.cleanup() here; other components may use GPIO as well.

    # -------------------------------------------------------------------------
    # "Interrupt-like" handlers called from polling thread
    # -------------------------------------------------------------------------

    def _on_pressed(self) -> None:
        self._press_time = time.monotonic()
        _LOGGER.debug("Button pressed (GPIO %s low)", self._cfg.pin)

    def _on_released(self) -> None:
        if self._press_time is None:
            return

        duration = time.monotonic() - self._press_time
        self._press_time = None
        _LOGGER.debug("Button released after %.3f seconds", duration)

        if duration >= self._cfg.long_press_seconds:
            self._handle_long_press()
        else:
            self._handle_short_press()

    # -------------------------------------------------------------------------
    # High-level behaviors
    # -------------------------------------------------------------------------

    def _handle_short_press(self) -> None:
        """Short press: wake / stop behavior."""
        # Snapshot playing state quickly
        tts_playing = bool(getattr(self.state.tts_player, "is_playing", False))
        music_playing = bool(getattr(self.state.music_player, "is_playing", False))

        if tts_playing or music_playing:
            _LOGGER.debug(
                "Button short press while audio playing "
                "(tts=%s, music=%s) -> stopping playback",
                tts_playing,
                music_playing,
            )

            # Stop TTS/timer via the same logic as the Stop wake word.
            if self.state.satellite is not None:
                self.loop.call_soon_threadsafe(self.state.satellite.stop)

            # Also stop any music playback controlled by the LVA.
            self.loop.call_soon_threadsafe(self.state.music_player.stop)
        else:
            _LOGGER.debug(
                "Button short press with no audio playing -> manual wakeup"
            )
            if self.state.satellite is not None:
                # Use a dedicated manual wakeup to avoid faking a wake-word object.
                self.loop.call_soon_threadsafe(
                    self.state.satellite.manual_wakeup, "button"
                )

    def _handle_long_press(self) -> None:
        """Long press: toggle mic mute."""
        new_state = not self.state.mic_muted
        _LOGGER.debug(
            "Button long press: toggling mic mute -> %s", new_state
        )

        # Use the standard set_mic_mute event so MicMuteHandler can:
        # - update ServerState.mic_muted
        # - control the mic_muted_event
        # - publish MQTT mute state
        self.loop.call_soon_threadsafe(
            self.event_bus.publish,
            "set_mic_mute",
            {"state": new_state},
        )
