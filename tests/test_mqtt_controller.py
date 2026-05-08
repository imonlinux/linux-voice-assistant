"""Tests for MQTT Controller integration and Home Assistant communication."""

import pytest
import json
from unittest.mock import Mock, MagicMock, patch
from linux_voice_assistant.mqtt_controller import MqttController
from linux_voice_assistant.config import MqttConfig
from linux_voice_assistant.models import Preferences, SatelliteState
from linux_voice_assistant.event_bus import EventBus


class TestMqttControllerInitialization:
    """Test MqttController initialization and setup."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for MQTT tests."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus for MQTT controller."""
        return EventBus()

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username="test_user",
            password="test_pass"
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences for MQTT controller."""
        prefs = Preferences()
        prefs.num_leds = 12
        return prefs

    def test_mqtt_controller_initialization(self, event_loop, event_bus, mqtt_config, preferences):
        """Test MqttController can be initialized."""
        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        assert controller.loop == event_loop
        assert controller.preferences == preferences
        assert controller._host == "localhost"
        assert controller._port == 1883
        assert controller._username == "test_user"
        assert controller._password == "test_pass"
        assert controller._device_name == "test_device"
        assert controller._mac_address == "aa:bb:cc:dd:ee:ff"
        assert controller._connected == False

    def test_mqtt_controller_topic_generation(self, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT topics are generated correctly."""
        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        # Check topic prefix (slugify uses underscores, not hyphens)
        assert controller._topic_prefix == "lva/test_device"

        # Check mute topics
        assert "mute" in controller.topics
        assert controller.topics["mute"]["command"] == "lva/test_device/mute/set"
        assert controller.topics["mute"]["state"] == "lva/test_device/mute/state"

        # Check num_leds topics
        assert "num_leds" in controller.topics
        assert controller.topics["num_leds"]["command"] == "lva/test_device/num_leds/set"
        assert controller.topics["num_leds"]["state"] == "lva/test_device/num_leds/state"

        # Check state topics are generated
        for state_name in controller.CONFIGURABLE_STATES:
            assert state_name in controller.topics
            assert "effect_command" in controller.topics[state_name]
            assert "effect_state" in controller.topics[state_name]
            assert "light_command" in controller.topics[state_name]
            assert "light_state" in controller.topics[state_name]

    def test_mqtt_controller_configurable_states(self, event_loop, event_bus, mqtt_config, preferences):
        """Test that all SatelliteStates are configurable."""
        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        expected_states = [
            SatelliteState.IDLE.value,
            SatelliteState.LISTENING.value,
            SatelliteState.THINKING.value,
            SatelliteState.RESPONDING.value,
            SatelliteState.ERROR.value,
        ]

        assert controller.CONFIGURABLE_STATES == expected_states


