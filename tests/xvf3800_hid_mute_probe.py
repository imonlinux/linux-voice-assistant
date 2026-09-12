#!/usr/bin/env python3
"""ReSpeaker XVF3800 HID mute/button probe.

This script helps reverse-engineer how the XVF3800 exposes its mute button over USB HID.
It is meant to be run from the linux-voice-assistant virtualenv on a system where the
XVF3800 is connected via USB.

Requirements:
    pip install hidapi

Usage examples:

    # Basic usage (auto-detect first 2886:001a HID interface)
    python tests/xvf3800_hid_mute_probe.py

    # Explicit vendor/product (optional, defaults are correct for XVF3800)
    python tests/xvf3800_hid_mute_probe.py --vendor-id 0x2886 --product-id 0x001a

Once running, press and release the XVF3800 mute button a few times. The script will
print raw HID reports in hex. Share that output in the Discussion so we can identify
which byte/bit corresponds to the mute state.
"""

import argparse
import binascii
import datetime as dt
import sys
import time
from typing import List, Optional

try:
    import hid  # type: ignore[import]
except Exception as exc:  # pragma: no cover - environment dependent
    print("ERROR: Failed to import 'hid' (hidapi).", file=sys.stderr)
    print("       Install it with: pip install hidapi", file=sys.stderr)
    print(f"       Details: {exc}", file=sys.stderr)
    sys.exit(1)


def _fmt_vid_pid(vid: int, pid: int) -> str:
    return f"0x{vid:04x}:0x{pid:04x}"


def list_matching_devices(vendor_id: int, product_id: int) -> List[dict]:
    devices = []
    for dev in hid.enumerate():
        if dev.get("vendor_id") == vendor_id and dev.get("product_id") == product_id:
            devices.append(dev)
    return devices


def choose_device(devices: List[dict]) -> Optional[dict]:
    """Pick a device from the list; if only one, return it, else pick the first.

    We log all candidates so users can see what's on their system.
    """
    if not devices:
        return None

    print("Found the following matching HID interfaces for XVF3800:")
    for idx, dev in enumerate(devices):
        path = dev.get("path")
        serial = dev.get("serial_number") or "<none>"
        mfg = dev.get("manufacturer_string") or "<unknown>"
        prod = dev.get("product_string") or "<unknown>"
        iface = dev.get("interface_number", -1)
        print(
            f"  [{idx}] path={path!r}, serial={serial!r}, iface={iface}, "
            f"manufacturer={mfg!r}, product={prod!r}"
        )

    print()
    print("NOTE: Using index 0 by default. If this is not the HID interface, ")
    print("      rerun with --path <exact path> from the list above.\n")
    return devices[0]


def open_device(
    vendor_id: int,
    product_id: int,
    path: Optional[bytes] = None,
) -> hid.device:
    """Open a HID device for the given VID/PID (and optional path)."""
    dev = hid.device()
    if path is not None:
        print(f"Opening HID device by path: {path!r}")
        dev.open_path(path)
    else:
        print(f"Opening HID device by VID/PID: {_fmt_vid_pid(vendor_id, product_id)}")
        dev.open(vendor_id, product_id)

    dev.set_nonblocking(True)
    return dev


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe HID reports for the ReSpeaker XVF3800 mute button."
    )
    parser.add_argument(
        "--vendor-id",
        type=lambda x: int(x, 0),
        default=0x2886,
        help="USB vendor ID (default: 0x2886 for Seeed)",
    )
    parser.add_argument(
        "--product-id",
        type=lambda x: int(x, 0),
        default=0x001a,
        help="USB product ID (default: 0x001a for XVF3800)",
    )
    parser.add_argument(
        "--path",
        type=str,
        default="",
        help="Optional exact HID path to open (overrides VID/PID selection)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=500,
        help="Read timeout in milliseconds (default: 500)",
    )
    args = parser.parse_args()

    print("=== XVF3800 HID Mute Probe ===")
    print(f"Vendor/Product: {_fmt_vid_pid(args.vendor_id, args.product_id)}")

    if args.path:
        chosen_path: Optional[bytes] = args.path.encode("utf-8")
    else:
        devices = list_matching_devices(args.vendor_id, args.product_id)
        if not devices:
            print(
                "No HID interfaces found for XVF3800 (VID/PID). Make sure the board is plugged in.",
                file=sys.stderr,
            )
            return 1
        chosen = choose_device(devices)
        if chosen is None:
            print("No device chosen.", file=sys.stderr)
            return 1
        chosen_path = chosen.get("path")
        print("Using device:")
        print(f"  path={chosen_path!r}")
        print(f"  serial={chosen.get('serial_number')!r}")
        print(f"  interface_number={chosen.get('interface_number')}")
        print()

    try:
        dev = open_device(args.vendor_id, args.product_id, chosen_path)
    except Exception as exc:  # pragma: no cover - hardware dependent
        print(f"ERROR: Failed to open HID device: {exc}", file=sys.stderr)
        return 1

    print("Listening for HID reports. Press and release the XVF3800 mute button a few times.")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            data = dev.read(64, timeout_ms=args.timeout_ms)
            if data:
                now = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                raw = bytes(data)
                hex_str = binascii.hexlify(raw).decode("ascii")
                # Trim trailing zeroes for readability
                hex_str = hex_str.rstrip("0") or hex_str
                print(f"[{now}] len={len(raw):02d} report={hex_str}")
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nStopping probe.")
    finally:
        try:
            dev.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
