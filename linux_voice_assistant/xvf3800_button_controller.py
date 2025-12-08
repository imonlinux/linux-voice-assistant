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
        """Set the mic-mute output (X0D30) high/low.

        Args:
          muted: True to mute mics + turn red LED on, False to unmute.

        Returns:
          True on success, False on error.
        """
        # Payload: [pin_index, value] as uint8 each
        payload = bytes([30, 1 if muted else 0])
        try:
            self._ctrl_write(self.GPO_RESID, self.GPO_WRITE_CMDID, payload)
            return True
        except usb.core.USBError as err:
            _LOGGER.error("USBError writing XVF3800 GPO mute value: %s", err)
        except Exception:
            _LOGGER.exception("Unexpected error writing XVF3800 GPO mute value")
        return False

    def close(self) -> None:
        """Dispose of USB resources."""
        try:
            usb.util.dispose_resources(self._dev)
        except Exception:
            # Not fatal; just log at debug level.
            _LOGGER.debug("Error disposing XVF3800 USB resources", exc_info=True)


# ---------------------------------------------------------------------------
# High-level controller: bridge between XVF3800 and LVA mute state
# ---------------------------------------------------------------------------


@dataclass
class XVF3800ButtonRuntimeConfig:
    """Runtime config for the XVF3800 button/mute controller."""

    poll_interval_seconds: float = 0.05  # How often to poll GPO state


class XVF3800ButtonController(EventHandler):
    """Monitor & synchronize the XVF3800's mute state with LVA.

    Responsibilities:

      - Poll X0D30 (GPO_READ_VALUES) on a background thread.
      - When the hardware mute state changes (e.g. user presses the
        XVF3800's mute button), publish "set_mic_mute" with the new
        state so MicMuteHandler can update ServerState, MQTT, LEDs, etc.
      - Listen for "mic_muted" and "mic_unmuted" events and mirror those
        changes back to the XVF3800 by setting X0D30 via GPO_WRITE_VALUE.

    IMPORTANT:
      - This controller does *not* implement short/long press semantics.
        The XVF3800's onboard button is treated as a dedicated mute
        toggle; wake/stop behavior still comes from wake words or a
        separate GPIO button.
    """

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

        # Poll interval comes from the button config if present.
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

        # Last known hardware mute state (bool) or None if unknown.
        self._last_hw_muted: Optional[bool] = None

        # Target mute state from LVA (set via mic_muted/mic_unmuted events).
        self._target_mute_state_lock = threading.Lock()
        self._target_mute_state: Optional[bool] = None

        # USB client is created lazily in the polling thread so that any
        # USB errors don't break __init__.
        self._usb_client: Optional[XVF3800USBClient] = None

        # Start polling thread immediately.
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

        # Subscribe to events *after* initialization is complete.
        self._subscribe_all_methods()

    # ------------------------------------------------------------------
    # Event handlers from EventBus
    # ------------------------------------------------------------------

    @subscribe
    def mic_muted(self, data: dict) -> None:
        """Mirror LVA mute -> XVF3800 hardware."""
        self._set_target_mute_state(True)

    @subscribe
    def mic_unmuted(self, data: dict) -> None:
        """Mirror LVA mute -> XVF3800 hardware."""
        self._set_target_mute_state(False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it."""
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
        """Request that the polling thread set the XVF3800 mute state."""
        with self._target_mute_state_lock:
            self._target_mute_state = muted

    def _take_target_mute_state(self) -> Optional[bool]:
        """Atomically fetch and clear the pending target mute state."""
        with self._target_mute_state_lock:
            value = self._target_mute_state
            self._target_mute_state = None
            return value

    def _ensure_usb_client(self) -> Optional[XVF3800USBClient]:
        """Lazy-initialize the USB client on the polling thread."""
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
        """Background loop to synchronize mute state with XVF3800."""
        _LOGGER.debug("XVF3800ButtonController polling thread started")

        while not self._shutdown_flag.is_set() and not getattr(
            self.state, "shutdown", False
        ):
            client = self._ensure_usb_client()
            if client is None:
                # If we failed to init USB, wait a bit and retry
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

            # 2) Read current hardware GPO mute state
            hw_muted = client.get_mute_gpo()
            if hw_muted is not None:
                if self._last_hw_muted is None:
                    # First observation; treat hardware as source of truth
                    self._last_hw_muted = hw_muted
                    _LOGGER.info(
                        "Initial XVF3800 hardware mute state: %s", hw_muted
                    )
                    # Align LVA state with hardware on first read
                    self.loop.call_soon_threadsafe(
                        self.state.event_bus.publish,
                        "set_mic_mute",
                        {"state": hw_muted},
                    )
                elif hw_muted != self._last_hw_muted:
                    # Hardware mute changed (button pressed)
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
                        {"state": hw_muted},
                    )

            time.sleep(self._cfg.poll_interval_seconds)

        _LOGGER.debug("XVF3800ButtonController polling thread exiting")