class TestMqttControllerLifecycle:
    """Test MQTT Controller connection lifecycle."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username=None,
            password=None
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_controller_start(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT controller starts connection."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        controller.start()

        # Verify client setup
        mock_client_instance.will_set.assert_called_once()
        mock_client_instance.connect.assert_called_once_with("localhost", 1883, 60)
        mock_client_instance.loop_start.assert_called_once()

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_controller_stop(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT controller stops connection."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        controller._connected = True

        # Run stop asynchronously
        async def test_stop():
            await controller.stop()

        event_loop.run_until_complete(test_stop())

        # Verify cleanup
        assert controller._connected == False


class TestMqttControllerMessageHandling:
    """Test MQTT Controller message handling and routing."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        bus = EventBus()
        events_received = []

        # Track events
        def on_set_mic_mute(data):
            events_received.append(("set_mic_mute", data))

        def on_set_num_leds(data):
            events_received.append(("set_num_leds", data))

        def on_set_idle_effect(data):
            events_received.append(("set_idle_effect", data))

        bus.subscribe("set_mic_mute", on_set_mic_mute)
        bus.subscribe("set_num_leds", on_set_num_leds)
        bus.subscribe("set_idle_effect", on_set_idle_effect)

        bus.events_received = events_received
        return bus

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username=None,
            password=None
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    @pytest.fixture
    def controller(self, event_loop, event_bus, mqtt_config, preferences):
        """Create MQTT controller for testing."""
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client'):
            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                config=mqtt_config,
                app_name="test_device",
                mac_address="aa:bb:cc:dd:ee:ff",
                preferences=preferences
            )
            return controller

    def test_mqtt_handles_mute_command_on(self, controller, event_bus):
        """Test MQTT handles mute ON command."""
        topic = controller.topics["mute"]["command"]
        controller._handle_message_on_loop(topic, "ON", retained=False)

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "set_mic_mute"
        assert event_bus.events_received[0][1]["state"] == True

    def test_mqtt_handles_mute_command_off(self, controller, event_bus):
        """Test MQTT handles mute OFF command."""
        topic = controller.topics["mute"]["command"]
        controller._handle_message_on_loop(topic, "OFF", retained=False)

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "set_mic_mute"
        assert event_bus.events_received[0][1]["state"] == False

    def test_mqtt_handles_num_leds_command(self, controller, event_bus):
        """Test MQTT handles num_leds command."""
        topic = controller.topics["num_leds"]["command"]
        controller._handle_message_on_loop(topic, "20", retained=False)

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "set_num_leds"
        assert event_bus.events_received[0][1]["num_leds"] == 20

    def test_mqtt_handles_effect_command(self, controller, event_bus):
        """Test MQTT handles effect command."""
        topic = controller.topics["idle"]["effect_command"]
        controller._handle_message_on_loop(topic, "slow pulse", retained=False)

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "set_idle_effect"
        assert event_bus.events_received[0][1]["effect"] == "slow_pulse"

    def test_mqtt_handles_light_command_off(self, controller, event_bus):
        """Test MQTT handles light OFF command."""
        topic = controller.topics["idle"]["light_command"]
        payload = json.dumps({"state": "OFF"})
        controller._handle_message_on_loop(topic, payload, retained=False)

        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "set_idle_effect"
        assert event_bus.events_received[0][1]["effect"] == "off"

    def test_mqtt_handles_light_command_color(self, controller, event_bus):
        """Test MQTT handles light color command."""
        # Set up event tracking for this test
        events_received = []

        def on_set_idle_color(data):
            events_received.append(("set_idle_color", data))

        event_bus.subscribe("set_idle_color", on_set_idle_color)

        topic = controller.topics["idle"]["light_command"]
        payload = json.dumps({"color": [255, 0, 0], "brightness": 0.8})
        controller._handle_message_on_loop(topic, payload, retained=False)

        assert len(events_received) == 1
        assert events_received[0][0] == "set_idle_color"
        assert events_received[0][1]["color"] == [255, 0, 0]
        assert events_received[0][1]["brightness"] == 0.8

    def test_mqtt_ignores_state_topic_outside_bootstrap(self, controller, event_bus):
        """Test MQTT ignores state topics outside bootstrap."""
        topic = controller.topics["idle"]["effect_state"]
        controller._bootstrap_state_sync = False

        controller._handle_message_on_loop(topic, "solid", retained=False)

        # Should not publish event
        assert len(event_bus.events_received) == 0


