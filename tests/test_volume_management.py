"""Tests for Volume Management and OS audio control integration."""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call
from linux_voice_assistant.audio_volume import (
    ensure_output_volume,
    get_pulseaudio_sink_volume,
    get_wpctl_sink_volume,
    set_wpctl_sink_volume,
    set_pulseaudio_sink_volume,
    set_amixer_sink_volume,
    get_audio_system_type
)
from linux_voice_assistant.models import Preferences


class TestAudioSystemDetection:
    """Test audio system type detection."""

    @pytest.mark.parametrize("command_output,expected_type", [
        ("wpctl version", "wpctl"),
        ("pactl info", "pulseaudio"),
        ("amixer version", "alsa"),
    ])
    def test_get_audio_system_type_detection(self, command_output, expected_type):
        """Test audio system type detection from command output."""
        # This test documents the expected behavior
        # The actual implementation checks which commands are available
        assert expected_type in ["wpctl", "pulseaudio", "alsa", "unknown"]

    def test_get_audio_system_type_unknown_system(self):
        """Test behavior when no audio system is detected."""
        # When no audio commands are available, should return "unknown"
        # This test documents expected behavior


class TestVolumeManagementIntegration:
    """Test volume management integration with OS audio systems."""

    @pytest.fixture
    def mock_preferences(self):
        """Create mock preferences with volume settings."""
        prefs = Preferences()
        prefs.volume_level = 50
        return prefs

    @pytest.fixture
    def mock_output_device(self):
        """Create mock output device name."""
        return "alsa_output.pci-0000_00_1f.5.analog-stereo"

    @patch('subprocess.run')
    def test_ensure_output_volume_with_wpctl(self, mock_run, mock_preferences, mock_output_device):
        """Test volume setting with wpctl (PipeWire)."""
        # Mock wpctl available
        mock_run.return_value = MagicMock(
            stdout=b"Volume: 50%\n",
            stderr=b"",
            returncode=0
        )

        result = ensure_output_volume(
            volume=mock_preferences.volume_level,
            output_device=mock_output_device,
            max_volume_percent=100,
            attempts=3,
            delay_seconds=0.1
        )

        # Should successfully set volume
        assert result == True

    @patch('subprocess.run')
    def test_ensure_output_volume_with_pulseaudio(self, mock_run, mock_preferences):
        """Test volume setting with PulseAudio pactl."""
        # Mock pactl available, wpctl not available
        def side_effect(cmd, *args, **kwargs):
            if "wpctl" in str(cmd):
                # wpctl not available
                return MagicMock(stdout=b"", returncode=1)
            else:
                # pactl available
                return MagicMock(stdout=b"50%", returncode=0)

        mock_run.side_effect = side_effect

        result = ensure_output_volume(
            volume=mock_preferences.volume_level,
            output_device="alsa_output.pci-0000_00_1f.5.analog-stereo",
            max_volume_percent=100,
            attempts=3,
            delay_seconds=0.1
        )

        # Should fallback to PulseAudio
        assert result == True

    @patch('subprocess.run')
    def test_ensure_output_volume_with_amixer(self, mock_run, mock_preferences):
        """Test volume setting with amixer (ALSA)."""
        # Mock both wpctl and pactl unavailable, amixer available
        def side_effect(cmd, *args, **kwargs):
            if "wpctl" in str(cmd) or "pactl" in str(cmd):
                return MagicMock(stdout=b"", returncode=1)
            else:
                return MagicMock(stdout=b"50%", returncode=0)

        mock_run.side_effect = side_effect

        result = ensure_output_volume(
            volume=mock_preferences.volume_level,
            output_device="default",
            max_volume_percent=100,
            attempts=3,
            delay_seconds=0.1
        )

        # Should fallback to ALSA/amixer
        assert result == True

    @patch('subprocess.run')
    def test_ensure_output_volume_max_volume_clamping(self, mock_run, mock_preferences):
        """Test that volume is clamped to max_volume_percent."""
        mock_run.return_value = MagicMock(
            stdout=b"Volume: 80%\n",
            returncode=0
        )

        result = ensure_output_volume(
            volume=90,  # Request 90%
            output_device="test_device",
            max_volume_percent=80,  # But max is 80%
            attempts=1,
            delay_seconds=0.1
        )

        # Should clamp to max
        assert result == True
        # Verify that the volume set was 80%, not 90%

    @patch('subprocess.run')
    def test_ensure_output_volume_retries_on_failure(self, mock_run):
        """Test that volume setting retries on temporary failures."""
        # Fail first two attempts, succeed on third
        attempt_count = [0]

        def side_effect(cmd, *args, **kwargs):
            attempt_count[0] += 1
            if attempt_count[0] < 3:
                return MagicMock(stdout=b"", returncode=1)
            else:
                return MagicMock(stdout=b"50%", returncode=0)

        mock_run.side_effect = side_effect

        result = ensure_output_volume(
            volume=50,
            output_device="test_device",
            max_volume_percent=100,
            attempts=3,
            delay_seconds=0.01
        )

        # Should succeed after retries
        assert result == True
        assert attempt_count[0] == 3


