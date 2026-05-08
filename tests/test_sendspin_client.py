"""Tests for Sendspin Client integration and protocol handling."""

import pytest
import asyncio
import json
import time
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from linux_voice_assistant.sendspin.client import SendspinClient
from linux_voice_assistant.sendspin.models import (
    DiscoveredSendspinServer,
    SendspinSessionInfo,
    SendspinConnectionState,
    SendspinPlaybackState,
    SendspinInternalState
)
from linux_voice_assistant.event_bus import EventBus


class TestSendspinModels:
    """Test Sendspin data models."""

    def test_discovered_server_websocket_url(self):
        """Test DiscoveredSendspinServer WebSocket URL generation."""
        server = DiscoveredSendspinServer(
            instance_name="test_server",
            host="192.168.1.100",
            port=8927,
            path="/sendspin"
        )

        assert server.ws_url() == "ws://192.168.1.100:8927/sendspin"

    def test_discovered_server_with_custom_path(self):
        """Test DiscoveredSendspinServer with custom path."""
        server = DiscoveredSendspinServer(
            instance_name="test_server",
            host="192.168.1.100",
            port=8927,
            path="/custom/path"
        )

        assert server.ws_url() == "ws://192.168.1.100:8927/custom/path"

    def test_sendspin_session_info_defaults(self):
        """Test SendspinSessionInfo initializes with defaults."""
        session = SendspinSessionInfo()

        assert session.server_id is None
        assert session.server_name is None
        assert session.active_roles == []

    def test_sendspin_session_info_with_data(self):
        """Test SendspinSessionInfo with data."""
        session = SendspinSessionInfo(
            server_id="server_123",
            server_name="Test Server",
            active_roles=["player@v1", "controller@v1"]
        )

        assert session.server_id == "server_123"
        assert session.server_name == "Test Server"
        assert session.active_roles == ["player@v1", "controller@v1"]

    def test_sendspin_connection_state(self):
        """Test SendspinConnectionState model."""
        state = SendspinConnectionState(
            connected=True,
            endpoint="ws://192.168.1.100:8927/sendspin",
            server_id="server_123",
            server_name="Test Server"
        )

        assert state.connected == True
        assert state.endpoint == "ws://192.168.1.100:8927/sendspin"
        assert state.server_id == "server_123"

    def test_sendspin_internal_state_defaults(self):
        """Test SendspinInternalState initializes with defaults."""
        state = SendspinInternalState()

        assert state.connection.connected == False
        assert state.connection.endpoint is None
        assert state.playback.playback_state == "unknown"
        assert state.playback.stream.codec is None
        assert state.metadata == {}