class TestMqttControllerDiscovery:
    """Test MQTT Controller Home Assistant discovery."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username=None,
            password=None
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_publishes_discovery_configs(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT publishes Home Assistant discovery configs."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        controller._publish_discovery_configs()

        # Verify number_leds config published
        assert mock_client_instance.publish.call_count > 0

        # Check some publish calls
        publish_calls = [str(call) for call in mock_client_instance.publish.call_args_list]

        # Should have published configs for num_leds, effects, and lights for each state
        assert any("num_leds/config" in call for call in publish_calls)
        assert any("idle_effect/config" in call for call in publish_calls)
        assert any("idle_color/config" in call for call in publish_calls)

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_discovery_device_info(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT discovery includes proper device info."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        controller._publish_discovery_configs()

        # Get the first publish call that contains JSON
        for call in mock_client_instance.publish.call_args_list:
            args, kwargs = call
            if len(args) > 1:
                try:
                    payload = json.loads(args[1])
                    if "device" in payload:
                        device_info = payload["device"]
                        assert device_info["identifiers"] == ["aa:bb:cc:dd:ee:ff"]
                        assert device_info["connections"] == [["mac", "aa:bb:cc:dd:ee:ff"]]
                        assert device_info["name"] == "test_device"
                        assert device_info["manufacturer"] == "LVA Project"
                        break
                except (json.JSONDecodeError, TypeError):
                    continue


class TestMqttControllerStatePublishing:
    """Test MQTT Controller state publishing."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username=None,
            password=None
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_publishes_mute_state(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT publishes mute state."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        controller.publish_mute_state(True)

        # Verify publish was called
        mock_client_instance.publish.assert_called()

        # Check the publish arguments
        args, kwargs = mock_client_instance.publish.call_args
        topic = args[0]
        payload = args[1]

        assert topic == controller.topics["mute"]["state"]
        assert payload == "ON"
        assert kwargs.get("retain") == True

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_publishes_num_leds_state(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT publishes num_leds state."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        controller.publish_num_leds_state(20)

        # Verify publish was called
        args, kwargs = mock_client_instance.publish.call_args
        topic = args[0]
        payload = args[1]

        assert topic == controller.topics["num_leds"]["state"]
        assert payload == "20"
        assert kwargs.get("retain") == True

    @patch('linux_voice_assistant.mqtt_controller.mqtt.Client')
    def test_mqtt_publishes_led_state(self, mock_mqtt_client, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT publishes LED state to MQTT."""
        mock_client_instance = MagicMock()
        mock_mqtt_client.return_value = mock_client_instance

        controller = MqttController(
            loop=event_loop,
            event_bus=event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=preferences
        )

        # Simulate publish_state_to_mqtt event handler
        data = {
            "state_name": "idle",
            "effect": "slow_pulse",
            "color": [0, 0, 255],
            "brightness": 0.7
        }

        controller.publish_state_to_mqtt(data)

        # Verify state was published - should publish exactly 2 messages (effect + light)
        assert mock_client_instance.publish.call_count == 2

        # Check that publish was called with proper topics and payloads
        publish_calls = mock_client_instance.publish.call_args_list

        # Get first publish call (effect_state)
        first_call_args = publish_calls[0][0]
        assert "_effect/state" in first_call_args[0]  # Uses underscore format
        assert first_call_args[1] == "Slow Pulse"  # Title case formatting

        # Get second publish call (light_state)
        second_call_args = publish_calls[1][0]
        assert "_light/state" in second_call_args[0]  # Uses underscore format
        light_data = json.loads(second_call_args[1])
        assert light_data["state"] == "ON"
        assert light_data["color"] == {"r": 0, "g": 0, "b": 255}
        assert light_data["brightness"] == int(0.7 * 255)


class TestMqttControllerBootstrapLogic:
    """Test MQTT Controller bootstrap state sync logic."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username=None,
            password=None
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    @pytest.fixture
    def controller(self, event_loop, event_bus, mqtt_config, preferences):
        """Create MQTT controller for testing."""
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client'):
            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                config=mqtt_config,
                app_name="test_device",
                mac_address="aa:bb:cc:dd:ee:ff",
                preferences=preferences
            )
            return controller

    def test_bootstrap_state_initialization(self, controller):
        """Test bootstrap state is initialized correctly."""
        assert controller._bootstrap_state_sync == True
        assert controller._bootstrap_ends_at == None

    def test_bootstrap_activated_on_connect(self, controller):
        """Test bootstrap is activated on connection."""
        mock_client = MagicMock()
        controller._on_connect(mock_client, None, {}, 0)

        assert controller._bootstrap_state_sync == True
        assert controller._bootstrap_ends_at is not None
        assert controller._bootstrap_end_handle is not None

    def test_bootstrap_ends_after_timeout(self, controller):
        """Test bootstrap ends after timeout."""
        mock_client = MagicMock()

        # Simulate connection
        controller._on_connect(mock_client, None, {}, 0)
        assert controller._bootstrap_state_sync == True

        # Simulate bootstrap end
        controller._end_bootstrap_state_sync()

        assert controller._bootstrap_state_sync == False
        assert controller._bootstrap_end_handle == None

    def test_bootstrap_retained_message_handling(self, controller, event_bus):
        """Test that retained messages are handled during bootstrap."""
        # Set up event tracking for this test
        events_received = []

        def on_set_idle_effect(data):
            events_received.append(("set_idle_effect", data))

        event_bus.subscribe("set_idle_effect", on_set_idle_effect)

        # Set bootstrap mode
        controller._bootstrap_state_sync = True

        topic = controller.topics["idle"]["effect_state"]
        controller._handle_message_on_loop(topic, "solid", retained=True)

        # Should process retained message during bootstrap
        assert len(events_received) == 1
        assert events_received[0][0] == "set_idle_effect"

    def test_bootstrap_ignores_non_retained_after_bootstrap(self, controller, event_bus):
        """Test that non-retained messages are ignored after bootstrap."""
        # Set up event tracking for this test
        events_received = []

        def on_set_idle_effect(data):
            events_received.append(("set_idle_effect", data))

        event_bus.subscribe("set_idle_effect", on_set_idle_effect)

        # End bootstrap
        controller._bootstrap_state_sync = False

        topic = controller.topics["idle"]["effect_state"]
        controller._handle_message_on_loop(topic, "solid", retained=False)

        # Should ignore non-retained messages on state topics
        assert len(events_received) == 0


class TestMqttControllerErrorHandling:
    """Test MQTT Controller error handling."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mqtt_config(self):
        """Create MQTT configuration."""
        return MqttConfig(
            host="localhost",
            port=1883,
            username=None,
            password=None
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    @pytest.fixture
    def controller(self, event_loop, event_bus, mqtt_config, preferences):
        """Create MQTT controller for testing."""
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client'):
            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                config=mqtt_config,
                app_name="test_device",
                mac_address="aa:bb:cc:dd:ee:ff",
                preferences=preferences
            )
            return controller

    def test_mqtt_handles_invalid_num_leds(self, controller, event_bus):
        """Test MQTT handles invalid num_leds gracefully."""
        # Set up event tracking for this test
        events_received = []

        def on_set_num_leds(data):
            events_received.append(("set_num_leds", data))

        event_bus.subscribe("set_num_leds", on_set_num_leds)

        topic = controller.topics["num_leds"]["command"]
        controller._handle_message_on_loop(topic, "invalid", retained=False)

        # Should not publish event for invalid value
        assert len(events_received) == 0

    def test_mqtt_handles_invalid_json_light_command(self, controller, event_bus):
        """Test MQTT handles invalid JSON in light command."""
        # Set up event tracking for this test
        events_received = []

        def on_set_idle_color(data):
            events_received.append(("set_idle_color", data))

        def on_turn_on_idle(data):
            events_received.append(("turn_on_idle", data))

        event_bus.subscribe("set_idle_color", on_set_idle_color)
        event_bus.subscribe("turn_on_idle", on_turn_on_idle)

        topic = controller.topics["idle"]["light_command"]
        controller._handle_message_on_loop(topic, "not json", retained=False)

        # Should not crash, just ignore
        assert len(events_received) == 0

    def test_mqtt_handles_connection_failure(self, event_loop, event_bus, mqtt_config, preferences):
        """Test MQTT handles connection failure gracefully."""
        with patch('linux_voice_assistant.mqtt_controller.mqtt.Client') as mock_client:
            mock_client_instance = MagicMock()
            mock_client_instance.connect.side_effect = Exception("Connection failed")
            mock_client.return_value = mock_client_instance

            controller = MqttController(
                loop=event_loop,
                event_bus=event_bus,
                config=mqtt_config,
                app_name="test_device",
                mac_address="aa:bb:cc:dd:ee:ff",
                preferences=preferences
            )

            # Should not raise exception
            controller.start()

            # Connection should be failed
            assert controller._connected == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])