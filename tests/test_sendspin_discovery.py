"""Tests for Sendspin mDNS/DNS-SD discovery (restored pre-2.0 feature)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from zeroconf import ServiceStateChange

from linux_voice_assistant.sendspin.discovery import (
    DEFAULT_SERVER_PATH,
    SENDSPIN_SERVER_SERVICE,
    DiscoveredSendspinServer,
    _decode_properties,
    _preferred_address,
    discover_sendspin_servers,
)


class TestSendspinDiscoveryConstants:
    def test_service_type_constant(self):
        """The service type matches the Sendspin spec / MA advertisement."""
        assert SENDSPIN_SERVER_SERVICE == "_sendspin-server._tcp.local."

    def test_default_path_constant(self):
        assert DEFAULT_SERVER_PATH == "/sendspin"


class TestSendspinPropertyDecoding:
    def test_decode_properties_valid(self):
        props = {b"path": b"/sendspin", b"version": b"1.0", b"name": b"Test Server"}
        decoded = _decode_properties(props)
        assert decoded["path"] == "/sendspin"
        assert decoded["version"] == "1.0"
        assert decoded["name"] == "Test Server"

    def test_decode_properties_none_and_empty(self):
        assert _decode_properties(None) == {}
        assert _decode_properties({}) == {}

    def test_decode_properties_invalid_utf8_key_skipped(self):
        decoded = _decode_properties({b"\xff\xfe": b"value", b"ok": b"yes"})
        assert decoded == {"ok": "yes"}

    def test_decode_properties_invalid_utf8_value_becomes_empty(self):
        decoded = _decode_properties({b"key": b"\xff\xfe"})
        assert decoded == {"key": ""}

    def test_decode_properties_none_value_becomes_empty(self):
        decoded = _decode_properties({b"key": None})
        assert decoded == {"key": ""}


class TestPreferredAddress:
    def test_prefers_ipv4_over_ipv6(self):
        assert _preferred_address(["fd00::5", "192.168.1.5"]) == "192.168.1.5"

    def test_falls_back_to_first_address(self):
        assert _preferred_address(["fd00::5"]) == "fd00::5"

    def test_none_when_no_addresses(self):
        assert _preferred_address([]) is None


class TestDiscoveredSendspinServer:
    def test_defaults(self):
        server = DiscoveredSendspinServer(
            instance_name="x._sendspin-server._tcp.local.", host="1.2.3.4", port=8927
        )
        assert server.path == "/sendspin"
        assert server.properties == {}


def _discovery_patches(
    request_ok=True,
    properties=None,
    addresses=None,
    port=8927,
    names=("MA-1._sendspin-server._tcp.local.", "MA-2._sendspin-server._tcp.local."),
    fire=True,
):
    """Patch zeroconf plumbing; the fake browser fires Added for ``names``."""

    mock_azc = MagicMock()
    mock_azc.async_close = AsyncMock()
    mock_azc.zeroconf = object()

    def fake_browser_cls(zc, stype, handlers=None, **kwargs):
        handler = handlers[0]

        async def fire_events():
            await asyncio.sleep(0)
            for name in names:
                handler(zc, stype, name, ServiceStateChange.Added)

        if fire:
            asyncio.get_running_loop().create_task(fire_events())
        return MagicMock()

    mock_info = MagicMock()
    mock_info.async_request = AsyncMock(return_value=request_ok)
    mock_info.properties = properties if properties is not None else {b"path": b"/sendspin"}
    mock_info.parsed_addresses.return_value = addresses or ["192.168.1.100"]
    mock_info.port = port

    ctx = (
        patch("linux_voice_assistant.sendspin.discovery.AsyncZeroconf", return_value=mock_azc),
        patch(
            "linux_voice_assistant.sendspin.discovery.AsyncServiceBrowser",
            side_effect=fake_browser_cls,
        ),
        patch("linux_voice_assistant.sendspin.discovery.AsyncServiceInfo", return_value=mock_info),
    )
    return ctx


class TestDiscoverSendspinServers:
    async def test_full_pipeline_builds_servers_from_advertisements(self):
        azc_p, browser_p, info_p = _discovery_patches(
            properties={b"path": b"/custom", b"version": b"2.0"},
            addresses=["fd00::5", "192.168.1.100"],
        )
        with azc_p, browser_p, info_p:
            servers = await discover_sendspin_servers(timeout_s=0.15)

        assert [s.instance_name for s in servers] == [
            "MA-1._sendspin-server._tcp.local.",
            "MA-2._sendspin-server._tcp.local.",
        ]
        server = servers[0]
        # IPv4 preferred over the v6 address, TXT path honored.
        assert server.host == "192.168.1.100"
        assert server.port == 8927
        assert server.path == "/custom"
        assert server.properties["version"] == "2.0"

    async def test_missing_txt_path_falls_back_to_default(self):
        azc_p, browser_p, info_p = _discovery_patches(properties={})
        with azc_p, browser_p, info_p:
            servers = await discover_sendspin_servers(timeout_s=0.15)
        assert servers and servers[0].path == "/sendspin"

    async def test_no_advertisements_returns_empty(self):
        # The fake browser never fires: the timeout path returns [].
        azc_p, browser_p, info_p = _discovery_patches(fire=False)
        with azc_p, browser_p, info_p:
            assert await discover_sendspin_servers(timeout_s=0.05) == []

    async def test_unresolvable_service_is_skipped(self):
        azc_p, browser_p, info_p = _discovery_patches(request_ok=False)
        with azc_p, browser_p, info_p:
            assert await discover_sendspin_servers(timeout_s=0.15) == []

    async def test_info_error_does_not_break_browse(self):
        azc_p, browser_p, info_p = _discovery_patches()
        with azc_p, browser_p, info_p as p:
            p.return_value.async_request = AsyncMock(side_effect=OSError("mdns timeout"))
            assert await discover_sendspin_servers(timeout_s=0.15) == []

    async def test_missing_addresses_or_port_are_skipped(self):
        azc_p, browser_p, info_p = _discovery_patches(addresses=[], port=0)
        with azc_p, browser_p, info_p:
            assert await discover_sendspin_servers(timeout_s=0.15) == []

    async def test_results_sorted_deterministically(self):
        azc_p, browser_p, info_p = _discovery_patches(
            names=("Zeta._sendspin-server._tcp.local.", "Alpha._sendspin-server._tcp.local.")
        )
        with azc_p, browser_p, info_p:
            servers = await discover_sendspin_servers(timeout_s=0.15)
        assert [s.instance_name.split(".")[0] for s in servers] == ["Alpha", "Zeta"]
