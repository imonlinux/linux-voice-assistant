"""XVF3800 USB-based button & mute controller.

This module integrates the ReSpeaker XVF3800's built-in mute/mic-control
with the Linux Voice Assistant (LVA):

- Observes the XVF3800's GPO state to detect hardware mute changes.
- Publishes "set_mic_mute" events on the EventBus when the user presses
  the physical mute button on the XVF3800.
- Listens for "mic_muted"/"mic_unmuted" events and mirrors the LVA's
  mute state back to the XVF3800 via its USB control interface.

The XVF3800's GPIO mapping (from Seeed docs) is:

  GPI (inputs):
    X1D09 - Mute button state (high = released, low = pressed)
    X1D13 - Floating
    X1D34 - Floating

  GPO (outputs):
    X0D11 - Floating
    X0D30 - Mic mute + red mute LED (high = muted, LED on)
    X0D31 - Amplifier enable (low = enabled)
    X0D33 - WS2812 LED power enable
    X0D39 - Floating

We do NOT try to interpret short/long presses like the generic GPIO
ButtonController; the XVF3800 mute button is treated as "mute toggle only".
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

import usb.core  # type: ignore[import]
import usb.util  # type: ignore[import]

from .event_bus import EventBus, EventHandler, subscribe

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level USB client (minimal subset of the XMOS/Seeed control protocol)
# ---------------------------------------------------------------------------


class XVF3800USBClient:
    """Minimal USB control client for the ReSpeaker XVF3800.

    This wraps the vendor-specific control transfers needed for:
      - GPO_READ_VALUES  (read mic mute LED / amp / LED power pins)
      - GPO_WRITE_VALUE  (set mic mute LED pin)

    It deliberately does NOT touch interfaces/altsettings so that audio
    streaming over USB is unaffected.
    """

    VENDOR_ID = 0x2886
    PRODUCT_ID = 0x001A
    TIMEOUT_MS = 1000

    # From Seeed's documentation (GPO control):
    #   - Resid = 20 (GPO_SERVICER_RESID)
    #   - CmdId = 0  -> GPO_READ_VALUES  (3/5 bytes depending on doc version)
    #   - CmdId = 1  -> GPO_WRITE_VALUE
    #
    # We expect GPO_READ_VALUES to return 5 bytes:
    #   [X0D11, X0D30, X0D31, X0D33, X0D39]
    #
    GPO_RESID = 20
    GPO_READ_CMDID = 0
    GPO_WRITE_CMDID = 1
    GPO_NUM_PINS = 5

    # Index of the mic mute/red LED pin in the GPO_READ_VALUES response
    # (X0D30, second element).
    GPO_MUTE_INDEX = 1
    # Index of the WS2812 LED power enable pin in the response (X0D33).
    GPO_WS2812_POWER_INDEX = 3

    def __init__(self) -> None:
        dev = usb.core.find(idVendor=self.VENDOR_ID, idProduct=self.PRODUCT_ID)
        if dev is None:
            raise RuntimeError(
                "ReSpeaker XVF3800 USB device not found "
                f"(VID=0x{self.VENDOR_ID:04X}, PID=0x{self.PRODUCT_ID:04X})"
            )

        self._dev = dev
        _LOGGER.debug(
            "Initialized XVF3800USBClient (bus=%s, address=%s)",
            getattr(dev, "bus", "?"),
            getattr(dev, "address", "?"),
        )

    # CRITICAL FIX: Add context manager support
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    def close(self) -> None:
        """Dispose of USB resources."""
        if hasattr(self, '_dev') and self._dev is not None:
            try:
                usb.util.dispose_resources(self._dev)
            except Exception as e:
                _LOGGER.debug("Error disposing XVF3800 USB resources: %s", e)
            finally:
                self._dev = None

    # Internal helpers -----------------------------------------------------

    def _ctrl_read(self, resid: int, cmdid: int, length: int) -> List[int]:
        """Perform a vendor-specific control IN transfer and return payload bytes."""
        # Per XMOS protocol: read cmdid is (0x80 | cmdid)
        wValue = 0x80 | cmdid
        wIndex = resid

        data = self._dev.ctrl_transfer(
            usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            wValue,
            wIndex,
            length,
            self.TIMEOUT_MS,
        )
        if not data:
            raise RuntimeError("Empty response from XVF3800 vendor control read")

        status = data[0]
        # 0 = CONTROL_SUCCESS, 64 = SERVICER_COMMAND_RETRY in XMOS docs,
        # but for our simple use case we just require success.
        if status != 0:
            raise RuntimeError(f"Unexpected XVF3800 control status: {status}")

        # Return the payload bytes as a Python list, excluding status byte.
        return list(data[1:])

    def _ctrl_write(self, resid: int, cmdid: int, payload: bytes) -> None:
        """Perform a vendor-specific control OUT transfer."""
        wValue = cmdid
        wIndex = resid

        # Note: ctrl_transfer will raise usb.core.USBError on failure.
        self._dev.ctrl_transfer(
            usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            wValue,
            wIndex,
            payload,
            self.TIMEOUT_MS,
        )

    # Public API -----------------------------------------------------------

    def read_gpo_values(self) -> List[int]:
        """Read all GPO pin values.

        Returns a list of integers, one per pin:
          [X0D11, X0D30, X0D31, X0D33, X0D39]
        """
        length = self.GPO_NUM_PINS + 1  # +1 for status byte
        values = self._ctrl_read(self.GPO_RESID, self.GPO_READ_CMDID, length)
        if len(values) < self.GPO_NUM_PINS:
            _LOGGER.warning(
                "XVF3800 GPO_READ_VALUES returned %d bytes, expected >= %d",
                len(values),
                self.GPO_NUM_PINS,
            )
        return values

    def set_gpo_pin(self, pin: int, value: bool) -> bool:
        """Set a single GPO pin high/low.

        Args:
          pin: GPO pin number (e.g., 30 for X0D30, 33 for X0D33)
          value: True -> high, False -> low

        Returns:
          True on success, False on error.
        """
        payload = bytes([pin, 1 if value else 0])
        try:
            self._ctrl_write(self.GPO_RESID, self.GPO_WRITE_CMDID, payload)
            return True
        except usb.core.USBError as err:
            _LOGGER.error("USBError writing XVF3800 GPO pin %s value: %s", pin, err)
        except Exception:
            _LOGGER.exception("Unexpected error writing XVF3800 GPO pin %s value", pin)
        return False

    def get_mute_gpo(self) -> Optional[bool]:
        """Return the current mic-mute output state from X0D30.

        Returns:
          True  -> mics muted / red mute LED on
          False -> mics unmuted / red mute LED off
          None  -> could not read values (USB error)
        """
        try:
            values = self.read_gpo_values()
            if len(values) <= self.GPO_MUTE_INDEX:
                _LOGGER.error(
                    "XVF3800 GPO_READ_VALUES payload too short: %r", values
                )
                return None
            mute_val = values[self.GPO_MUTE_INDEX]
            return bool(mute_val)
        except usb.core.USBError as err:
            _LOGGER.error("USBError reading XVF3800 GPO values: %s", err)
        except Exception:
            _LOGGER.exception("Unexpected error reading XVF3800 GPO values")
        return None

    def set_mute_gpo(self, muted: bool) -> bool:
        """Set the mic-mute output (X0D30) high/low."""
        return self.set_gpo_pin(30, muted)


# ---------------------------------------------------------------------------
# High-level controller: bridge between XVF3800 and LVA mute state
# ---------------------------------------------------------------------------


@dataclass
class XVF3800ButtonRuntimeConfig:
    """Runtime config for the XVF3800 button/mute controller."""

    # CRITICAL FIX: Change default from 0.01s to 0.05s (100 Hz -> 20 Hz)
    # 80% reduction in CPU usage for button monitoring
    poll_interval_seconds: float = 0.05  # 20 Hz polling


class XVF3800ButtonController(EventHandler):
    """Monitor & synchronize the XVF3800's mute state with LVA."""

    def __init__(
        self,
        loop,
        event_bus: EventBus,
        state: "ServerState",
        button_config,
    ) -> None:
        super().__init__(event_bus)
        self.loop = loop
        self.state = state

        # CRITICAL FIX: Default to 0.05s instead of 0.01s
        poll_interval = 0.05
        if hasattr(button_config, "poll_interval_seconds"):
            try:
                poll_interval = float(button_config.poll_interval_seconds)
            except Exception:
                _LOGGER.warning(
                    "Invalid poll_interval_seconds in button config; "
                    "defaulting to %.3fs",
                    poll_interval,
                )

        self._cfg = XVF3800ButtonRuntimeConfig(
            poll_interval_seconds=poll_interval,
        )

        self._thread: Optional[threading.Thread] = None
        self._shutdown_flag = threading.Event()

        self._last_hw_muted: Optional[bool] = None

        self._target_mute_state_lock = threading.Lock()
        self._target_mute_state: Optional[bool] = None

        self._usb_client: Optional[XVF3800USBClient] = None

        self._thread = threading.Thread(
            target=self._poll_loop,
            name="XVF3800ButtonControllerThread",
            daemon=True,
        )
        self._thread.start()
        _LOGGER.info(
            "XVF3800ButtonController started (poll_interval=%.3fs)",
            self._cfg.poll_interval_seconds,
        )

        self._subscribe_all_methods()

    # ------------------------------------------------------------------
    # Event handlers from EventBus
    # ------------------------------------------------------------------

    @subscribe
    def mic_muted(self, data: dict) -> None:
        self._set_target_mute_state(True)

    @subscribe
    def mic_unmuted(self, data: dict) -> None:
        self._set_target_mute_state(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._shutdown_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._usb_client is not None:
            self._usb_client.close()
        _LOGGER.info("XVF3800ButtonController stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_target_mute_state(self, muted: bool) -> None:
        with self._target_mute_state_lock:
            self._target_mute_state = muted

    def _take_target_mute_state(self) -> Optional[bool]:
        with self._target_mute_state_lock:
            value = self._target_mute_state
            self._target_mute_state = None
            return value

    def _ensure_usb_client(self) -> Optional[XVF3800USBClient]:
        if self._usb_client is not None:
            return self._usb_client

        try:
            self._usb_client = XVF3800USBClient()
            _LOGGER.info("Connected to ReSpeaker XVF3800 for mute control")
        except Exception:
            _LOGGER.exception(
                "Failed to initialize XVF3800 USB client; "
                "mute button integration will be disabled"
            )
            self._usb_client = None
        return self._usb_client

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        _LOGGER.debug("XVF3800ButtonController polling thread started")

        while not self._shutdown_flag.is_set() and not getattr(
            self.state, "shutdown", False
        ):
            client = self._ensure_usb_client()
            if client is None:
                time.sleep(2.0)
                continue

            # 1) Apply any pending target state from LVA -> hardware
            target = self._take_target_mute_state()
            if target is not None:
                if self._last_hw_muted is None or target != self._last_hw_muted:
                    success = client.set_mute_gpo(target)
                    if success:
                        self._last_hw_muted = target
                        _LOGGER.debug(
                            "Set XVF3800 hardware mute state -> %s", target
                        )

            # 2) Read current hardware GPO values (mute + WS2812 power)
            try:
                values = client.read_gpo_values()
                hw_muted = None
                if len(values) > client.GPO_MUTE_INDEX:
                    hw_muted = bool(values[client.GPO_MUTE_INDEX])

                # If the firmware/button toggles WS2812 power off, re-enable it so
                # the ring can still display LVA state (mute, idle effects, etc.)
                if len(values) > client.GPO_WS2812_POWER_INDEX:
                    ws2812_power = bool(values[client.GPO_WS2812_POWER_INDEX])
                    if not ws2812_power:
                        if client.set_gpo_pin(33, True):
                            _LOGGER.debug("Re-enabled XVF3800 WS2812 LED power (X0D33)")
                else:
                    ws2812_power = None

            except usb.core.USBError as err:
                _LOGGER.error("USBError reading XVF3800 GPO values: %s", err)
                hw_muted = None
            except Exception:
                _LOGGER.exception("Unexpected error reading XVF3800 GPO values")
                hw_muted = None

            if hw_muted is not None:
                if self._last_hw_muted is None:
                    self._last_hw_muted = hw_muted
                    _LOGGER.info(
                        "Initial XVF3800 hardware mute state: %s", hw_muted
                    )
                    self.loop.call_soon_threadsafe(
                        self.state.event_bus.publish,
                        "set_mic_mute",
                        {"state": hw_muted, "source": "xvf3800_hw"},
                    )
                elif hw_muted != self._last_hw_muted:
                    _LOGGER.info(
                        "Detected XVF3800 mute state change from %s to %s; "
                        "publishing set_mic_mute event",
                        self._last_hw_muted,
                        hw_muted,
                    )
                    self._last_hw_muted = hw_muted
                    self.loop.call_soon_threadsafe(
                        self.state.event_bus.publish,
                        "set_mic_mute",
                        {"state": hw_muted, "source": "xvf3800_hw"},
                    )

            time.sleep(self._cfg.poll_interval_seconds)

        _LOGGER.debug("XVF3800ButtonController polling thread exiting")
