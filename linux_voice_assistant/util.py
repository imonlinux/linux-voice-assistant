"""Utility methods."""

import logging
import uuid
from collections.abc import Callable
from typing import Optional

_LOGGER = logging.getLogger(__name__)
_CACHED_MAC: Optional[str] = None


def get_mac_address() -> str:
    """
    Get the MAC address as a hex string (lowercase, no colons).
    Example: "b827eb123456"

    This is a thin wrapper around uuid.getnode(), cached so we only
    compute/log it once per process.
    """
    global _CACHED_MAC
    if _CACHED_MAC:
        return _CACHED_MAC

    node = uuid.getnode()
    mac_hex = f"{node:012x}"

    # If the multicast bit is set, this is probably not a real hardware MAC.
    if (node >> 40) & 1:
        _LOGGER.warning(
            "uuid.getnode() returned a MAC with the multicast bit set; "
            "discovery identity may change on reboot."
        )

    _LOGGER.debug("Using MAC address from uuid.getnode(): %s", mac_hex)
    _CACHED_MAC = mac_hex
    return _CACHED_MAC


def format_mac(mac: str) -> str:
    """Format a hex MAC string with colons (e.g., aa:bb:cc:dd:ee:ff)."""
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()