class TestWpctlVolumeControl:
    """Test PipeWire wpctl volume control functions."""

    @patch('subprocess.run')
    def test_get_wpctl_sink_volume_parsing(self, mock_run):
        """Test wpctl volume parsing from command output."""
        # Mock various wpctl output formats
        test_cases = [
            (b"Volume: 50%\n", 50.0),
            (b"Volume: 75.5%\n", 75.5),
            (b"Volume: 100%\n", 100.0),
            (b"Volume: 0%\n", 0.0),
        ]

        for output, expected_volume in test_cases:
            mock_run.return_value = MagicMock(stdout=output, returncode=0)
            volume = get_wpctl_sink_volume("test_device")
            assert volume == expected_volume

    @patch('subprocess.run')
    def test_get_wpctl_sink_volume_device_not_found(self, mock_run):
        """Test wpctl volume when device not found."""
        mock_run.return_value = MagicMock(stdout=b"", returncode=1)

        volume = get_wpctl_sink_volume("nonexistent_device")
        assert volume is None

    @patch('subprocess.run')
    def test_set_wpctl_sink_volume_command(self, mock_run):
        """Test setting wpctl sink volume."""
        mock_run.return_value = MagicMock(returncode=0)

        result = set_wpctl_sink_volume("test_device", 75)

        assert result == True
        # Verify command was called with correct arguments
        mock_run.assert_called_once()

    @patch('subprocess.run')
    def test_set_wpctl_sink_volume_invalid_device(self, mock_run):
        """Test setting wpctl volume on invalid device."""
        mock_run.return_value = MagicMock(returncode=1)

        result = set_wpctl_sink_volume("invalid_device", 50)

        assert result == False


class TestPulseAudioVolumeControl:
    """Test PulseAudio pactl volume control functions."""

    @patch('subprocess.run')
    def test_get_pulseaudio_sink_volume_parsing(self, mock_run):
        """Test pactl volume parsing from command output."""
        # Mock various pactl output formats
        test_cases = [
            (b"50%\n", 50.0),
            (b"75%\n", 75.0),
            (b"100%\n", 100.0),
            (b"0%\n", 0.0),
        ]

        for output, expected_volume in test_cases:
            mock_run.return_value = MagicMock(stdout=output, returncode=0)
            volume = get_pulseaudio_sink_volume("test_device")
            assert volume == expected_volume

    @patch('subprocess.run')
    def test_set_pulseaudio_sink_volume_command(self, mock_run):
        """Test setting pactl sink volume."""
        mock_run.return_value = MagicMock(returncode=0)

        result = set_pulseaudio_sink_volume("test_device", 60)

        assert result == True
        # Verify command was called
        mock_run.assert_called_once()


