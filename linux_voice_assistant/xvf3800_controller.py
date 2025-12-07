"""XVF3800 USB/HID controller scaffold.

This module is intended to integrate the ReSpeaker XVF3800 mute button and
mute LED with the Linux Voice Assistant (LVA) mute state.

Design goals:
  - HW mute button -> LVA:
      * When the user presses the XVF3800 mute button, the board mutes the
        mic and lights the red LED.
      * This controller detects the change over USB HID and publishes the
        standard "set_mic_mute" event so the existing MicMuteHandler can:
          - update ServerState.mic_muted
          - update ServerState.mic_muted_event
          - publish MQTT mute state
          - notify the tray, etc.

  - LVA -> HW:
      * When LVA toggles mute (via HA MQTT, ReSpeaker 2-Mic GPIO button, etc.),
        this controller receives the same "set_mic_mute" event and sends a
        USB HID command to synchronize the XVF3800's internal mute state and
        red LED.

This file is a scaffold:
  - The USB/HID read/write paths are structured, but the actual report
    format is not yet known.
  - TODOs are marked where the mute bit must be decoded or set.
  - Once the test script (xvf3800_hid_mute_probe.py) has revealed the
    HID report layout, implement _parse_hw_mute_from_report() and
    _send_hw_mute_state().
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .event_bus import EventBus, EventHandler, subscribe
from .models import ServerState

_LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - depends on platform / install
    import hid  # type: ignore[import]
except Exception:  # ImportError, RuntimeError, etc.
    hid = None  # type: ignore[assignment]


@dataclass
class Xvf3800RuntimeConfig:
    """Runtime configuration for the XVF3800 controller.

    This is intentionally simple; if we later add more XVF-specific options,
    they can be added here and wired from config.json.
    """  # noqa: D401

    enabled: bool = True
    vendor_id: int = 0x2886
    product_id: int = 0x001A
    read_timeout_ms: int = 200
    poll_interval_seconds: float = 0.02


class Xvf3800Controller(EventHandler):
    """Controller for the ReSpeaker XVF3800 USB/HID mute integration.

    Responsibilities:
      - Open the XVF3800 HID interface (if available and enabled).
      - Run a background thread that reads HID reports and detects mute
        state changes, publishing "set_mic_mute" events back into the LVA.
      - Listen for "set_mic_mute" events and push desired mute state down
        to the XVF3800 (muting the mic and controlling the red LED).

    Safe failure:
      - If the 'hid' module is not available, or no matching XVF3800 device
        is found, the controller logs an info message and stays inactive.
      - All errors in the polling thread are logged but do not crash LVA.
    """

    def __init__(
        self,
        loop,
        event_bus: EventBus,
        state: ServerState,
        config: Optional[Xvf3800RuntimeConfig] = None,
    ) -> None:
        super().__init__(event_bus)

        self.loop = loop
        self.state = state
        self._cfg = config or Xvf3800RuntimeConfig()

        self._thread: Optional[threading.Thread] = None
        self._hid_dev: Optional["hid.device"] = None  # type: ignore[name-defined]
        self._last_hw_mute_state: Optional[bool] = None
        self._running = False

        if not self._cfg.enabled:
            _LOGGER.info("Xvf3800Controller disabled in config; not initializing HID")
            return

        if hid is None:
            _LOGGER.info(
                "hidapi not available; XVF3800 mute integration disabled. "
                "Install it with 'pip install hidapi' if you want this feature.",
            )
            return

        # Attempt to open the XVF3800 HID interface.
        try:
            self._hid_dev = self._open_device(
                self._cfg.vendor_id,
                self._cfg.product_id,
            )
        except Exception:
            _LOGGER.exception(
                "Failed to open XVF3800 HID device (VID=0x%04x PID=0x%04x); "
                "mute integration disabled",
                self._cfg.vendor_id,
                self._cfg.product_id,
            )
            self._hid_dev = None

        if self._hid_dev is None:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="Xvf3800ControllerThread",
            daemon=True,
        )
        self._thread.start()

        # Subscribe to events AFTER init
        self._subscribe_all_methods()

        _LOGGER.info(
            "Xvf3800Controller initialized (VID=0x%04x PID=0x%04x)",
            self._cfg.vendor_id,
            self._cfg.product_id,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Stop the polling thread and close HID device (if any)."""
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._hid_dev is not None:
            try:
                self._hid_dev.close()
            except Exception:
                _LOGGER.exception("Error closing XVF3800 HID device")
        _LOGGER.debug("Xvf3800Controller stopped")

    # ------------------------------------------------------------------
    # HID setup
    # ------------------------------------------------------------------

    def _open_device(self, vendor_id: int, product_id: int):
        """Open the first HID interface that matches the XVF3800 VID/PID.

        If multiple HID interfaces are present, we currently pick the first
        one returned by hid.enumerate(). If this proves unreliable, we can
        refine selection using interface_number/product_string.
        """
        devices = [
            dev
            for dev in hid.enumerate()  # type: ignore[attr-defined]
            if dev.get("vendor_id") == vendor_id
            and dev.get("product_id") == product_id
        ]

        if not devices:
            _LOGGER.warning(
                "No HID interfaces found for XVF3800 (VID=0x%04x PID=0x%04x)",
                vendor_id,
                product_id,
            )
            return None

        dev_info = devices[0]
        path = dev_info.get("path")
        _LOGGER.info(
            "Opening XVF3800 HID device: path=%r serial=%r interface=%r",
            path,
            dev_info.get("serial_number"),
            dev_info.get("interface_number"),
        )

        dev = hid.device()  # type: ignore[call-arg]
        if path is not None:
            dev.open_path(path)
        else:
            dev.open(vendor_id, product_id)

        dev.set_nonblocking(True)
        return dev

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Background loop reading HID reports and detecting mute changes."""
        _LOGGER.debug("Xvf3800Controller polling thread started")

        assert self._hid_dev is not None

        while self._running and not getattr(self.state, "shutdown", False):
            try:
                data = self._hid_dev.read(  # type: ignore[union-attr]
                    64,
                    timeout_ms=self._cfg.read_timeout_ms,
                )
            except Exception:
                _LOGGER.exception("Error reading from XVF3800 HID device")
                break

            if data:
                # 'data' is typically a list of ints; convert to bytes for parsing.
                report = bytes(data)
                new_state = self._parse_hw_mute_from_report(report)
                if new_state is not None and new_state != self._last_hw_mute_state:
                    _LOGGER.debug(
                        "XVF3800 HW mute state changed: %s -> %s",
                        self._last_hw_mute_state,
                        new_state,
                    )
                    self._last_hw_mute_state = new_state

                    # Publish standard event; MicMuteHandler will take care
                    # of updating ServerState and emitting mic_muted/mic_unmuted.
                    self.loop.call_soon_threadsafe(
                        self.event_bus.publish,
                        "set_mic_mute",
                        {"state": new_state},
                    )

            time.sleep(self._cfg.poll_interval_seconds)

        _LOGGER.debug("Xvf3800Controller polling thread exiting")

    # ------------------------------------------------------------------
    # Parsing & writing HID reports (placeholders)
    # ------------------------------------------------------------------

    def _parse_hw_mute_from_report(self, report: bytes) -> Optional[bool]:
        """Extract the current mute state from a raw HID report.

        Returns:
            True  -> HW reports 'muted'
            False -> HW reports 'unmuted'
            None  -> This report does not contain mute information

        TODO: Implement this once we know the HID report layout. For now,
              it logs the report at DEBUG level and returns None.
        """
        _LOGGER.debug(
            "XVF3800 HID report (len=%d): %s",
            len(report),
            report.hex(),
        )
        # Once we have sample outputs from xvf3800_hid_mute_probe.py,
        # implement the bit extraction here.
        return None

    def _send_hw_mute_state(self, muted: bool) -> None:
        """Send a HID command to set the XVF3800 mute state + LED.

        TODO: Once we understand the HID output/report format (from docs or
              reverse engineering), construct and write the correct report.
              For now, this method just logs what it *would* do.
        """
        if self._hid_dev is None:
            return

        _LOGGER.debug(
            "(stub) Would send HID command to set XVF3800 mute=%s",
            muted,
        )
        # Example once we know the correct output report:
        # report_id = 0x01
        # payload = bytes([report_id, 0x01 if muted else 0x00, 0x00, 0x00, ...])
        # try:
        #     self._hid_dev.write(payload)
        # except Exception:
        #     _LOGGER.exception("Error writing XVF3800 HID mute command")

    # ------------------------------------------------------------------
    # EventBus subscriptions
    # ------------------------------------------------------------------

    @subscribe
    def set_mic_mute(self, data: dict) -> None:
        """Handle global 'set_mic_mute' events.

        This is the *same* event that MQTT, ButtonController, etc. use. By
        listening here, we can sync the XVF3800 hardware state with LVA's
        logical mute state.

        The central MicMuteHandler is still the source of truth; this
        controller should not modify ServerState directly, only attempt to
        mirror that state to the hardware.
        """
        desired = bool(data.get("state", False))
        _LOGGER.debug(
            "Xvf3800Controller received set_mic_mute event: desired=%s",
            desired,
        )

        # Remember the last state so we don't treat an echo from hardware as new.
        self._last_hw_mute_state = desired
        self._send_hw_mute_state(desired)
