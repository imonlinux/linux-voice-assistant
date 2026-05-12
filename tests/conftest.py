"""Shared pytest configuration and fixtures."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock
import pytest

# Add parent directory to path for imports
TEST_DIR = Path(__file__).parent
REPO_DIR = TEST_DIR.parent
sys.path.insert(0, str(REPO_DIR))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_config_file(temp_dir):
    """Create a temporary config file."""
    def _create_config(config_dict):
        import json
        config_path = temp_dir / "test_config.json"
        with open(config_path, 'w') as f:
            json.dump(config_dict, f)
        return config_path
    return _create_config


@pytest.fixture
def temp_preferences_file(temp_dir):
    """Create a temporary preferences file."""
    import json
    from dataclasses import asdict
    from linux_voice_assistant.models import Preferences

    prefs = Preferences()
    prefs_path = temp_dir / "test_preferences.json"
    with open(prefs_path, 'w') as f:
        json.dump(asdict(prefs), f)
    return prefs_path


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def event_bus():
    """Create EventBus instance."""
    from linux_voice_assistant.event_bus import EventBus
    return EventBus(track_events=True)


@pytest.fixture
def mock_soundcard(monkeypatch):
    """Mock soundcard library for testing without audio hardware."""
    mock_mic = MagicMock()
    mock_mic.name = "Test Microphone"
    mock_mic.recorder = MagicMock()

    mock_sc = MagicMock()
    mock_sc.get_microphone = MagicMock(return_value=mock_mic)
    mock_sc.all_microphones = MagicMock(return_value=[mock_mic])
    mock_sc.default_microphone = MagicMock(return_value=mock_mic)

    # Patch both soundcard and potential import variations
    monkeypatch.setitem(sys.modules, 'soundcard', mock_sc)

    return mock_sc


@pytest.fixture
def mock_mpv_player(monkeypatch):
    """Mock mpv.Player for testing without audio playback."""
    mock_player = MagicMock()
    mock_player.audio_device_list = []
    mock_player.play = MagicMock()
    mock_player.stop = MagicMock()
    mock_player.pause = MagicMock()
    mock_player.set_volume = MagicMock()

    mock_mpv = MagicMock()
    mock_mpv.Player = MagicMock(return_value=mock_player)

    monkeypatch.setitem(sys.modules, 'mpv', mock_mpv)

    return mock_mpv


@pytest.fixture
def minimal_config(temp_config_file):
    """Create minimal valid configuration for testing."""
    config_dict = {
        "app": {
            "name": "test_device",
            "debug": False,
            "preferences_file": "preferences.json",
            "wakeup_sound": "",
            "thinking_sound": "",
            "timer_finished_sound": "",
            "event_sounds_enabled": True,
            "thinking_sound_loop": False,
            "listen_during_wake_sound": False
        },
        "audio": {
            "input_device": None,
            "output_device": None,
            "input_block_size": 1280,
            "volume_sync": False,
            "max_volume_percent": 100
        },
        "wake_word": {
            "directories": ["wakewords", "wakewords/openWakeWord"],
            "model": "ok_nabu",
            "stop_model": "stop",
            "download_dir": "wakewords/custom",
            "openwakeword_threshold": 0.5,
            "refractory_seconds": 0.5
        },
        "esphome": {
            "host": "0.0.0.0",
            "port": 6053
        },
        "led": {
            "led_type": "dotstar",
            "interface": "spi",
            "spi_device": "/dev/spidev0.0",
            "gpio_clk": 11,
            "gpio_mosi": 10,
            "num_leds": 12
        },
        "mqtt": {
            "enabled": False
        },
        "button": {
            "enabled": False
        }
    }
    return temp_config_file(config_dict)


@pytest.fixture
def minimal_state(event_loop, event_bus, temp_preferences_file):
    """Create minimal ServerState for testing."""
    from linux_voice_assistant.models import ServerState, Preferences

    prefs = Preferences()

    return ServerState(
        name="test_device",
        mac_address="aa:bb:cc:dd:ee:ff",
        event_bus=event_bus,
        loop=event_loop,
        entities=[],
        music_player=None,
        tts_player=None,
        available_wake_words={},
        wake_words={},
        active_wake_words=set(),
        stop_word=None,
        wake_word_sensitivity="Slightly sensitive",
        wakeup_sound="",
        thinking_sound="",
        timer_finished_sound="",
        preferences=prefs,
        preferences_path=temp_preferences_file,
        download_dir=Path("/tmp/test_download"),
        refractory_seconds=0.5,
        event_sounds_enabled=True,
        thinking_sound_loop=False,
        listen_during_wake_sound=False
    )


@pytest.fixture
def mock_state(event_loop, event_bus):
    """Create mock ServerState for end-to-end workflow tests."""
    from linux_voice_assistant.models import ServerState, Preferences

    state = MagicMock(spec=ServerState)
    state.loop = event_loop
    state.event_bus = event_bus
    state.preferences = MagicMock(spec=Preferences)
    state.preferences.volume_level = 0.5
    state.mic_mute = False
    return state


# Hardware-specific skip conditions
skip_if_no_xvf3800 = pytest.mark.skipif(
    not os.path.exists("/dev/bus/usb/001/"),  # Basic USB check
    reason="XVF3800 hardware not available"
)

skip_if_no_gpio = pytest.mark.skipif(
    not os.path.exists("/dev/gpiochip0") and not os.path.exists("/sys/class/gpio"),
    reason="GPIO hardware not available"
)

skip_if_no_spi = pytest.mark.skipif(
    not os.path.exists("/dev/spidev0.0"),
    reason="SPI device not available"
)


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "hardware: marks tests as requiring hardware (deselect with '-m \"not hardware\"')"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers dynamically."""
    for item in items:
        # Mark tests that require specific hardware
        if "xvf3800" in item.nodeid.lower():
            item.add_marker(pytest.mark.hardware)
        if "gpio" in item.nodeid.lower():
            item.add_marker(pytest.mark.hardware)
        if "spi" in item.nodeid.lower():
            item.add_marker(pytest.mark.hardware)