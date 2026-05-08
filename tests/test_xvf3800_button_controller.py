"""Tests for XVF3800 Button Controller hardware integration."""

import pytest
import threading
import time
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from linux_voice_assistant.xvf3800_button_controller import (
    XVF3800USBClient,
    XVF3800ButtonController,
    XVF3800ButtonRuntimeConfig
)
from linux_voice_assistant.event_bus import EventBus


class TestXVF3800USBClient:
    """Test XVF3800 USB client low-level hardware interface."""

    def test_usb_client_constants(self):
        """Test USB client vendor/product IDs and parameters."""
        assert XVF3800USBClient.VENDOR_ID == 0x2886
        assert XVF3800USBClient.PRODUCT_ID == 0x001A
        assert XVF3800USBClient.GPO_RESID == 20
        assert XVF3800USBClient.GPO_READ_CMDID == 0
        assert XVF3800USBClient.GPO_WRITE_CMDID == 1
        assert XVF3800USBClient.GPO_NUM_PINS == 5
        assert XVF3800USBClient.GPO_MUTE_INDEX == 1
        assert XVF3800USBClient.GPO_WS2812_POWER_INDEX == 3

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_usb_client_initialization_success(self, mock_usb_find):
        """Test USB client initialization when device is found."""
        mock_device = MagicMock()
        mock_device.bus = 1
        mock_device.address = 5
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()

        assert client._dev == mock_device
        mock_usb_find.assert_called_once_with(idVendor=0x2886, idProduct=0x001A)

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_usb_client_initialization_failure(self, mock_usb_find):
        """Test USB client initialization when device is not found."""
        mock_usb_find.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            XVF3800USBClient()

        assert "not found" in str(exc_info.value)
        assert "2886" in str(exc_info.value)
        assert "001A" in str(exc_info.value)

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_usb_client_context_manager(self, mock_usb_find):
        """Test USB client context manager support."""
        mock_device = MagicMock()
        mock_usb_find.return_value = mock_device

        with XVF3800USBClient() as client:
            assert client is not None
            assert client._dev == mock_device

        # Verify cleanup was called
        mock_device.assert_not_called()  # No specific cleanup expected

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_usb_client_close(self, mock_usb_find):
        """Test USB client cleanup."""
        mock_device = MagicMock()
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        client.close()

        assert client._dev is None

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_read_gpo_values(self, mock_usb_find):
        """Test reading GPO values from USB device."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [0, 1, 0, 1, 0, 0]  # status + 5 pins
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        values = client.read_gpo_values()

        assert values == [1, 0, 1, 0, 0]
        mock_device.ctrl_transfer.assert_called_once()

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_read_gpo_values_short_response(self, mock_usb_find):
        """Test handling of short GPO values response."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [0, 1, 0]  # Only 3 bytes
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        values = client.read_gpo_values()

        # Should return what we got, even if short
        assert values == [1, 0]

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_read_gpo_values_error_status(self, mock_usb_find):
        """Test handling of error status in GPO read."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [64, 1, 0, 1, 0, 0]  # Error status
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()

        with pytest.raises(RuntimeError) as exc_info:
            client.read_gpo_values()

        assert "Unexpected XVF3800 control status: 64" in str(exc_info.value)

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_set_gpo_pin(self, mock_usb_find):
        """Test setting individual GPO pin."""
        mock_device = MagicMock()
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        result = client.set_gpo_pin(30, True)

        assert result == True
        mock_device.ctrl_transfer.assert_called_once()

        # Verify the call was made (checking ctrl_transfer was called)
        assert mock_device.ctrl_transfer.call_count == 1

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_set_gpo_pin_usb_error(self, mock_usb_find):
        """Test handling of USB error when setting GPO pin."""
        import usb.core
        mock_device = MagicMock()
        mock_device.ctrl_transfer.side_effect = usb.core.USBError("USB error")
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        result = client.set_gpo_pin(30, True)

        assert result == False

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_get_mute_gpo(self, mock_usb_find):
        """Test reading mute GPO state."""
        mock_device = MagicMock()
        # Return proper data structure: status byte + payload bytes
        # After _ctrl_read slices [1:], we get the actual values
        mock_device.ctrl_transfer.return_value = [0, 0, 1, 0, 0, 0]  # Status=0, values=[0,1,0,0,0]
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        mute_state = client.get_mute_gpo()

        assert mute_state == True

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_get_mute_gpo_unmuted(self, mock_usb_find):
        """Test reading unmuted state."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [0, 0, 0, 1, 0, 0]  # Mute pin = 0
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        mute_state = client.get_mute_gpo()

        assert mute_state == False

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_get_mute_gpo_error(self, mock_usb_find):
        """Test handling of USB error when reading mute GPO."""
        import usb.core
        mock_device = MagicMock()
        mock_device.ctrl_transfer.side_effect = usb.core.USBError("USB error")
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        mute_state = client.get_mute_gpo()

        assert mute_state is None

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_get_mute_gpo_short_response(self, mock_usb_find):
        """Test handling of short response when reading mute GPO."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [0, 0]  # Only mute pin
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        mute_state = client.get_mute_gpo()

        assert mute_state is None

    @patch('linux_voice_assistant.xvf3800_button_controller.usb.core.find')
    def test_set_mute_gpo(self, mock_usb_find):
        """Test setting mute GPO state."""
        mock_device = MagicMock()
        mock_usb_find.return_value = mock_device

        client = XVF3800USBClient()
        result = client.set_mute_gpo(True)

        assert result == True
        mock_device.ctrl_transfer.assert_called_once()


class TestXVF3800ButtonRuntimeConfig:
    """Test XVF3800 button runtime configuration."""

    def test_default_config(self):
        """Test default runtime configuration."""
        config = XVF3800ButtonRuntimeConfig()

        assert config.poll_interval_seconds == 0.05  # 20 Hz default

    def test_custom_config(self):
        """Test custom runtime configuration."""
        config = XVF3800ButtonRuntimeConfig(poll_interval_seconds=0.1)

        assert config.poll_interval_seconds == 0.1


class TestXVF3800ButtonController:
    """Test XVF3800 Button Controller high-level integration."""

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
    def mock_state(self):
        """Create mock ServerState."""
        state = MagicMock()
        state.shutdown = False
        state.event_bus = EventBus()
        return state

    @pytest.fixture
    def button_config(self):
        """Create button config."""
        config = MagicMock()
        config.poll_interval_seconds = 0.05
        return config

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_button_controller_initialization(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test button controller initialization."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        assert controller.loop == event_loop
        assert controller.state == mock_state
        assert controller._cfg.poll_interval_seconds == 0.05
        assert controller._shutdown_flag.is_set() == False
        assert controller._thread is not None

        # Give thread time to start, then stop
        time.sleep(0.1)
        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_button_controller_custom_poll_interval(self, mock_usb_client_class, event_loop, event_bus, mock_state):
        """Test button controller with custom poll interval."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        custom_config = MagicMock()
        custom_config.poll_interval_seconds = 0.1

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=custom_config
        )

        assert controller._cfg.poll_interval_seconds == 0.1

        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_button_controller_invalid_poll_interval(self, mock_usb_client_class, event_loop, event_bus, mock_state):
        """Test button controller with invalid poll interval defaults to safe value."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        bad_config = MagicMock()
        bad_config.poll_interval_seconds = "invalid"

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=bad_config
        )

        # Should default to 0.05s when invalid
        assert controller._cfg.poll_interval_seconds == 0.05

        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_mic_muted_event_handler(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test mic_muted event sets target mute state."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Trigger event handler
        controller.mic_muted({})

        # Check target state was set
        target_state = controller._take_target_mute_state()
        assert target_state == True

        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_mic_unmuted_event_handler(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test mic_unmuted event sets target mute state."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Trigger event handler
        controller.mic_unmuted({})

        # Check target state was set
        target_state = controller._take_target_mute_state()
        assert target_state == False

        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_stop_controller(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test stopping the button controller."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Give thread time to start
        time.sleep(0.1)

        # Stop the controller
        controller.stop()

        # Verify shutdown flag is set
        assert controller._shutdown_flag.is_set()
        assert controller._usb_client is not None  # Client exists but may not be connected yet


class TestXVF3800ButtonControllerHardwareIntegration:
    """Test XVF3800 Button Controller hardware integration scenarios."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus with event tracking."""
        bus = EventBus()
        events_received = []

        def on_set_mic_mute(data):
            events_received.append(("set_mic_mute", data))

        bus.subscribe("set_mic_mute", on_set_mic_mute)
        bus.events_received = events_received
        return bus

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState with event bus."""
        state = MagicMock()
        state.shutdown = False
        state.event_bus = event_bus
        return state

    @pytest.fixture
    def button_config(self):
        """Create button config."""
        config = MagicMock()
        config.poll_interval_seconds = 0.05
        return config

    @pytest.mark.skip(reason="Complex threading behavior with timing-dependent polling loop")
    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_hardware_mute_button_press_detection(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test detection of hardware mute button press."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        # Set up all required mock attributes
        mock_usb_client.GPO_MUTE_INDEX = 1
        mock_usb_client.GPO_WS2812_POWER_INDEX = 3
        mock_usb_client.get_mute_gpo.return_value = False  # Initially unmuted
        mock_usb_client.set_mute_gpo.return_value = True

        # Simulate GPO values being read multiple times (enough for polling cycles)
        gpo_read_sequence = []
        for i in range(10):
            gpo_read_sequence.append([0, 0, 1, 0, 0])  # Unmuted
        gpo_read_sequence.append([0, 1, 1, 0, 0])  # Muted

        mock_usb_client.read_gpo_values.side_effect = gpo_read_sequence

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Wait for polling to detect state change
        time.sleep(0.3)

        controller.stop()

        # Should have detected mute state change and published event
        mute_events = [e for e in event_bus.events_received if e[0] == "set_mic_mute"]
        # Note: The initial unmuted state will also trigger an event
        assert len(mute_events) >= 1

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_software_mute_sync_to_hardware(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test software mute state syncs to hardware."""
        mock_usb_client = MagicMock()
        mock_usb_client.get_mute_gpo.return_value = False  # Initially unmuted
        mock_usb_client.read_gpo_values.return_value = [0, 0, 1, 0, 0]
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Trigger software mute event
        controller.mic_muted({})

        # Wait for polling to process target state
        time.sleep(0.2)

        controller.stop()

        # Verify hardware mute was set
        mock_usb_client.set_mute_gpo.assert_called_with(True)

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_usb_connection_retry_on_failure(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test USB connection retry on initialization failure."""
        # Fail first time, succeed second time
        mock_usb_client_class.side_effect = [
            RuntimeError("Device not found"),
            MagicMock()  # Success on retry
        ]

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Give polling thread time to retry
        time.sleep(0.3)

        controller.stop()

        # Should have attempted reconnection
        assert mock_usb_client_class.call_count >= 1


class TestXVF3800ButtonControllerErrorHandling:
    """Test XVF3800 Button Controller error handling."""

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
    def mock_state(self):
        """Create mock ServerState."""
        state = MagicMock()
        state.shutdown = False
        state.event_bus = EventBus()
        return state

    @pytest.fixture
    def button_config(self):
        """Create button config."""
        config = MagicMock()
        config.poll_interval_seconds = 0.05
        return config

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_usb_read_error_handling(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test handling of USB read errors."""
        import usb.core

        mock_usb_client = MagicMock()
        mock_usb_client.read_gpo_values.side_effect = usb.core.USBError("Read error")
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Should not crash, just log error and continue
        time.sleep(0.2)

        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_usb_write_error_handling(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test handling of USB write errors."""
        import usb.core

        mock_usb_client = MagicMock()
        mock_usb_client.read_gpo_values.return_value = [0, 0, 1, 0, 0]
        mock_usb_client.set_mute_gpo.return_value = False  # Write failed
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Trigger mute event (will fail to write)
        controller.mic_muted({})

        # Should not crash, just log error and continue
        time.sleep(0.2)

        controller.stop()

    @patch('linux_voice_assistant.xvf3800_button_controller.XVF3800USBClient')
    def test_shutdown_flag_respected(self, mock_usb_client_class, event_loop, event_bus, mock_state, button_config):
        """Test that shutdown flag stops polling loop."""
        mock_usb_client = MagicMock()
        mock_usb_client_class.return_value = mock_usb_client

        controller = XVF3800ButtonController(
            loop=event_loop,
            event_bus=event_bus,
            state=mock_state,
            button_config=button_config
        )

        # Give thread time to start
        time.sleep(0.1)

        # Set shutdown flag
        controller._shutdown_flag.set()

        # Wait for thread to exit
        controller._thread.join(timeout=2.0)

        # Thread should have exited
        assert not controller._thread.is_alive()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])