class TestSendspinClientInitialization:
    """Test SendspinClient initialization and configuration."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def config(self):
        """Create Sendspin configuration."""
        return {
            "enabled": True,
            "connection": {
                "server_host": "192.168.1.100",
                "server_port": 8927,
                "server_path": "/sendspin",
                "hello_timeout_seconds": 8.0,
                "ping_interval_seconds": 0,
                "ping_timeout_seconds": 20.0,
                "time_sync_interval_seconds": 5.0,
                "time_sync_adaptive": True,
                "time_sync_min_interval_seconds": 0.5,
                "time_sync_max_interval_seconds": 10.0,
                "time_sync_burst_size": 8,
                "time_sync_burst_spacing_seconds": 0.05,
                "time_sync_burst_grace_seconds": 0.15,
                "mdns": False
            },
            "coordination": {
                "duck_during_voice": True,
                "duck_gain": 0.3
            },
            "roles": {
                "player": True,
                "metadata": True,
                "controller": True
            },
            "player": {
                "supported_codecs": ["pcm"],
                "sample_rate": 48000,
                "channels": 2,
                "bit_depth": 16,
                "buffer_capacity_bytes": 1048576,
                "supported_commands": ["volume", "mute"]
            },
            "client": {
                "name": "Test Client",
                "device_info": {
                    "manufacturer": "Test Manufacturer",
                    "model": "Test Model"
                }
            },
            "initial": {
                "volume": 80,
                "muted": False
            },
            "logging": {
                "debug_protocol": False,
                "debug_payloads": False
            }
        }

    def test_sendspin_client_initialization(self, event_loop, event_bus, config):
        """Test SendspinClient can be initialized."""
        client = SendspinClient(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            client_id="test_client_123",
            client_name="Test Client"
        )

        assert client._loop == event_loop
        assert client._event_bus == event_bus
        assert client._client_id == "test_client_123"
        assert client._client_name == "Test Client"
        assert client._enabled == True

    def test_sendspin_client_disabled(self, event_loop, event_bus):
        """Test SendspinClient when disabled."""
        config = {"enabled": False}
        client = SendspinClient(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            client_id="test_client_123",
            client_name="Test Client"
        )

        assert client._enabled == False

    def test_sendspin_client_volume_initialization(self, event_loop, event_bus, config):
        """Test SendspinClient initializes volume from config."""
        config["initial"]["volume"] = 60
        config["initial"]["muted"] = True

        client = SendspinClient(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            client_id="test_client_123",
            client_name="Test Client"
        )

        assert client._user_volume == 60
        assert client._muted == True

    def test_sendspin_client_ducking_configuration(self, event_loop, event_bus, config):
        """Test SendspinClient ducking configuration."""
        config["coordination"]["duck_during_voice"] = False

        client = SendspinClient(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            client_id="test_client_123",
            client_name="Test Client"
        )

        assert client._duck_during_voice == False
        assert client._duck_gain == 0.3

    def test_sendspin_client_time_sync_configuration(self, event_loop, event_bus, config):
        """Test SendspinClient time sync configuration."""
        config["connection"]["time_sync_burst_size"] = 5
        config["connection"]["time_sync_adaptive"] = False

        client = SendspinClient(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            client_id="test_client_123",
            client_name="Test Client"
        )

        assert client._time_sync_burst_size == 5
        assert client._time_sync_adaptive == False


class TestSendspinClientMessageWrapping:
    """Test Sendspin message wrapping and unwrapping."""

    def test_build_message(self):
        """Test message building with payload wrapping."""
        msg = SendspinClient._build_message("client/hello", {
            "client_id": "test_client",
            "version": 1
        })

        assert msg["type"] == "client/hello"
        assert msg["payload"]["client_id"] == "test_client"
        assert msg["payload"]["version"] == 1
        # Also check top-level duplication
        assert msg["client_id"] == "test_client"
        assert msg["version"] == 1

    def test_unwrap_message_with_payload(self):
        """Test message unwrapping with payload field."""
        msg = {
            "type": "server/hello",
            "payload": {
                "server_id": "server_123",
                "name": "Test Server"
            }
        }

        mtype, payload = SendspinClient._unwrap_message(msg)

        assert mtype == "server/hello"
        assert payload["server_id"] == "server_123"
        assert payload["name"] == "Test Server"

    def test_unwrap_message_without_payload(self):
        """Test message unwrapping without payload field."""
        msg = {
            "type": "server/hello",
            "server_id": "server_123",
            "name": "Test Server"
        }

        mtype, payload = SendspinClient._unwrap_message(msg)

        assert mtype == "server/hello"
        assert payload["server_id"] == "server_123"
        assert payload["name"] == "Test Server"

    def test_unwrap_message_mixed(self):
        """Test message unwrapping with mixed structure."""
        msg = {
            "type": "server/hello",
            "payload": {
                "server_id": "server_123"
            },
            "extra_field": "value"
        }

        mtype, payload = SendspinClient._unwrap_message(msg)

        assert mtype == "server/hello"
        assert payload["server_id"] == "server_123"
        # Note: extra_field is not included when payload is present
        # The implementation returns only the payload dict when it exists


class TestSendspinClientDisconnectLogic:
    """Test SendspinClient disconnect reason mapping and goodbye logic."""

    def test_disconnect_reason_mapping_shutdown(self):
        """Test disconnect reason mapping for shutdown."""
        reason = SendspinClient._map_disconnect_reason("shutdown")
        assert reason == "shutdown"

    def test_disconnect_reason_mapping_restart(self):
        """Test disconnect reason mapping for restart."""
        reason = SendspinClient._map_disconnect_reason("restart")
        assert reason == "restart"

    def test_disconnect_reason_mapping_user_request(self):
        """Test disconnect reason mapping for user request."""
        reason = SendspinClient._map_disconnect_reason("user_request")
        assert reason == "user_request"

    def test_disconnect_reason_mapping_another_server(self):
        """Test disconnect reason mapping for another server."""
        reason = SendspinClient._map_disconnect_reason("another_server")
        assert reason == "another_server"

    def test_disconnect_reason_mapping_aliases(self):
        """Test disconnect reason mapping for common aliases."""
        assert SendspinClient._map_disconnect_reason("stop") == "shutdown"
        assert SendspinClient._map_disconnect_reason("exit") == "shutdown"
        assert SendspinClient._map_disconnect_reason("quit") == "shutdown"
        assert SendspinClient._map_disconnect_reason("reboot") == "restart"
        assert SendspinClient._map_disconnect_reason("reconnect") == "restart"
        assert SendspinClient._map_disconnect_reason("user") == "user_request"
        assert SendspinClient._map_disconnect_reason("manual") == "user_request"
        assert SendspinClient._map_disconnect_reason("switch") == "another_server"

    def test_disconnect_reason_mapping_unknown(self):
        """Test disconnect reason mapping for unknown reasons defaults to shutdown."""
        reason = SendspinClient._map_disconnect_reason("unknown_reason")
        assert reason == "shutdown"


class TestSendspinClientVolumeAndDucking:
    """Test SendspinClient volume and ducking logic."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def config(self):
        """Create Sendspin configuration."""
        return {
            "enabled": True,
            "coordination": {
                "duck_during_voice": True,
                "duck_gain": 0.3
            },
            "initial": {
                "volume": 80,
                "muted": False
            },
            "connection": {},
            "roles": {},
            "player": {},
            "client": {},
            "logging": {}
        }

    @pytest.fixture
    def client(self, event_loop, event_bus, config):
        """Create SendspinClient for testing."""
        with patch('linux_voice_assistant.sendspin.client.SendspinPlayerPipeline'):
            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=config,
                client_id="test_client",
                client_name="Test Client"
            )
            return client

    def test_effective_volume_without_ducking(self, client):
        """Test effective volume without ducking."""
        client._user_volume = 80
        client._ducked = False

        assert client._effective_volume() == 80

    def test_effective_volume_with_ducking(self, client):
        """Test effective volume with ducking."""
        client._user_volume = 80
        client._ducked = True

        # 80 * 0.3 = 24
        assert client._effective_volume() == 24

    def test_effective_volume_clamping(self, client):
        """Test effective volume is clamped to valid range."""
        client._duck_gain = 0.0  # Extreme ducking
        client._user_volume = 80
        client._ducked = True

        # Should be clamped to at least 0
        assert client._effective_volume() >= 0

    def test_set_ducked(self, client):
        """Test setting ducked state."""
        client._ducked = False

        # Enable ducking
        client.set_ducked(True)
        assert client._ducked == True

        # Disable ducking
        client.set_ducked(False)
        assert client._ducked == False

    def test_set_ducked_ignores_when_disabled(self, client):
        """Test set_ducked is ignored when duck_during_voice is False."""
        client._duck_during_voice = False
        client._ducked = False

        client.set_ducked(True)

        # Should remain unchanged
        assert client._ducked == False


