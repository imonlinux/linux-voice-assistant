"""XVF3800 USB LED ring backend.

This backend supports two generations of LED control:

1) Legacy/global LED control (older firmware):
   - LED_EFFECT, LED_BRIGHTNESS, LED_SPEED, LED_COLOR

2) Per-LED ring control (newer firmware):
   - LED_RING_COLOR: 12x uint32 values, one per LED (0xRRGGBB)

We intentionally keep the implementation minimal and self-contained so it can be
used by LVA without shelling out to xvf_host.py.
"""

from __future__ import annotations

import logging
import struct
from typing import List, Optional, Sequence, Tuple

import usb.core  # type: ignore[import]
import usb.util  # type: ignore[import]

_LOGGER = logging.getLogger(__name__)

CONTROL_SUCCESS = 0
SERVICER_COMMAND_RETRY = 64

# name -> (resid, cmdid, count, access, type)
PARAMETERS = {
    # ---------------------------------------------------------------------
    # APPLICATION_SERVICER_RESID (core info)
    # ---------------------------------------------------------------------
    "VERSION": (48, 0, 3, "ro", "uint8"),

    # ---------------------------------------------------------------------
    # GPO_SERVICER_RESID (LED controls)
    # ---------------------------------------------------------------------
    # Legacy/global controls
    "LED_EFFECT":     (20, 12, 1, "rw", "uint8"),
    "LED_BRIGHTNESS": (20, 13, 1, "rw", "uint8"),
    "LED_GAMMIFY":    (20, 14, 1, "rw", "uint8"),
    "LED_SPEED":      (20, 15, 1, "rw", "uint8"),
    "LED_COLOR":      (20, 16, 1, "rw", "uint32"),

    # Newer firmware: per-LED ring control (12 WS2812 LEDs)
    "LED_RING_COLOR": (20, 19, 12, "rw", "uint32"),
}


class _ReSpeaker:
    """Low-level USB control wrapper for XVF3800 parameters."""

    TIMEOUT_MS = 100_000
    VID = 0x2886
    PID = 0x001A

    def __init__(self, dev: "usb.core.Device") -> None:  # type: ignore[name-defined]
        self.dev = dev

    # ------------------------------------------------------------------
    # Encoding / decoding helpers
    # ------------------------------------------------------------------

    def _pack_values(self, data_type: str, values: Sequence[int]) -> bytes:
        if data_type == "uint8":
            return bytes([int(v) & 0xFF for v in values])
        if data_type == "uint32":
            return b"".join(struct.pack("<I", int(v) & 0xFFFFFFFF) for v in values)
        if data_type == "int32":
            return b"".join(struct.pack("<i", int(v)) for v in values)
        raise ValueError(f"Unsupported data type '{data_type}'")

    def _unpack_values(self, data_type: str, raw: bytes, count: int) -> List[int]:
        if data_type == "uint8":
            return list(raw[:count])
        if data_type == "uint32":
            return list(struct.unpack("<" + "I" * count, raw[: count * 4]))
        if data_type == "int32":
            return list(struct.unpack("<" + "i" * count, raw[: count * 4]))
        raise ValueError(f"Unsupported data type '{data_type}'")

    def _read_length(self, data_type: str, count: int) -> int:
        # +1 for status byte returned by XMOS vendor control read
        if data_type == "uint8":
            return count + 1
        if data_type in ("uint32", "int32"):
            return (count * 4) + 1
        raise ValueError(f"Unsupported data type '{data_type}'")

    # ------------------------------------------------------------------
    # Public parameter IO
    # ------------------------------------------------------------------

    def write(self, name: str, data_list: Sequence[int]) -> None:
        try:
            resid, cmdid, count, access, data_type = PARAMETERS[name]
        except KeyError as exc:
            raise ValueError(f"Unknown XVF3800 parameter '{name}'") from exc

        if access == "ro":
            raise ValueError(f"{name} is read-only")

        if len(data_list) != count:
            raise ValueError(f"{name} expects {count} values, got {len(data_list)}")

        payload = self._pack_values(data_type, data_list)

        _LOGGER.debug(
            "XVF3800 write: name=%s resid=%s cmdid=%s payload_len=%d",
            name,
            resid,
            cmdid,
            len(payload),
        )

        self.dev.ctrl_transfer(
            usb.util.CTRL_OUT
            | usb.util.CTRL_TYPE_VENDOR
            | usb.util.CTRL_RECIPIENT_DEVICE,
            0,
            cmdid,
            resid,
            payload,
            self.TIMEOUT_MS,
        )

    def read(self, name: str, max_retries: int = 10) -> List[int]:
        try:
            resid, cmdid, count, access, data_type = PARAMETERS[name]
        except KeyError as exc:
            raise ValueError(f"Unknown XVF3800 parameter '{name}'") from exc

        if access == "wo":
            raise ValueError(f"{name} is write-only")

        length = self._read_length(data_type, count)
        wValue = 0x80 | cmdid  # per XMOS protocol: read is (0x80 | cmdid)

        attempt = 0
        while True:
            attempt += 1
            resp = self.dev.ctrl_transfer(
                usb.util.CTRL_IN
                | usb.util.CTRL_TYPE_VENDOR
                | usb.util.CTRL_RECIPIENT_DEVICE,
                0,
                wValue,
                resid,
                length,
                self.TIMEOUT_MS,
            )

            if not resp:
                raise RuntimeError("Empty response from XVF3800 control read")

            status = int(resp[0])
            if status == CONTROL_SUCCESS:
                raw = bytes(resp[1:])
                return self._unpack_values(data_type, raw, count)

            if status == SERVICER_COMMAND_RETRY and attempt < max_retries:
                continue

            raise RuntimeError(f"XVF3800 control read failed (status={status}, name={name})")

    def close(self) -> None:
        usb.util.dispose_resources(self.dev)


