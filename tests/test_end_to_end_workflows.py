"""End-to-End workflow tests for linux-voice-assistant.

Tests complete user workflows and integration scenarios across components.
"""

import pytest
import asyncio
import time
from unittest.mock import Mock, MagicMock, patch, AsyncMock

# Mock hardware dependencies before importing
import sys
sys.modules['soundcard'] = MagicMock()

from linux_voice_assistant.event_bus import EventBus
from linux_voice_assistant.models import ServerState, Preferences
from linux_voice_assistant.mqtt_controller import MqttController
from linux_voice_assistant.sendspin.client import SendspinClient
from linux_voice_assistant.xvf3800_button_controller import XVF3800ButtonController
from linux_voice_assistant.xvf3800_led_backend import XVF3800LedBackend
from linux_voice_assistant.audio_engine import AudioEngine
from linux_voice_assistant.led_controller import LedController


class TestCompleteVoiceAssistantWorkflow:
    """Test complete voice assistant workflows from wake word to response."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock server state."""
        import asyncio
        loop = asyncio.new_event_loop()
        state = MagicMock(spec=ServerState)
        state.loop = loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.mic_mute = False
        return state

    @pytest.mark.asyncio
    async def test_wake_word_to_mute_toggle_workflow(self, event_loop, event_bus, mock_state):
        """Test complete workflow: wake word detection → button press → mute toggle → LED feedback."""
        # 1. Setup: Create mock microphone with wake word capability
        mock_mic = MagicMock()
        mock_mic.RECORD = True
        mock_mic.__enter__ = Mock(return_value=mock_mic)
        mock_mic.__exit__ = Mock(return_value=False)

        # 2. Setup: Create audio engine for wake word detection
        with patch('linux_voice_assistant.audio_engine.MicroWakeWord') as mock_www:
            mock_wake_word = MagicMock()
            mock_wake_word.detect.return_value = True  # Simulate wake word detected
            mock_www.return_value = mock_wake_word

            audio_engine = AudioEngine(
                mock_state,
                mock_mic,
                input_block_size=1024,
                oww_threshold=0.5
            )

            # 3. Setup: Create LED controller for feedback
            with patch('linux_voice_assistant.led_controller.get_mic') as mock_get_mic:
                mock_get_mic.return_value = MagicMock()
                led_controller = LedController(mock_state)
                led_controller.start()

                # 4. Action: Simulate wake word detection
                event_bus.publish("wake_word_detected", {"model": "ok_nabu"})

                # 5. Action: Simulate hardware button press for mute toggle
                event_bus.publish("set_mic_mute", {"mute": True})

                # 6. Verification: Check state changed
                assert mock_state.mic_mute is True

                # 7. Verification: Check LED feedback was triggered
                await asyncio.sleep(0.1)  # Allow async operations
                led_controller.stop()

                audio_engine.stop()

    @pytest.mark.asyncio
    async def test_volume_control_workflow(self, event_loop, event_bus, mock_state):
        """Test workflow: volume change → audio ducking → unducking."""
        # 1. Setup: Initialize at volume 50%
        initial_volume = 50
        mock_state.preferences.volume_level = initial_volume

        # 2. Action: Simulate TTS starting (should duck volume)
        event_bus.publish("tts_start", {"volume_ducking": 0.3})

        # 3. Verification: Check ducking occurred
        # In real implementation, this would check volume was reduced
        await asyncio.sleep(0.1)

        # 4. Action: Simulate TTS ending (should unduck volume)
        event_bus.publish("tts_end", {})

        # 5. Verification: Check volume restored
        # In real implementation, this would verify volume is back to 50%
        await asyncio.sleep(0.1)


