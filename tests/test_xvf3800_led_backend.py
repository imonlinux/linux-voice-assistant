"""Tests for XVF3800 LED Backend hardware integration."""

import pytest
import struct
import time
from unittest.mock import Mock, MagicMock, patch
from linux_voice_assistant.xvf3800_led_backend import (
    _ReSpeaker,
    XVF3800USBDevice,
    XVF3800LedBackend,
    PARAMETERS,
    CONTROL_SUCCESS,
    SERVICER_COMMAND_RETRY
)


class TestXVF3800Parameters:
    """Test XVF3800 parameter definitions."""

    def test_parameters_dict_structure(self):
        """Test PARAMETERS dictionary contains expected entries."""
        # Test critical LED parameters
        assert "LED_EFFECT" in PARAMETERS
        assert "LED_BRIGHTNESS" in PARAMETERS
        assert "LED_SPEED" in PARAMETERS
        assert "LED_COLOR" in PARAMETERS
        assert "LED_RING_COLOR" in PARAMETERS

        # Test GPO parameters
        assert "GPO_READ_VALUES" in PARAMETERS
        assert "GPO_WRITE_VALUE" in PARAMETERS

        # Test device control parameters
        assert "VERSION" in PARAMETERS
        assert "REBOOT" in PARAMETERS

    def test_led_effect_parameters(self):
        """Test LED effect parameter structure."""
        resid, cmdid, count, access, data_type = PARAMETERS["LED_EFFECT"]

        assert resid == 20  # GPO_SERVICER_RESID
        assert cmdid == 12
        assert count == 1
        assert access == "rw"  # Read/write
        assert data_type == "uint8"

    def test_led_ring_color_parameters(self):
        """Test LED ring color parameter structure."""
        resid, cmdid, count, access, data_type = PARAMETERS["LED_RING_COLOR"]

        assert resid == 20  # GPO_SERVICER_RESID
        assert cmdid == 19
        assert count == 12  # 12 LEDs
        assert access == "rw"
        assert data_type == "uint32"

    def test_gpo_parameters(self):
        """Test GPO parameter structures."""
        read_resid, read_cmdid, read_count, read_access, read_type = PARAMETERS["GPO_READ_VALUES"]
        write_resid, write_cmdid, write_count, write_access, write_type = PARAMETERS["GPO_WRITE_VALUE"]

        assert read_resid == 20
        assert read_cmdid == 0
        assert read_count == 5  # 5 GPO pins
        assert read_access == "ro"

        assert write_resid == 20
        assert write_cmdid == 1
        assert write_count == 2  # [pin, value]
        assert write_access == "wo"