def _find_device(vid: int = _ReSpeaker.VID, pid: int = _ReSpeaker.PID) -> Optional[_ReSpeaker]:
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if not dev:
        return None
    return _ReSpeaker(dev)


class XVF3800LedBackend:
    """High-level LED backend for the XVF3800."""

    ring_led_count: int = 12

    def __init__(self, vid: int = _ReSpeaker.VID, pid: int = _ReSpeaker.PID) -> None:
        wrapper = _find_device(vid, pid)
        if wrapper is None:
            raise RuntimeError(
                f"XVF3800 USB device not found (vid=0x{vid:04x}, pid=0x{pid:04x})"
            )

        self._dev = wrapper
        self.supports_per_led: bool = False

        # Best-effort feature detection: if we can read LED_RING_COLOR, we assume
        # per-LED control is supported by the current firmware.
        try:
            _ = self._dev.read("LED_RING_COLOR")
            self.supports_per_led = True
        except Exception:
            self.supports_per_led = False

        # Best-effort version read (not fatal)
        try:
            ver = self._dev.read("VERSION")
            _LOGGER.info(
                "Connected to XVF3800 USB device (vid=0x%04x, pid=0x%04x, version=%s, per_led=%s)",
                vid,
                pid,
                ".".join(str(int(x)) for x in ver[:3]),
                self.supports_per_led,
            )
        except Exception:
            _LOGGER.info(
                "Connected to XVF3800 USB device (vid=0x%04x, pid=0x%04x, per_led=%s)",
                vid,
                pid,
                self.supports_per_led,
            )

    # ---------------------------------------------------------------------
    # Legacy/global controls
    # ---------------------------------------------------------------------

    def set_effect(self, effect_id: int) -> None:
        """Set LED effect mode (0=off, 1=breath, 2=rainbow, 3=single color, 4=doa)."""
        self._dev.write("LED_EFFECT", [int(effect_id) & 0xFF])

    def set_brightness(self, brightness_0_255: int) -> None:
        """Set LED brightness (0-255)."""
        value = max(0, min(255, int(brightness_0_255)))
        self._dev.write("LED_BRIGHTNESS", [value])

    def set_speed(self, speed_id: int) -> None:
        """Set LED effect speed (0=slow, 1=medium, 2=fast)."""
        self._dev.write("LED_SPEED", [int(speed_id) & 0xFF])

    def set_color(self, r: int, g: int, b: int) -> None:
        """Set LED color for breath / single color modes (0xRRGGBB)."""
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        color_value = (r << 16) | (g << 8) | b
        self._dev.write("LED_COLOR", [color_value])

    # ---------------------------------------------------------------------
    # Per-LED ring control (newer firmware)
    # ---------------------------------------------------------------------

    def set_ring_colors(self, color_values: Sequence[int]) -> None:
        """Set all 12 ring LEDs with 0xRRGGBB values (length must be 12)."""
        if not self.supports_per_led:
            raise RuntimeError("Per-LED ring control is not supported by this firmware")
        if len(color_values) != self.ring_led_count:
            raise ValueError(
                f"LED_RING_COLOR expects {self.ring_led_count} values, got {len(color_values)}"
            )
        self._dev.write("LED_RING_COLOR", [int(v) & 0xFFFFFFFF for v in color_values])

    def set_ring_rgb(self, colors: Sequence[Tuple[int, int, int]]) -> None:
        """Set all 12 ring LEDs with (r,g,b) tuples (length must be 12)."""
        if len(colors) != self.ring_led_count:
            raise ValueError(
                f"Ring expects {self.ring_led_count} colors, got {len(colors)}"
            )
        vals: List[int] = []
        for r, g, b in colors:
            r = max(0, min(255, int(r)))
            g = max(0, min(255, int(g)))
            b = max(0, min(255, int(b)))
            vals.append((r << 16) | (g << 8) | b)
        self.set_ring_colors(vals)

    def set_ring_solid(self, r: int, g: int, b: int) -> None:
        """Convenience: set all ring LEDs to the same RGB color."""
        self.set_ring_rgb([(r, g, b)] * self.ring_led_count)

    def clear_ring(self) -> None:
        """Convenience: turn all ring LEDs off (per-LED mode)."""
        if not self.supports_per_led:
            # Legacy fallback: just set effect off
            self.set_effect(0)
            self.set_brightness(0)
            return
        self.set_ring_colors([0] * self.ring_led_count)

    def get_version(self) -> Optional[Tuple[int, int, int]]:
        """Return (major, minor, patch) if readable, else None."""
        try:
            ver = self._dev.read("VERSION")
            if len(ver) >= 3:
                return (int(ver[0]), int(ver[1]), int(ver[2]))
        except Exception:
            return None
        return None

    def close(self) -> None:
        if self._dev is not None:
            self._dev.close()
