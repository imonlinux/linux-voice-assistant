"""Tests for configuration system."""

import json
import tempfile
from pathlib import Path

import pytest

from linux_voice_assistant.config import load_config_from_json


class TestConfigLoading:
    """Test configuration loading from files."""

    def test_load_valid_config(self):
        """Test loading a valid configuration file."""
        config_data = {
            "app": {
                "name": "test_device",
                "debug": False,
                "preferences_file": "preferences.json",
                "wakeup_sound": "sounds/wakeup/wake_word_triggered.flac",
                "thinking_sound": "sounds/thinking/processing.flac",
                "timer_finished_sound": "sounds/timer/timer_finished.flac",
                "event_sounds_enabled": True,
                "thinking_sound_loop": False,
                "listen_during_wake_sound": False,
            },
            "audio": {"input_device": None, "output_device": None, "input_block_size": 1280, "volume_sync": False, "max_volume_percent": 100},
            "wake_word": {
                "directories": ["wakewords", "wakewords/openWakeWord"],
                "model": "ok_nabu",
                "stop_model": "stop",
                "download_dir": "wakewords/custom",
                "openwakeword_threshold": 0.5,
                "refractory_seconds": 0.5,
            },
            "esphome": {"host": "0.0.0.0", "port": 6053},
            "led": {"led_type": "dotstar", "interface": "spi", "spi_device": "/dev/spidev0.0", "gpio_clk": 11, "gpio_mosi": 10, "num_leds": 12},
            "mqtt": {"enabled": False},
            "button": {"enabled": False},
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.app.name == "test_device"
            assert not config.app.debug
            assert config.audio.input_block_size == 1280
            assert config.wake_word.model == "ok_nabu"
            assert config.esphome.host == "0.0.0.0"
            assert config.esphome.port == 6053

        finally:
            temp_path.unlink(missing_ok=True)

    def test_load_config_with_missing_optional_fields(self):
        """Test loading config with missing optional fields uses defaults."""
        minimal_config = {"app": {"name": "minimal_device"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(minimal_config, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.app.name == "minimal_device"
            # Should have defaults for other fields
            assert hasattr(config, "audio")
            assert hasattr(config, "wake_word")
            assert hasattr(config, "esphome")

        finally:
            temp_path.unlink(missing_ok=True)

    def test_load_invalid_json(self):
        """Test loading invalid JSON raises appropriate error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            f.write("{ invalid json }")

        try:
            with pytest.raises(json.JSONDecodeError):
                load_config_from_json(temp_path)

        finally:
            temp_path.unlink(missing_ok=True)

    def test_load_nonexistent_file(self):
        """Test loading nonexistent file raises appropriate error."""
        nonexistent_path = Path("/tmp/nonexistent_config_file_12345.json")

        with pytest.raises(FileNotFoundError):
            load_config_from_json(nonexistent_path)


class TestConfigValidation:
    """Test configuration validation."""

    def test_port_validation(self):
        """Test port number validation."""
        valid_ports = [6053, 8080, 8888, 1024, 65535]

        for port in valid_ports:
            config_data = {"app": {"name": "test"}, "esphome": {"host": "0.0.0.0", "port": port}}

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                temp_path = Path(f.name)
                json.dump(config_data, f)

            try:
                config = load_config_from_json(temp_path)
                assert config.esphome.port == port

            finally:
                temp_path.unlink(missing_ok=True)

    def test_wake_word_threshold_validation(self):
        """Test wake word threshold is in valid range."""
        valid_thresholds = [0.0, 0.25, 0.5, 0.75, 1.0]

        for threshold in valid_thresholds:
            config_data = {"app": {"name": "test"}, "wake_word": {"model": "ok_nabu", "openwakeword_threshold": threshold}}

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                temp_path = Path(f.name)
                json.dump(config_data, f)

            try:
                config = load_config_from_json(temp_path)
                assert config.wake_word.openwakeword_threshold == threshold

            finally:
                temp_path.unlink(missing_ok=True)

    def test_led_type_validation(self):
        """Test LED type is one of the supported types."""
        valid_types = ["dotstar", "neopixel", "xvf3800"]

        for led_type in valid_types:
            config_data = {"app": {"name": "test"}, "led": {"led_type": led_type, "num_leds": 12}}

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                temp_path = Path(f.name)
                json.dump(config_data, f)

            try:
                config = load_config_from_json(temp_path)
                assert config.led.led_type == led_type

            finally:
                temp_path.unlink(missing_ok=True)


class TestConfigDefaults:
    """Test configuration default values."""

    def test_audio_defaults(self):
        """Test audio section defaults."""
        config_data = {"app": {"name": "test"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            # Check audio defaults
            assert config.audio.input_device is None
            assert config.audio.output_device is None
            assert hasattr(config.audio, "input_block_size")
            assert hasattr(config.audio, "volume_sync")

        finally:
            temp_path.unlink(missing_ok=True)

    def test_esphome_defaults(self):
        """Test ESPHome section defaults."""
        config_data = {"app": {"name": "test"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            # Check ESPHome defaults
            assert config.esphome.host == "0.0.0.0"
            assert config.esphome.port == 6053

        finally:
            temp_path.unlink(missing_ok=True)

    def test_wake_word_defaults(self):
        """Test wake word section defaults."""
        config_data = {"app": {"name": "test"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            # Check wake word defaults
            assert hasattr(config.wake_word, "directories")
            assert hasattr(config.wake_word, "model")
            assert hasattr(config.wake_word, "stop_model")
            assert hasattr(config.wake_word, "openwakeword_threshold")

        finally:
            temp_path.unlink(missing_ok=True)


class TestConfigIntegration:
    """Test configuration integration with other components."""

    def test_config_with_sound_paths(self):
        """Test configuration with sound file paths."""
        config_data = {
            "app": {
                "name": "test_device",
                "wakeup_sound": "sounds/wakeup/wake_word_triggered.flac",
                "thinking_sound": "sounds/thinking/processing.flac",
                "timer_finished_sound": "sounds/timer/timer_finished.flac",
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.app.wakeup_sound == "sounds/wakeup/wake_word_triggered.flac"
            assert config.app.thinking_sound == "sounds/thinking/processing.flac"
            assert config.app.timer_finished_sound == "sounds/timer/timer_finished.flac"

        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_wake_volume(self):
        """Test wake_volume config default (0 = follow master, 1-100 = fixed)."""
        config_data = {"app": {"name": "test_device", "wake_volume": 35}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.app.wake_volume == 35

        finally:
            temp_path.unlink(missing_ok=True)

    def test_wake_volume_defaults_to_100(self):
        """Wake volume default preserves the historical full-volume chime."""
        config_data = {"app": {"name": "test_device"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.app.wake_volume == 100

        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_mqtt_enabled(self):
        """Test configuration with MQTT enabled."""
        config_data = {"app": {"name": "test"}, "mqtt": {"enabled": True, "host": "localhost", "port": 1883, "username": "user", "password": "pass", "discovery_prefix": "homeassistant"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.mqtt.enabled
            assert config.mqtt.host == "localhost"
            assert config.mqtt.port == 1883
            assert config.mqtt.username == "user"
            # discovery_prefix is no longer a supported config field
            assert not hasattr(config.mqtt, "discovery_prefix")

        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_mqtt_explicitly_disabled(self):
        """Explicit enabled=false wins even when mqtt.host is set.

        Regression: the loader used to force enabled=True whenever host
        was present, silently restarting MQTT on configs that disabled it.
        """
        config_data = {
            "app": {"name": "test"},
            "mqtt": {
                "enabled": False,
                "host": "localhost",
                "port": 1883,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)
            assert not config.mqtt.enabled
            assert config.mqtt.host == "localhost"
        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_mqtt_host_implies_enabled_when_flag_omitted(self):
        """Host set + no enabled key still enables MQTT (tray back-compat)."""
        config_data = {
            "app": {"name": "test"},
            "mqtt": {
                "host": "localhost",
                "port": 1883,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)
            assert config.mqtt.enabled
        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_mqtt_absent_stays_disabled(self):
        """No mqtt section at all: disabled, even though defaults are None-host."""
        config_data = {"app": {"name": "test"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)
            assert not config.mqtt.enabled
        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_button_enabled(self):
        """Test configuration with button enabled."""
        config_data = {"app": {"name": "test"}, "button": {"enabled": True, "mode": "gpio", "pin": 17, "press_time_ms": 50, "long_press_time_ms": 1000}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.button.enabled
            assert config.button.mode == "gpio"
            assert config.button.pin == 17
            # press_time_ms and long_press_time_ms are no longer supported config fields
            # Button now uses internal defaults for timing
            assert not hasattr(config.button, "press_time_ms")
            assert not hasattr(config.button, "long_press_time_ms")

        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_xvf3800_button(self):
        """Test configuration with XVF3800 button mode."""
        config_data = {"app": {"name": "test"}, "button": {"enabled": True, "mode": "xvf3800"}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.button.enabled
            assert config.button.mode == "xvf3800"

        finally:
            temp_path.unlink(missing_ok=True)

    def test_config_with_sendspin(self):
        """Test configuration with Sendspin enabled."""
        config_data = {"app": {"name": "test"}, "sendspin": {"enabled": True, "host": "localhost", "port": 8909, "initial": {"volume": 80}}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            # Sendspin config should be preserved as-is
            assert hasattr(config, "sendspin")

        finally:
            temp_path.unlink(missing_ok=True)


class TestConfigSoundPaths:
    """Test sound path resolution and validation."""

    def test_sound_path_resolution(self):
        """Test sound paths are resolved correctly."""
        config_data = {"app": {"name": "test", "wakeup_sound": "sounds/wakeup/wake_word_triggered.flac", "thinking_sound": "", "timer_finished_sound": "sounds/timer/timer_finished.flac"}}  # Disabled

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(config_data, f)

        try:
            config = load_config_from_json(temp_path)

            assert config.app.wakeup_sound != ""
            assert config.app.thinking_sound == ""  # Empty means disabled
            assert config.app.timer_finished_sound != ""

        finally:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
