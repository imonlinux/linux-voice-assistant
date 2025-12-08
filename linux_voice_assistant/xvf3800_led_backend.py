import logging
import struct
from typing import Optional, Sequence

import usb.core
import usb.util

_LOGGER = logging.getLogger(__name__)

CONTROL_SUCCESS = 0
SERVICER_COMMAND_RETRY = 64

# name, resid, cmdid, length, access, type
PARAMETERS = {
    # GPO_SERVICER_RESID LED controls (legacy interface)
    "LED_EFFECT":     (20, 12, 1, "rw", "uint8"),
    "LED_BRIGHTNESS": (20, 13, 1, "rw", "uint8"),
    "LED_GAMMIFY":    (20, 14, 1, "rw", "uint8"),
    "LED_SPEED":      (20, 15, 1, "rw", "uint8"),
    "LED_COLOR":      (20, 16, 1, "rw", "uint32"),
}


class _ReSpeaker:
    """Low-level USB control wrapper for XVF3800 parameters we care about."""

    TIMEOUT = 100_000

    def __init__(self, dev: usb.core.Device) -> None:  # type: ignore[name-defined]
        self.dev = dev

    def write(self, name: str, data_list: Sequence[int]) -> None:
        try:
            data = PARAMETERS[name]
        except KeyError as exc:
            raise ValueError(f"Unknown XVF3800 parameter '{name}'") from exc

        resid, cmdid, count, access, data_type = data

        if access == "ro":
            raise ValueError(f"{name} is read-only")

        if len(data_list) != count:
            raise ValueError(f"{name} expects {count} values, got {len(data_list)}")

        payload = bytearray()

        if data_type == "uint8":
            for v in data_list:
                payload += int(v).to_bytes(1, byteorder="little", signed=False)
        elif data_type in ("uint32", "int32"):
            fmt = b"I" if data_type == "uint32" else b"i"
            for v in data_list:
                payload += struct.pack(fmt, int(v))
        else:
            # We don't actually use other types for LED controls.
            raise ValueError(f"Unsupported data type '{data_type}' for {name}")

        _LOGGER.debug(
            "XVF3800 write: name=%s resid=%s cmdid=%s payload=%s",
            name,
            resid,
            cmdid,
            list(payload),
        )

        self.dev.ctrl_transfer(
            usb.util.CTRL_OUT
            | usb.util.CTRL_TYPE_VENDOR
            | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            cmdid,
            resid,
            payload,
            self.TIMEOUT,
        )

    def close(self) -> None:
        usb.util.dispose_resources(self.dev)


def _find_device(vid: int = 0x2886, pid: int = 0x001A) -> Optional[_ReSpeaker]:
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev:
        return None
    return _ReSpeaker(dev)


class XVF3800LedBackend:
    """High-level LED backend for XVF3800 USB LED ring.

    This is intentionally minimal and only covers the legacy LED controls:
      - LED_EFFECT
      - LED_BRIGHTNESS
      - LED_SPEED
      - LED_COLOR
    """

    def __init__(self, vid: int = 0x2886, pid: int = 0x001A) -> None:
        wrapper = _find_device(vid, pid)
        if wrapper is None:
            raise RuntimeError(
                f"XVF3800 USB device not found (vid=0x{vid:04x}, pid=0x{pid:04x})"
            )

        self._dev = wrapper
        _LOGGER.info(
            "Connected to XVF3800 USB device (vid=0x%04x, pid=0x%04x)", vid, pid
        )

    # ---------------------------------------------------------------------
    # Public high-level controls
    # ---------------------------------------------------------------------

    def set_effect(self, effect_id: int) -> None:
        """Set LED effect mode (0=off, 1=breath, 2=rainbow, 3=single color, 4=doa)."""
        effect_id = int(effect_id) & 0xFF
        self._dev.write("LED_EFFECT", [effect_id])

    def set_brightness(self, brightness_0_255: int) -> None:
        """Set LED brightness (0-255)."""
        value = max(0, min(255, int(brightness_0_255)))
        self._dev.write("LED_BRIGHTNESS", [value])

    def set_speed(self, speed_id: int) -> None:
        """Set LED effect speed (0=slow, 1=medium, 2=fast)."""
        speed_id = int(speed_id) & 0xFF
        self._dev.write("LED_SPEED", [speed_id])

    def set_color(self, r: int, g: int, b: int) -> None:
        """Set LED color for breath / single color modes (0xRRGGBB)."""
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))

        color_value = (r << 16) | (g << 8) | b
        self._dev.write("LED_COLOR", [color_value])

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()

