"""Utility methods."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)
_CACHED_MAC: Optional[str] = None


def load_jsonc(path: str, encoding: str = "utf-8") -> Any:
    """
    Load a JSON file, stripping JSONC comments (// and /* */).

    This allows config files to contain comments for documentation
    while maintaining compatibility with json.load() for standard JSON.

    Args:
        path: Path to the JSON file
        encoding: File encoding (default: utf-8)

    Returns:
        Parsed JSON data as Python dict/list

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON is malformed after comment stripping
    """
    with open(path, "r", encoding=encoding) as f:
        content = f.read()

    # Strip // comments (not inside strings)
    lines = []
    in_string = False
    string_char = None

    for line in content.split('\n'):
        i = 0
        stripped_line = []
        while i < len(line):
            char = line[i]

            # Track if we're inside a string literal
            if char in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                if in_string and char == string_char:
                    in_string = False
                    string_char = None
                elif not in_string:
                    in_string = True
                    string_char = char

            # If not in string, check for comment start
            if not in_string:
                if char == '/' and i + 1 < len(line) and line[i+1] == '/':
                    # Line comment - skip rest of line
                    break
                elif char == '/' and i + 1 < len(line) and line[i+1] == '*':
                    # Block comment start - find end
                    end_idx = line.find('*/', i + 2)
                    if end_idx != -1:
                        i = end_idx + 2
                    else:
                        # Multi-line comment - skip rest of line, will handle in subsequent lines
                        break

            stripped_line.append(char)
            i += 1

        if stripped_line:
            lines.append(''.join(stripped_line).rstrip())

    return json.loads('\n'.join(lines))


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
    # Remove existing colons and other separators
    clean_mac = mac.replace(":", "").replace("-", "").replace(".", "")

    # Format with colons every 2 characters
    return ":".join(clean_mac[i : i + 2] for i in range(0, 12, 2))


def slugify_device_id(name: str) -> str:
    """Convert a display name to a consistent device_id."""
    return name.strip().lower().replace(" ", "_")


def call_all(*callables: Optional[Callable[[], None]]) -> None:
    for item in filter(None, callables):
        item()


def is_arm() -> bool:
    """Detect if running on ARM architecture (e.g., Raspberry Pi)."""
    try:
        import platform
        return platform.machine().startswith(('arm', 'aarch'))
    except Exception:
        # Fallback: try to read from /proc/cpuinfo
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return 'ARM' in f.read()
        except Exception:
            return False
