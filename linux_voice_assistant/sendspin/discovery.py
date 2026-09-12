from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from .models import DiscoveredSendspinServer

_LOGGER = logging.getLogger(__name__)

SENDSPIN_SERVER_SERVICE = "_sendspin-server._tcp.local."


def _decode_properties(props: Optional[Dict[bytes, bytes]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not props:
        return out
    for k, v in props.items():
        try:
            ks = k.decode("utf-8", errors="ignore")
        except Exception:
            continue
        try:
            vs = v.decode("utf-8", errors="ignore")
        except Exception:
            vs = ""
        out[ks] = vs
    return out


async def discover_sendspin_servers(
    timeout_s: float = 2.5,
    service_type: str = SENDSPIN_SERVER_SERVICE,
) -> List[DiscoveredSendspinServer]:
    """
    Discover Sendspin servers via mDNS/DNS-SD.

    Service type: `_sendspin-server._tcp.local.`
    TXT key: `path` (default `/sendspin`)
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
            ok = await info.async_request(zeroconf, timeout=1500)
            if not ok:
                return

            props = _decode_properties(info.properties)
            path = props.get("path", "/sendspin") or "/sendspin"

            addrs = info.parsed_addresses()
            if not addrs:
                return

            host = addrs[0]  # prefer first (often IPv4 first)
            port = int(info.port)

            server = DiscoveredSendspinServer(
                instance_name=name,
                host=host,
                port=port,
                path=path,
                properties=props,
            )

            async with lock:
                found[name] = server

        except Exception:
            _LOGGER.debug("Sendspin discovery error for %s", name, exc_info=True)

    # IMPORTANT: zeroconf calls handlers using keyword args.
    # So this handler must accept those parameter names.
    def _on_state_change(
        zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        asyncio.create_task(
            _handle_service_change(zeroconf, service_type, name, state_change)
        )

    browser = AsyncServiceBrowser(
        azc.zeroconf,
        service_type,
        handlers=[_on_state_change],
    )

    try:
        await asyncio.sleep(timeout_s)
    finally:
        try:
            browser.cancel()
        except Exception:
            pass
        await azc.async_close()

    servers = list(found.values())
    servers.sort(key=lambda s: (s.instance_name.lower(), s.host, s.port, s.path))
    return servers