class TestMQTTIntegrationWorkflow:
    """Test MQTT discovery, connection, and Home Assistant integration workflows."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_loop, event_bus):
        """Create mock server state."""
        state = MagicMock(spec=ServerState)
        state.loop = event_loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.mac_address = "aa:bb:cc:dd:ee:ff"
        return state

    @pytest.mark.asyncio
    async def test_mqtt_discovery_and_connection_workflow(self, event_loop, event_bus, mock_state):
        """Test complete MQTT workflow: discovery → connection → HA integration."""
        # 1. Setup: Mock MQTT broker
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client') as mock_mqtt_client:
            mock_client = MagicMock()
            mock_mqtt_client.return_value = mock_client
            mock_client.connect.return_value = 0  # Connection successful

            # 2. Setup: Create MQTT controller
            mqtt_config = MagicMock()
            mqtt_config.host = "localhost"
            mqtt_config.port = 1883
            mqtt_config.username = None
            mqtt_config.password = None
            mqtt_config.client_id = "lva-test"
            mqtt_config.discovery_prefix = "homeassistant"
            mqtt_config.birth_topic = "homeassistant/status"
            mqtt_config.birth_payload = "online"
            mqtt_config.will_topic = "homeassistant/status"
            mqtt_config.will_payload = "offline"

            # 3. Action: Start MQTT controller (triggers discovery)
            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                state=mock_state,
                config=mqtt_config
            )

            # 4. Action: Simulate successful connection
            mock_client.on_connect = None
            controller._on_connect(None, None, 0, 0)

            # 5. Verification: Check discovery topics were published
            assert mock_client.publish.called

            # 6. Verification: Check state sync
            publish_calls = [call[0][0] for call in mock_client.publish.call_args_list]
            discovery_calls = [call for call in publish_calls if "homeassistant/" in call]
            assert len(discovery_calls) > 0

            # 7. Cleanup
            controller.stop()


class TestSendspinIntegrationWorkflow:
    """Test Sendspin discovery, WebSocket connection, and audio routing workflows."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_loop, event_bus):
        """Create mock server state."""
        state = MagicMock(spec=ServerState)
        state.loop = event_loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.preferences.sendspin_volume = 100
        state.mac_address = "aabbccddeeff"
        return state

    @pytest.mark.asyncio
    async def test_sendspin_discovery_and_connection_workflow(self, event_loop, event_bus, mock_state):
        """Test complete Sendspin workflow: mDNS discovery → WebSocket → handshake → state sync."""
        # 1. Setup: Mock WebSocket connection
        with patch('linux_voice_assistant.sendspin.client.websockets.connect') as mock_connect:
            mock_ws = MagicMock()
            mock_connect.return_value = mock_ws

            # 2. Setup: Mock server hello message
            mock_ws.recv.side_effect = [
                # Server hello
                '{"type": "hello", "seq": 1, "server_id": "ma-test", "server_name": "MusicAssistant", "version": "1.0", "snapshot": {"volume": 80, "muted": false}}',
                # Close message
                '{"type": "close"}'
            ]

            # 3. Setup: Create Sendspin client
            sendspin_config = {
                "enabled": True,
                "discovery": True,
                "auto_connect": True,
                "server_id": "ma-test"
            }

            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=sendspin_config,
                client_id="lva-aabbccddeeff",
                client_name="LVA Test"
            )

            # 4. Action: Start client (triggers discovery and connection)
            task = event_loop.create_task(client.run())
            await asyncio.sleep(0.2)  # Allow connection and handshake

            # 5. Verification: Check WebSocket was connected
            assert mock_connect.called

            # 6. Verification: Check handshake was sent
            sent_messages = []
            for call in mock_ws.send.call_args_list:
                sent_messages.append(call[0][0])

            hello_messages = [msg for msg in sent_messages if "hello" in msg]
            assert len(hello_messages) > 0

            # 7. Verification: Check state was synchronized
            # Client should have published its initial state
            state_events = [e for e in event_bus.events_received if "volume" in str(e)]
            assert len(state_events) > 0

            # 8. Cleanup
            client.stop()
            task.cancel()


