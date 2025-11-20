"""Utility methods."""

import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)
_CACHED_MAC: Optional[str] = None


def get_mac_address() -> str:
    """
    Get the MAC address as a hex string (lowercase, no colons).
    Example: "b827eb123456"
    
    This method prioritizes physical Linux interfaces to ensure stability 
    across reboots, avoiding uuid.getnode()'s random fallback.
    """
    global _CACHED_MAC
    if _CACHED_MAC:
        return _CACHED_MAC

    # 1. Try reading from sysfs (Linux specific, most stable)
    # We look for common interface names first.
    interfaces = ["wlan0", "eth0", "end0", "enp", "wlp"]
    sys_net = Path("/sys/class/net")
    
    if sys_net.exists():
        # Sort to prefer 'wlan0' or 'eth0' over others
        found_devs = sorted([p.name for p in sys_net.iterdir()])
        
        # Reorder to prioritize preferred interfaces
        target_devs = []
        for pref in interfaces:
            for dev in found_devs:
                if dev.startswith(pref):
                    target_devs.append(dev)
        
        # Add any remaining devices (excluding loopback)
        for dev in found_devs:
            if dev not in target_devs and dev != "lo":
                target_devs.append(dev)

        for dev_name in target_devs:
            try:
                addr_path = sys_net / dev_name / "address"
                if addr_path.exists():
                    mac_str = addr_path.read_text().strip().replace(":", "").lower()
                    if len(mac_str) == 12:
                        _LOGGER.debug(f"Found MAC address from {dev_name}: {mac_str}")
                        _CACHED_MAC = mac_str
                        return _CACHED_MAC
            except Exception:
                continue

    # 2. Fallback to uuid.getnode()
    _LOGGER.warning("Could not find MAC from sysfs, falling back to uuid.getnode()")
    node = uuid.getnode()
    
    # Check if the multicast bit is set (indicates a random MAC)
    if (node >> 40) & 1:
        _LOGGER.warning("Generated MAC address is RANDOM/MULTICAST. Discovery identity will change on reboot.")

    mac_hex = f"{node:012x}"
    _CACHED_MAC = mac_hex
    return _CACHED_MAC


def format_mac(mac: str) -> str:
    """Format a hex MAC string with colons (e.g., aa:bb:cc...)."""
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()
