"""mDNS/DNS-SD discovery of Sendspin servers.

Restored fork feature (pre-2.0 behavior): when
``sendspin.connection.server_host`` is not set and ``sendspin.connection.mdns``
is true (the default), the client browses the network for
``_sendspin-server._tcp.local.`` advertisements — the service type the
Sendspin spec and Music Assistant publish — and connects to the first server
found. Setting ``server_host`` bypasses discovery entirely.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

_LOGGER = logging.getLogger(__name__)

SENDSPIN_SERVER_SERVICE = "_sendspin-server._tcp.local."

# Default TXT ``path`` when the advertisement doesn't carry one.
DEFAULT_SERVER_PATH = "/sendspin"


@dataclass
class DiscoveredSendspinServer:
    """A Sendspin server advertisement seen on the network."""

    instance_name: str
    host: str
    port: int
    path: str = DEFAULT_SERVER_PATH
    properties: Dict[str, str] = field(default_factory=dict)


def _decode_properties(props: Optional[Dict[bytes, bytes]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not props:
        return out
    for key, value in props.items():
        try:
            key_s = key.decode("utf-8", errors="ignore")
        except Exception:  # pylint: disable=broad-except
            continue
        if not key_s:
            continue
        try:
            value_s = value.decode("utf-8", errors="ignore") if value else ""
        except Exception:  # pylint: disable=broad-except
            value_s = ""
        out[key_s] = value_s
    return out


def _preferred_address(addresses: List[str]) -> Optional[str]:
    """First IPv4 address if present (least surprise on dual-stack hosts)."""
    if not addresses:
        return None
    for address in addresses:
        if ":" not in address:
            return address
    return addresses[0]


async def discover_sendspin_servers(
    timeout_s: float = 2.5,
    service_type: str = SENDSPIN_SERVER_SERVICE,
) -> List[DiscoveredSendspinServer]:
    """Browse for Sendspin server advertisements for ``timeout_s`` seconds.

    Returns servers sorted by instance name (deterministic pick order when
    several MA servers are visible).
    """
    azc = AsyncZeroconf()
    found: Dict[str, DiscoveredSendspinServer] = {}
    lock = asyncio.Lock()

    async def _handle_service_change(
        zeroconf, stype: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
            return
        try:
            info = AsyncServiceInfo(stype, name)
            if not await info.async_request(zeroconf, timeout=1500):
                return
            props = _decode_properties(info.properties)
            path = props.get("path") or DEFAULT_SERVER_PATH
            host = _preferred_address(info.parsed_addresses())
            if host is None or not info.port:
                return
            server = DiscoveredSendspinServer(
                instance_name=name,
                host=host,
                port=int(info.port),
                path=path,
                properties=props,
            )
            async with lock:
                found[name] = server
            _LOGGER.debug("Sendspin discovery: found %s at %s:%s%s", name, host, server.port, path)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.debug("Sendspin discovery error for %s", name, exc_info=True)

    # zeroconf invokes handlers with keyword arguments; keep the names.
    def _on_state_change(
        zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        asyncio.create_task(
            _handle_service_change(zeroconf, service_type, name, state_change)
        )

    browser = AsyncServiceBrowser(azc.zeroconf, service_type, handlers=[_on_state_change])
    try:
        await asyncio.sleep(timeout_s)
    finally:
        try:
            browser.cancel()
        except Exception:  # pylint: disable=broad-except
            pass
        await azc.async_close()

    servers = list(found.values())
    servers.sort(key=lambda s: (s.instance_name.lower(), s.host, s.port, s.path))
    return servers