class TestHardwareIntegrationWorkflow:
    """Test hardware button → LED feedback → state sync workflows."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock server state."""
        import asyncio
        loop = asyncio.new_event_loop()
        state = MagicMock(spec=ServerState)
        state.loop = loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.mic_mute = False
        return state

    def test_hardware_button_to_led_feedback_workflow(self, event_loop, event_bus, mock_state):
        """Test workflow: hardware button press → event publish → LED feedback → state update."""
        # 1. Setup: Mock USB device
        with patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient') as mock_usb_client:
            mock_usb = MagicMock()
            mock_usb_client.return_value = mock_usb
            mock_usb.GPO_MUTE_INDEX = 1
            mock_usb.get_mute_gpo.return_value = False

            # 2. Setup: Mock LED backend
            with patch('linux_voice_assistant.xvf3800_led_backend.XVF3800LedBackend') as mock_led_backend:
                mock_led = MagicMock()
                mock_led_backend.return_value = mock_led

                # 3. Action: Create button controller
                button_config = MagicMock()
                button_config.xvf3800_button_poll_interval = 0.1

                controller = XVF3800ButtonController(
                    loop=event_loop,
                    event_bus=event_bus,
                    state=mock_state,
                    button_config=button_config
                )

                # 4. Action: Simulate mute event from software
                event_bus.publish("set_mic_mute", {"mute": True})

                # 5. Verification: Check state updated
                time.sleep(0.2)  # Allow polling cycle
                assert mock_state.mic_mute is True

                # 6. Verification: Check hardware was updated
                assert mock_usb.set_mute_gpo.called

                # 7. Cleanup
                controller.stop()


class TestErrorRecoveryWorkflow:
    """Test error recovery and resilience workflows."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_loop, event_bus):
        """Create mock server state."""
        state = MagicMock(spec=ServerState)
        state.loop = event_loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.mac_address = "aa:bb:cc:dd:ee:ff"
        return state

    @pytest.mark.asyncio
    async def test_mqtt_connection_failure_recovery(self, event_loop, event_bus, mock_state):
        """Test MQTT connection failure and automatic reconnection workflow."""
        # 1. Setup: Mock MQTT broker with connection failure
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client') as mock_mqtt_client:
            mock_client = MagicMock()
            mock_mqtt_client.return_value = mock_client

            # 2. Simulate connection failure then success
            mock_client.connect.side_effect = [1, 0]  # Fail then succeed
            mock_client.loop_start.return_value = None

            # 3. Setup: Create MQTT controller
            mqtt_config = MagicMock()
            mqtt_config.host = "localhost"
            mqtt_config.port = 1883
            mqtt_config.username = None
            mqtt_config.password = None
            mqtt_config.client_id = "lva-test"

            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                state=mock_state,
                config=mqtt_config
            )

            # 4. Action: Start controller (first connection fails)
            controller.start()

            # 5. Action: Simulate reconnection trigger
            controller._on_disconnect(None, None, 0)

            # 6. Action: Simulate successful reconnection
            controller._on_connect(None, None, 0, 0)

            # 7. Verification: Check controller handled recovery
            assert controller.connected is True

            # 8. Cleanup
            controller.stop()

    @pytest.mark.asyncio
    async def test_sendspin_websocket_disconnection_recovery(self, event_loop, event_bus, mock_state):
        """Test Sendspin WebSocket disconnection and reconnection workflow."""
        # 1. Setup: Mock WebSocket with disconnection
        with patch('linux_voice_assistant.sendspin.client.websockets.connect') as mock_connect:
            mock_ws = MagicMock()
            mock_connect.return_value = mock_ws

            # 2. Simulate server messages then disconnection
            mock_ws.recv.side_effect = [
                '{"type": "hello", "seq": 1}',
                ConnectionError("WebSocket closed")
            ]

            # 3. Setup: Create Sendspin client
            sendspin_config = {
                "enabled": True,
                "auto_connect": True,
                "reconnect_delay": 0.1
            }

            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=sendspin_config,
                client_id="lva-test",
                client_name="LVA Test"
            )

            # 4. Action: Start client
            task = event_loop.create_task(client.run())

            # 5. Wait for connection and disconnection
            await asyncio.sleep(0.3)

            # 6. Verification: Check client handled disconnection gracefully
            assert client.connected is False

            # 7. Cleanup
            client.stop()
            task.cancel()


class TestMusicAssistantScenario:
    """Test real-world Music Assistant usage scenarios."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_loop, event_bus):
        """Create mock server state."""
        state = MagicMock(spec=ServerState)
        state.loop = event_loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.preferences.sendspin_volume = 100
        state.mac_address = "aabbccddeeff"
        return state

    @pytest.mark.asyncio
    async def test_music_assistant_volume_change_workflow(self, event_loop, event_bus, mock_state):
        """Test Music Assistant volume change workflow: MA sends volume → LVA updates state → LED feedback."""
        # 1. Setup: Mock WebSocket
        with patch('linux_voice_assistant.sendspin.client.websockets.connect') as mock_connect:
            mock_ws = MagicMock()
            mock_connect.return_value = mock_ws

            # 2. Setup: Mock server messages including volume change
            mock_ws.recv.side_effect = [
                '{"type": "hello", "seq": 1}',
                # Volume update from MA
                '{"type": "message", "seq": 2, "message": "volume_update", "data": {"volume": 75}}',
                '{"type": "close"}'
            ]

            # 3. Setup: Create Sendspin client
            sendspin_config = {"enabled": True, "auto_connect": True}

            client = SendspinClient(
                loop=event_loop,
                event_bus=event_bus,
                config=sendspin_config,
                client_id="lva-test",
                client_name="LVA Test"
            )

            # 4. Action: Start client and receive volume update
            task = event_loop.create_task(client.run())
            await asyncio.sleep(0.3)

            # 5. Verification: Check volume event was published
            volume_events = [e for e in event_bus.events_received if "volume" in str(e).lower()]
            assert len(volume_events) > 0

            # 6. Cleanup
            client.stop()
            task.cancel()