class TestSendspinClientStatePublishing:
    """Test SendspinClient state publishing to EventBus."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus with event tracking."""
        bus = EventBus()
        events_received = []

        def on_connection_state(data):
            events_received.append(("connection_state", data))

        def on_playback_state(data):
            events_received.append(("playback_state", data))

        def on_metadata(data):
            events_received.append(("metadata", data))

        def on_audio_state(data):
            events_received.append(("audio_state", data))

        bus.subscribe("sendspin_connection_state", on_connection_state)
        bus.subscribe("sendspin_playback_state", on_playback_state)
        bus.subscribe("sendspin_metadata", on_metadata)
        bus.subscribe("sendspin_audio_state", on_audio_state)

        bus.events_received = events_received
        return bus

    @pytest.fixture
    def config(self):
        """Create Sendspin configuration."""
        return {
            "enabled": True,
            "coordination": {
                "duck_during_voice": True,
                "duck_gain": 0.3
            },
            "initial": {
                "volume": 80,
                "muted": False
            },
            "connection": {},
            "roles": {},
            "player": {},
            "client": {},
            "logging": {}
        }

    @pytest.fixture
    def client(self, event_loop, event_bus, config):
        """Create SendspinClient for testing."""
        with patch('linux_voice_assistant.sendspin.client.SendspinPlayerPipeline'):
            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=config,
                client_id="test_client",
                client_name="Test Client"
            )
            return client

    def test_publish_connection_state(self, client, event_bus):
        """Test publishing connection state."""
        client._state.connection.connected = True
        client._state.connection.endpoint = "ws://192.168.1.100:8927/sendspin"
        client._state.connection.server_id = "server_123"
        client._state.connection.server_name = "Test Server"

        client._publish_connection_state()

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "connection_state"
        assert event_bus.events_received[0][1]["connected"] == True
        assert event_bus.events_received[0][1]["endpoint"] == "ws://192.168.1.100:8927/sendspin"
        assert event_bus.events_received[0][1]["server_id"] == "server_123"

    def test_publish_playback_state(self, client, event_bus):
        """Test publishing playback state."""
        client._state.playback.playback_state = "playing"
        client._state.playback.stream.codec = "pcm"
        client._state.playback.stream.sample_rate = 48000
        client._state.playback.stream.channels = 2
        client._state.playback.stream.bit_depth = 16

        client._publish_playback_state()

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "playback_state"
        assert event_bus.events_received[0][1]["playback_state"] == "playing"
        assert event_bus.events_received[0][1]["codec"] == "pcm"
        assert event_bus.events_received[0][1]["sample_rate"] == 48000

    def test_publish_metadata(self, client, event_bus):
        """Test publishing metadata."""
        client._state.metadata = {
            "title": "Test Song",
            "artist": "Test Artist",
            "album": "Test Album"
        }

        client._publish_metadata()

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "metadata"
        assert event_bus.events_received[0][1]["title"] == "Test Song"
        assert event_bus.events_received[0][1]["artist"] == "Test Artist"

    def test_publish_audio_state(self, client, event_bus):
        """Test publishing audio state."""
        client._user_volume = 75
        client._muted = False
        client._ducked = True

        client._publish_audio_state()

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "audio_state"
        assert event_bus.events_received[0][1]["volume"] == 75
        assert event_bus.events_received[0][1]["muted"] == False
        assert event_bus.events_received[0][1]["ducked"] == True
        assert event_bus.events_received[0][1]["effective_volume"] == 22  # 75 * 0.3


