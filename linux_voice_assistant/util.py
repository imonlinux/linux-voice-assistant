"""Utility methods."""

import uuid
from collections.abc import Callable
from typing import Optional


def get_mac() -> str:
    """Get MAC address as a string."""
    return ":".join(
        f"{((uuid.getnode() >> i) & 0xFF):02x}" for i in range(0, 8 * 6, 8)
    )[::-1]


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()
