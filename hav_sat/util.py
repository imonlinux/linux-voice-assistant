"""Utility methods."""

import platform
import uuid
from collections.abc import Callable
from typing import Optional


def get_mac() -> str:
    mac = uuid.getnode()
    mac_str = ":".join(f"{(mac >> i) & 0xff:02x}" for i in range(40, -1, -8))
    return mac_str


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def is_arm() -> bool:
    machine = platform.machine()
    return ("arm" in machine) or ("aarch" in machine)