class TestSendspinClientHelloMessages:
    """Test SendspinClient hello message building."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def config(self):
        """Create Sendspin configuration."""
        return {
            "enabled": True,
            "connection": {},
            "coordination": {},
            "roles": {
                "player": True,
                "metadata": True,
                "controller": True
            },
            "player": {
                "supported_codecs": ["pcm", "flac"],
                "sample_rate": 48000,
                "channels": 2,
                "bit_depth": 16,
                "buffer_capacity_bytes": 1048576,
                "supported_commands": ["volume", "mute"]
            },
            "client": {
                "name": "Test Client",
                "device_info": {
                    "manufacturer": "Test Manufacturer",
                    "model": "Test Model"
                }
            },
            "initial": {},
            "logging": {}
        }

    @pytest.fixture
    def client(self, event_loop, event_bus, config):
        """Create SendspinClient for testing."""
        with patch('linux_voice_assistant.sendspin.client.SendspinPlayerPipeline'):
            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=config,
                client_id="test_client_123",
                client_name="Test Client"
            )
            return client

    def test_build_client_hello(self, client):
        """Test client hello message building."""
        hello = client._build_client_hello()

        assert hello["type"] == "client/hello"
        assert hello["payload"]["client_id"] == "test_client_123"
        assert hello["payload"]["name"] == "Test Client"
        assert hello["payload"]["version"] == 1
        assert "player@v1" in hello["payload"]["supported_roles"]
        assert "metadata@v1" in hello["payload"]["supported_roles"]
        assert "controller@v1" in hello["payload"]["supported_roles"]
        assert hello["payload"]["device_info"]["manufacturer"] == "Test Manufacturer"

    def test_build_player_support_v1(self, client):
        """Test player support v1 structure."""
        support = client._build_player_support_v1()

        assert "supported_formats" in support
        assert len(support["supported_formats"]) == 2  # pcm and flac

        # Check first format
        fmt1 = support["supported_formats"][0]
        assert fmt1["codec"] in ["pcm", "flac"]
        assert fmt1["channels"] == 2
        assert fmt1["sample_rate"] == 48000
        assert fmt1["bit_depth"] == 16

        assert support["buffer_capacity"] == 1048576
        assert "volume" in support["supported_commands"]
        assert "mute" in support["supported_commands"]

    def test_build_initial_client_state(self, client):
        """Test initial client state message."""
        client._user_volume = 70
        client._muted = True

        state = client._build_initial_client_state()

        assert state["type"] == "client/state"
        assert state["payload"]["state"] == "synchronized"
        assert "player" in state["payload"]
        assert state["payload"]["player"]["volume"] == 70
        assert state["payload"]["player"]["muted"] == True


class TestSendspinClientServerHelloHandling:
    """Test SendspinClient server hello handling."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus with event tracking."""
        bus = EventBus()
        events_received = []

        def on_connection_state(data):
            events_received.append(("connection_state", data))

        bus.subscribe("sendspin_connection_state", on_connection_state)
        bus.events_received = events_received
        return bus

    @pytest.fixture
    def config(self):
        """Create Sendspin configuration."""
        return {
            "enabled": True,
            "connection": {},
            "coordination": {},
            "roles": {},
            "player": {},
            "client": {},
            "initial": {},
            "logging": {}
        }

    @pytest.fixture
    def client(self, event_loop, event_bus, config):
        """Create SendspinClient for testing."""
        with patch('linux_voice_assistant.sendspin.client.SendspinPlayerPipeline'):
            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=config,
                client_id="test_client",
                client_name="Test Client"
            )
            return client

    def test_handle_server_hello(self, client, event_bus):
        """Test server hello handling."""
        endpoint = "ws://192.168.1.100:8927/sendspin"
        payload = {
            "server_id": "server_123",
            "name": "Test Server",
            "active_roles": ["player@v1", "controller@v1"]
        }

        client._handle_server_hello(endpoint, payload)

        # Check state updates
        assert client._state.connection.connected == True
        assert client._state.connection.endpoint == endpoint
        assert client._state.connection.server_id == "server_123"
        assert client._state.connection.server_name == "Test Server"
        assert "player@v1" in client._active_roles
        assert "controller@v1" in client._active_roles

        # Check event was published
        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "connection_state"
        assert event_bus.events_received[0][1]["connected"] == True

    def test_handle_server_hello_with_server_name_fallback(self, client, event_bus):
        """Test server hello with server_name fallback."""
        endpoint = "ws://192.168.1.100:8927/sendspin"
        payload = {
            "server_id": "server_123",
            "server_name": "Test Server Name",  # Use server_name instead of name
            "active_roles": []
        }

        client._handle_server_hello(endpoint, payload)

        assert client._state.connection.server_name == "Test Server Name"