class TestReSpeakerLowLevel:
    """Test _ReSpeaker low-level USB wrapper."""

    def test_constants(self):
        """Test ReSpeaker constants."""
        assert _ReSpeaker.VID == 0x2886
        assert _ReSpeaker.PID == 0x001A
        assert _ReSpeaker.TIMEOUT_MS == 100_000

    def test_initialization(self):
        """Test ReSpeaker initialization."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        assert resp.dev == mock_device

    @patch('linux_voice_assistant.xvf3800_led_backend.usb.util.dispose_resources')
    def test_context_manager(self, mock_dispose):
        """Test ReSpeaker context manager support."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with _ReSpeaker(mock_device) as resp_ctx:
            assert resp_ctx == resp

        # Verify cleanup was called
        assert resp.dev is None

    def test_pack_values_uint8(self):
        """Test packing uint8 values."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        result = resp._pack_values("uint8", [1, 2, 3])

        assert result == bytes([1, 2, 3])

    def test_pack_values_uint32(self):
        """Test packing uint32 values."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        result = resp._pack_values("uint32", [0x12345678, 0x00FF00FF])

        expected = struct.pack("<I", 0x12345678) + struct.pack("<I", 0x00FF00FF)
        assert result == expected

    def test_pack_values_unsupported_type(self):
        """Test packing unsupported data type raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with pytest.raises(ValueError) as exc_info:
            resp._pack_values("unsupported", [1, 2, 3])

        assert "Unsupported data type" in str(exc_info.value)

    def test_unpack_values_uint8(self):
        """Test unpacking uint8 values."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        result = resp._unpack_values("uint8", bytes([1, 2, 3, 4, 5]), 5)

        assert result == [1, 2, 3, 4, 5]

    def test_unpack_values_uint32(self):
        """Test unpacking uint32 values."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        raw = struct.pack("<I", 0x12345678) + struct.pack("<I", 0x00FF00FF)
        result = resp._unpack_values("uint32", raw, 2)

        assert result == [0x12345678, 0x00FF00FF]

    def test_unpack_values_int32(self):
        """Test unpacking int32 values."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        raw = struct.pack("<i", -12345) + struct.pack("<i", 67890)
        result = resp._unpack_values("int32", raw, 2)

        assert result == [-12345, 67890]

    def test_read_length_calculation(self):
        """Test read length calculation for different data types."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        # uint8: count + status byte
        assert resp._read_length("uint8", 5) == 6

        # uint32/int32: (count * 4) + status byte
        assert resp._read_length("uint32", 12) == 49  # (12 * 4) + 1
        assert resp._read_length("int32", 3) == 13   # (3 * 4) + 1

    def test_read_length_unsupported_type(self):
        """Test read length calculation for unsupported type raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with pytest.raises(ValueError):
            resp._read_length("unsupported", 1)

    def test_write_success(self):
        """Test successful parameter write."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        resp.write("LED_EFFECT", [2])  # Rainbow effect

        mock_device.ctrl_transfer.assert_called_once()

        # Verify call arguments
        call_args = mock_device.ctrl_transfer.call_args
        args = call_args[0]

        # Check request type (CTRL_OUT | vendor | device)
        assert args[0] & 0x40  # CTRL_OUT bit
        assert args[0] & 0x02  # CTRL_TYPE_VENDOR
        assert args[0] & 0x01  # CTRL_RECIPIENT_DEVICE

        # Check command ID
        assert args[2] == 12  # LED_EFFECT cmdid

        # Check payload
        assert args[5] == bytes([2])

    def test_write_read_only_parameter(self):
        """Test writing to read-only parameter raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with pytest.raises(ValueError) as exc_info:
            resp.write("VERSION", [1, 2, 3])

        assert "read-only" in str(exc_info.value)

    def test_write_unknown_parameter(self):
        """Test writing unknown parameter raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with pytest.raises(ValueError) as exc_info:
            resp.write("UNKNOWN_PARAM", [1])

        assert "Unknown XVF3800 parameter" in str(exc_info.value)

    def test_write_wrong_count(self):
        """Test writing with wrong value count raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        # LED_EFFECT expects 1 value, not 3
        with pytest.raises(ValueError) as exc_info:
            resp.write("LED_EFFECT", [1, 2, 3])

        assert "expects 1 values, got 3" in str(exc_info.value)

    def test_read_success(self):
        """Test successful parameter read."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [0, 1, 2, 3]  # status + data
        resp = _ReSpeaker(mock_device)

        result = resp.read("VERSION")

        assert result == [1, 2, 3]

    def test_read_with_retry(self):
        """Test read with SERVICER_COMMAND_RETRY status."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        # First call returns retry status, second succeeds
        mock_device.ctrl_transfer.side_effect = [
            [SERVICER_COMMAND_RETRY],  # Retry
            [CONTROL_SUCCESS, 1, 2, 3]  # Success
        ]

        result = resp.read("VERSION", max_retries=2)

        assert result == [1, 2, 3]
        assert mock_device.ctrl_transfer.call_count == 2

    def test_read_write_only_parameter(self):
        """Test reading write-only parameter raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with pytest.raises(ValueError) as exc_info:
            resp.read("GPO_WRITE_VALUE")

        assert "write-only" in str(exc_info.value)

    def test_read_unknown_parameter(self):
        """Test reading unknown parameter raises error."""
        mock_device = MagicMock()
        resp = _ReSpeaker(mock_device)

        with pytest.raises(ValueError) as exc_info:
            resp.read("UNKNOWN_PARAM")

        assert "Unknown XVF3800 parameter" in str(exc_info.value)

    def test_read_empty_response(self):
        """Test read with empty response raises error."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = []
        resp = _ReSpeaker(mock_device)

        with pytest.raises(RuntimeError) as exc_info:
            resp.read("VERSION")

        assert "Empty response" in str(exc_info.value)

    def test_read_error_status(self):
        """Test read with error status raises error."""
        mock_device = MagicMock()
        mock_device.ctrl_transfer.return_value = [255, 0, 0]  # Error status
        resp = _ReSpeaker(mock_device)

        with pytest.raises(RuntimeError) as exc_info:
            resp.read("VERSION")

        assert "control read failed" in str(exc_info.value)