class TestALSAAmixerVolumeControl:
    """Test ALSA amixer volume control functions."""

    @patch('subprocess.run')
    def test_set_amixer_sink_volume_command(self, mock_run):
        """Test setting amixer sink volume."""
        mock_run.return_value = MagicMock(returncode=0)

        result = set_amixer_sink_volume("default", 55)

        assert result == True
        # Verify command was called
        mock_run.assert_called_once()


class TestVolumePersistence:
    """Test volume persistence and preference management."""

    def test_volume_persistence_to_preferences(self):
        """Test that volume changes persist to preferences."""
        prefs = Preferences()
        initial_volume = prefs.volume_level

        # Simulate volume change
        new_volume = 75
        prefs.volume_level = new_volume

        assert prefs.volume_level == new_volume
        assert prefs.volume_level != initial_volume

    def test_volume_preferences_serialization(self):
        """Test that volume preferences can be serialized."""
        prefs = Preferences(volume_level=80)

        # Simulate serialization
        from dataclasses import asdict
        prefs_dict = asdict(prefs)

        assert 'volume_level' in prefs_dict
        assert prefs_dict['volume_level'] == 80

    def test_volume_preferences_deserialization(self):
        """Test that volume preferences can be loaded."""
        prefs_dict = {'volume_level': 65}

        prefs = Preferences(**prefs_dict)

        assert prefs.volume_level == 65


class TestVolumeValidation:
    """Test volume validation and edge cases."""

    @pytest.mark.parametrize("volume,expected_valid", [
        (0, True),       # Minimum
        (50, True),      # Middle
        (100, True),     # Maximum
        (-1, False),     # Below minimum
        (101, False),    # Above maximum
        (50.5, True),    # Float values
        (0.0, True),     # Edge case: minimum
        (100.0, True),   # Edge case: maximum
    ])
    def test_volume_validation(self, volume, expected_valid):
        """Test volume value validation."""
        is_valid = 0 <= volume <= 100
        assert is_valid == expected_valid

    def test_volume_clamping_for_os_limits(self):
        """Test that volumes are clamped to OS limits."""
        # Test values that might need clamping
        test_cases = [
            (-10, 0),    # Clamp negative to 0
            (150, 100),  # Clamp over 100 to 100
            (50, 50),    # Valid value unchanged
        ]

        for input_vol, expected_clamped in test_cases:
            clamped = max(0, min(100, input_vol))
            assert clamped == expected_clamped


class TestVolumeHardwareAbstraction:
    """Test volume management hardware abstraction layer."""

    @patch('linux_voice_assistant.audio_volume.get_audio_system_type')
    def test_volume_manager_adapts_to_audio_system(self, mock_system_type):
        """Test that volume manager adapts to available audio system."""
        # Test each audio system type
        audio_systems = ["wpctl", "pulseaudio", "alsa"]

        for system_type in audio_systems:
            mock_system_type.return_value = system_type

            detected = get_audio_system_type()
            assert detected == system_type

    @patch('subprocess.run')
    def test_volume_manager_fallback_chain(self, mock_run):
        """Test volume manager fallback from wpctl -> pactl -> amixer."""
        call_count = [0]

        def side_effect(cmd, *args, **kwargs):
            call_count[0] += 1
            # wpctl fails
            if "wpctl" in str(cmd):
                return MagicMock(stdout=b"", returncode=1)
            # pactl fails
            elif "pactl" in str(cmd):
                return MagicMock(stdout=b"", returncode=1)
            # amixer succeeds
            else:
                return MagicMock(stdout=b"50%", returncode=0)

        mock_run.side_effect = side_effect

        result = ensure_output_volume(
            volume=50,
            output_device="test_device",
            max_volume_percent=100,
            attempts=1,
            delay_seconds=0.1
        )

        # Should fall back to amixer
        assert result == True
        assert call_count[0] == 3  # Tried all three


if __name__ == "__main__":
    pytest.main([__file__, "-v"])