class TestSendspinClientTimeSync:
    """Test SendspinClient time synchronization logic."""

    def test_compute_time_sync_interval_adaptive_disabled(self):
        """Test time sync interval when adaptive is disabled."""
        # We need a client instance to test this
        with patch('linux_voice_assistant.sendspin.client.SendspinPlayerPipeline'):
            event_loop = asyncio.new_event_loop()
            event_bus = EventBus()

            config = {
                "enabled": True,
                "connection": {
                    "time_sync_interval_seconds": 5.0,
                    "time_sync_adaptive": False
                },
                "coordination": {},
                "roles": {},
                "player": {},
                "client": {},
                "initial": {},
                "logging": {}
            }

            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=config,
                client_id="test_client",
                client_name="Test Client"
            )

            interval = client._compute_time_sync_interval_s()
            assert interval == 5.0

            event_loop.close()

    def test_time_sync_burst_mode_constraints(self):
        """Test time sync burst mode constraints."""
        with patch('linux_voice_assistant.sendspin.client.SendspinPlayerPipeline'):
            event_loop = asyncio.new_event_loop()
            event_bus = EventBus()

            config = {
                "enabled": True,
                "connection": {
                    "time_sync_burst_size": 0  # Invalid, should be clamped to 1
                },
                "coordination": {},
                "roles": {},
                "player": {},
                "client": {},
                "initial": {},
                "logging": {}
            }

            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=config,
                client_id="test_client",
                client_name="Test Client"
            )

            # Should be clamped to minimum 1
            assert client._time_sync_burst_size == 1

            event_loop.close()