class TestXVF3800USBDevice:
    """Test XVF3800USBDevice high-level interface."""

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_initialization_success(self, mock_find):
        """Test successful device initialization."""
        mock_resp = MagicMock()
        mock_find.return_value = mock_resp

        device = XVF3800USBDevice()

        assert device._rsp == mock_resp

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_initialization_failure(self, mock_find):
        """Test device initialization failure."""
        mock_find.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            XVF3800USBDevice()

        assert "USB device not found" in str(exc_info.value)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_reboot(self, mock_find):
        """Test device reboot command."""
        mock_resp = MagicMock()
        mock_find.return_value = mock_resp

        device = XVF3800USBDevice()
        device.reboot()

        mock_resp.write.assert_called_once_with("REBOOT", [1])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_save_configuration(self, mock_find):
        """Test save configuration command."""
        mock_resp = MagicMock()
        mock_find.return_value = mock_resp

        device = XVF3800USBDevice()
        device.save_configuration()

        mock_resp.write.assert_called_once_with("SAVE_CONFIGURATION", [1])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_audio_routing(self, mock_find):
        """Test audio routing configuration."""
        mock_resp = MagicMock()
        mock_find.return_value = mock_resp

        device = XVF3800USBDevice()
        device.set_audio_mgr_op_l(category=1, source=2)
        device.set_audio_mgr_op_r(category=3, source=4)

        # Verify left channel routing
        mock_resp.write.assert_any_call("AUDIO_MGR_OP_L", [1, 2])

        # Verify right channel routing
        mock_resp.write.assert_any_call("AUDIO_MGR_OP_R", [3, 4])

    @patch('linux_voice_assistant.xvf3800_led_backend.usb.core.find')
    def test_wait_for_reenumeration(self, mock_usb_find):
        """Test waiting for device re-enumeration."""
        # Simulate device disappearing and reappearing
        mock_usb_find.side_effect = [
            MagicMock(),  # Device exists initially
            None,  # Device disappears
            None,  # Still gone
            MagicMock(),  # Device reappears
        ]

        XVF3800USBDevice.wait_for_reenumeration(timeout_s=1.0, settle_s=0.1)

        # Should have called find multiple times
        assert mock_usb_find.call_count >= 3


