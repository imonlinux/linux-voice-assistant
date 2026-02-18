"""Runs mDNS zeroconf service for Home Assistant discovery."""

import logging
import socket
from typing import Optional

from .util import get_mac_address

_LOGGER = logging.getLogger(__name__)

try:
    from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
except ImportError:
    _LOGGER.fatal("pip install zeroconf")
    raise

MDNS_TARGET_IP = "224.0.0.251"


class HomeAssistantZeroconf:
    def __init__(
        self, port: int, name: Optional[str] = None, host: Optional[str] = None,
        mac_address: Optional[str] = None,
    ) -> None:
        self.port = port
        # Use the stable MAC address (persisted via preferences.json).
        # Fall back to hardware detection if not provided (shouldn't happen
        # in normal operation, but keeps the class usable standalone).
        self.mac_address = mac_address or get_mac_address()
        self.name = name or self.mac_address

        if not host:
            try:
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                test_sock.setblocking(False)
                test_sock.connect((MDNS_TARGET_IP, 1))
                host = test_sock.getsockname()[0]
                test_sock.close()
                _LOGGER.debug("Detected IP: %s", host)
            except Exception:
                _LOGGER.warning("Could not detect IP address, mDNS might fail.")
                host = "127.0.0.1"

        assert host
        self.host = host
        self._aiozc = AsyncZeroconf()

    async def register_server(self) -> None:
        service_info = AsyncServiceInfo(
            "_esphomelib._tcp.local.",
            f"{self.name}._esphomelib._tcp.local.",
            addresses=[socket.inet_aton(self.host)],
            port=self.port,
            properties={
                "version": "2025.9.0",
                "mac": self.mac_address,
                "board": "host",
                "platform": "HOST",
                "network": "ethernet", 
            },
            server=f"{self.name}.local.",
        )
        await self._aiozc.async_register_service(service_info)
        _LOGGER.debug("Zeroconf discovery enabled: %s", service_info)