class TestSendspinClientErrorHandling:
    """Test SendspinClient error handling."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def config(self):
        """Create Sendspin configuration."""
        return {
            "enabled": True,
            "connection": {},
            "coordination": {},
            "roles": {},
            "player": {},
            "client": {},
            "initial": {},
            "logging": {}
        }

    def test_websocket_closed_detection(self):
        """Test WebSocket closed detection utility."""
        # Import the utility function from the client module
        from linux_voice_assistant.sendspin.client import _ws_is_closed

        # Test None case
        assert _ws_is_closed(None) == True

        # Test mock WebSocket with closed attribute
        ws = MagicMock()
        ws.closed = True
        assert _ws_is_closed(ws) == True

        ws.closed = False
        assert _ws_is_closed(ws) == False

    def test_config_helpers_with_dict(self):
        """Test config helpers with dict input."""
        config = {
            "key1": "value1",
            "key2": "value2",
            "nested": {
                "subkey": "subvalue"
            }
        }

        assert SendspinClient._cfg_get(config, "key1") == "value1"
        assert SendspinClient._cfg_get(config, "missing", "default") == "default"
        assert SendspinClient._cfg_get_section(config, "nested")["subkey"] == "subvalue"

    def test_config_helpers_with_object(self):
        """Test config helpers with object input."""
        class ConfigObj:
            key1 = "value1"
            key2 = "value2"

        config = ConfigObj()

        assert SendspinClient._cfg_get(config, "key1") == "value1"
        assert SendspinClient._cfg_get(config, "missing", "default") == "default"

    def test_config_helpers_with_none(self):
        """Test config helpers with None input."""
        assert SendspinClient._cfg_get(None, "key", "default") == "default"
        assert SendspinClient._cfg_get_section(None, "key") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])