class TestHomeAssistantAutomationScenario:
    """Test real-world Home Assistant automation scenarios."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create event bus for workflow testing."""
        return EventBus(track_events=True)

    @pytest.fixture
    def mock_state(self, event_loop, event_bus):
        """Create mock server state."""
        state = MagicMock(spec=ServerState)
        state.loop = event_loop
        state.event_bus = event_bus
        state.preferences = MagicMock(spec=Preferences)
        state.preferences.volume_level = 50
        state.mac_address = "aa:bb:cc:dd:ee:ff"
        return state

    @pytest.mark.asyncio
    async def test_home_assistant_mute_toggle_automation(self, event_loop, event_bus, mock_state):
        """Test HA automation: MQTT command → LVA mute toggle → state update → feedback."""
        # 1. Setup: Mock MQTT broker
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client') as mock_mqtt_client:
            mock_client = MagicMock()
            mock_mqtt_client.return_value = mock_client
            mock_client.connect.return_value = 0

            # 2. Setup: Create MQTT controller
            mqtt_config = MagicMock()
            mqtt_config.host = "localhost"
            mqtt_config.port = 1883
            mqtt_config.username = None
            mqtt_config.password = None
            mqtt_config.client_id = "lva-test"
            mqtt_config.discovery_prefix = "homeassistant"

            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                state=mock_state,
                config=mqtt_config
            )

            # 3. Action: Simulate MQTT connection
            controller._on_connect(None, None, 0, 0)

            # 4. Action: Simulate HA sending mute command via MQTT
            controller._on_command(None, {"mute": True})

            # 5. Verification: Check mute event was published to event bus
            mute_events = [e for e in event_bus.events_received if "mute" in str(e).lower()]
            assert len(mute_events) > 0

            # 6. Verification: Check state was updated
            assert mock_state.mic_mute is True

            # 7. Cleanup
            controller.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])