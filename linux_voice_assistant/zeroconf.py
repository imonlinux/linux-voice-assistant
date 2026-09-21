"""Runs mDNS zeroconf service for Home Assistant discovery."""

import logging
import socket
from typing import Optional

_LOGGER = logging.getLogger(__name__)

try:
    from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
except ImportError:
    _LOGGER.fatal("zeroconf not installed. Please install it with: pip install zeroconf")
    raise

MDNS_TARGET_IP = "224.0.0.251"


class HomeAssistantZeroconf:
    def __init__(
        self,
        port: int,
        mac_address: str,
        host_ip_address: str,
        name: Optional[str] = None,
        friendly_name: Optional[str] = None,
    ) -> None:
        self.port = port
        self.mac_address = mac_address
        self.name = name or self.mac_address
        self.friendly_name = friendly_name
        self.host_ip_address = host_ip_address

        self._aiozc = AsyncZeroconf()

    async def register_server(self) -> None:

        properties = {
            "version": "2025.9.0",
            "mac": self.mac_address,
            "board": "host",
            "platform": "HOST",
            "network": "ethernet",  # or "wifi"
        }
        # Fork: advertise the human-readable name so HA's discovery card
        # shows it instead of the raw device name (lva-<mac>). Home
        # Assistant's ESPHome integration prefers this TXT property when
        # presenting a discovered entry.
        if self.friendly_name:
            properties["friendly_name"] = self.friendly_name

        service_info = AsyncServiceInfo(
            "_esphomelib._tcp.local.",
            f"{self.name}._esphomelib._tcp.local.",
            addresses=[socket.inet_aton(self.host_ip_address)],
            port=self.port,
            properties=properties,
            server=f"{self.name}.local.",
        )
        await self._aiozc.async_register_service(service_info)
        _LOGGER.debug("Zeroconf discovery enabled: %s", service_info)