class TestXVF3800LedBackend:
    """Test XVF3800 LED Backend high-level interface."""

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_initialization_with_per_led_support(self, mock_find):
        """Test LED backend initialization with per-LED support."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR succeeds (firmware supports it)
            [1, 2, 3],  # VERSION read
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        assert backend.supports_per_led == True
        assert backend._dev == mock_resp

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_initialization_without_per_led_support(self, mock_find):
        """Test LED backend initialization without per-LED support."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            RuntimeError("Parameter not supported"),  # LED_RING_COLOR fails
            [1, 2, 3],  # VERSION read
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        assert backend.supports_per_led == False

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_initialization_device_not_found(self, mock_find):
        """Test LED backend initialization when device not found."""
        mock_find.return_value = None

        with pytest.raises(RuntimeError) as exc_info:
            XVF3800LedBackend()

        assert "USB device not found" in str(exc_info.value)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_effect(self, mock_find):
        """Test setting LED effect."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_effect(2)  # Rainbow effect

        mock_resp.write.assert_called_with("LED_EFFECT", [2])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_brightness(self, mock_find):
        """Test setting LED brightness."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_brightness(200)

        mock_resp.write.assert_called_with("LED_BRIGHTNESS", [200])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_brightness_clamping(self, mock_find):
        """Test brightness value clamping."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        # Test upper bound
        backend.set_brightness(300)
        mock_resp.write.assert_called_with("LED_BRIGHTNESS", [255])

        # Test lower bound
        backend.set_brightness(-10)
        mock_resp.write.assert_called_with("LED_BRIGHTNESS", [0])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_speed(self, mock_find):
        """Test setting LED effect speed."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_speed(1)  # Medium speed

        mock_resp.write.assert_called_with("LED_SPEED", [1])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_color(self, mock_find):
        """Test setting LED color."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_color(255, 128, 0)  # Orange

        # Calculate expected color value: (r << 16) | (g << 8) | b
        expected = (255 << 16) | (128 << 8) | 0

        mock_resp.write.assert_called_with("LED_COLOR", [expected])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_color_clamping(self, mock_find):
        """Test color value clamping."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_color(300, -50, 999)  # Invalid values

        # Should be clamped to 0-255 range
        call_args = mock_resp.write.call_args
        color_value = call_args[0][1][0]

        # Extract RGB components
        r = (color_value >> 16) & 0xFF
        g = (color_value >> 8) & 0xFF
        b = color_value & 0xFF

        assert r == 255  # Max
        assert g == 0    # Min
        assert b == 255  # Max

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_ring_colors(self, mock_find):
        """Test setting individual ring LED colors."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_ring_colors([0xFF0000, 0x00FF00, 0x0000FF] + [0] * 9)

        mock_resp.write.assert_called_once()
        call_args = mock_resp.write.call_args
        assert call_args[0][0] == "LED_RING_COLOR"

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_ring_colors_wrong_count(self, mock_find):
        """Test setting ring colors with wrong count raises error."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        with pytest.raises(ValueError) as exc_info:
            backend.set_ring_colors([0xFF0000, 0x00FF00])  # Only 2 colors

        assert "expects 12 values, got 2" in str(exc_info.value)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_ring_colors_not_supported(self, mock_find):
        """Test setting ring colors when not supported raises error."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            RuntimeError("Not supported"),  # LED_RING_COLOR fails
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        with pytest.raises(RuntimeError) as exc_info:
            backend.set_ring_colors([0xFF0000] * 12)

        assert "not supported" in str(exc_info.value)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_ring_rgb(self, mock_find):
        """Test setting ring colors with RGB tuples."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        # Create 12 RGB tuples
        colors = [(255, 0, 0), (0, 255, 0)] + [(0, 0, 255)] * 10
        backend.set_ring_rgb(colors)

        # Should convert to 0xRRGGBB format and call set_ring_colors
        mock_resp.write.assert_called_once()

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_set_ring_solid(self, mock_find):
        """Test setting all ring LEDs to solid color."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.set_ring_solid(100, 150, 200)

        # Should set all 12 LEDs to the same color
        mock_resp.write.assert_called_once()

        # Verify all 12 LEDs have same color
        call_args = mock_resp.write.call_args
        colors = call_args[0][1]

        expected_color = (100 << 16) | (150 << 8) | 200
        assert all(c == expected_color for c in colors)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_clear_ring(self, mock_find):
        """Test clearing ring (turning off all LEDs)."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.clear_ring()

        # Should set all LEDs to 0 (off)
        mock_resp.write.assert_called_once_with("LED_RING_COLOR", [0] * 12)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_clear_ring_legacy_fallback(self, mock_find):
        """Test clear ring falls back to legacy mode when per-LED not supported."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            RuntimeError("Not supported"),  # LED_RING_COLOR fails
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.clear_ring()

        # Should use legacy fallback: effect off, brightness 0
        mock_resp.write.assert_any_call("LED_EFFECT", [0])
        mock_resp.write.assert_any_call("LED_BRIGHTNESS", [0])

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_get_version(self, mock_find):
        """Test getting firmware version."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        version = backend.get_version()

        assert version == (1, 2, 3)

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_get_version_unavailable(self, mock_find):
        """Test getting version when unavailable."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            RuntimeError("Read error"),  # VERSION fails
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        version = backend.get_version()

        assert version is None

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_close(self, mock_find):
        """Test closing LED backend."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_resp.close = MagicMock()
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()
        backend.close()

        mock_resp.close.assert_called_once()


class TestXVF3800LedBackendErrorHandling:
    """Test XVF3800 LED Backend error handling."""

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_led_power_ensure_on_operations(self, mock_find):
        """Test that LED power is ensured before critical operations."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [0, 1, 1, 0, 0],  # GPO_READ_VALUES: WS2812 power OFF
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        # During initialization, WS2812 power should be enabled
        # Check that GPO_WRITE_VALUE was called to enable power
        power_enable_calls = [call for call in mock_resp.write.call_args_list
                            if call[0][0] == "GPO_WRITE_VALUE" and call[0][1] == [33, 1]]

        assert len(power_enable_calls) > 0, "WS2812 LED power should be enabled during initialization"

    @patch('linux_voice_assistant.xvf3800_led_backend._find_device')
    def test_led_power_check_before_ring_operations(self, mock_find):
        """Test that LED power is checked before ring operations."""
        mock_resp = MagicMock()
        mock_resp.read.side_effect = [
            [0, 1, 1, 0, 0],  # GPO_READ_VALUES: WS2812 power OFF
            [255, 255, 255],  # LED_RING_COLOR
            [1, 2, 3],  # VERSION
        ]
        mock_find.return_value = mock_resp

        backend = XVF3800LedBackend()

        # Reset mock to track calls during operation
        mock_resp.reset_mock()

        # Setup GPO read to return power off
        mock_resp.read.return_value = [0, 1, 1, 0, 0]

        # Perform ring operation
        backend.set_ring_solid(255, 0, 0)

        # Should have attempted to re-enable power
        write_calls = [call for call in mock_resp.write.call_args_list
                     if call[0][0] == "GPO_WRITE_VALUE"
                     and call[0][1] == [33, 1]]

        assert len(write_calls) >= 1, "WS2812 LED power should be re-enabled if off"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])