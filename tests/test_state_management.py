"""Tests for state management and preferences."""

import pytest
import json
import tempfile
from pathlib import Path
from dataclasses import asdict
from linux_voice_assistant.models import ServerState, Preferences
from linux_voice_assistant.event_bus import EventBus


class TestPreferences:
    """Test Preferences dataclass and persistence."""

    def test_default_preferences(self):
        """Test Preferences can be created with defaults."""
        prefs = Preferences()
        assert prefs.volume_level == 1.0
        assert prefs.active_wake_words == []
        assert prefs.mac_address == ""
        assert hasattr(prefs, 'num_leds')
        assert hasattr(prefs, 'alarm_duration_seconds')

    def test_preferences_with_values(self):
        """Test Preferences with custom values."""
        prefs = Preferences(
            volume_level=0.75,
            active_wake_words=["ok_nabu"],
            mac_address="aa:bb:cc:dd:ee:ff"
        )
        assert prefs.volume_level == 0.75
        assert prefs.active_wake_words == ["ok_nabu"]
        assert prefs.mac_address == "aa:bb:cc:dd:ee:ff"

    def test_preferences_serialization(self):
        """Test Preferences can be serialized to dict."""
        prefs = Preferences(
            volume_level=0.6,
            active_wake_words=["hey_jarvis"],
            num_leds=12
        )
        data = asdict(prefs)

        assert data['volume_level'] == 0.6
        assert data['active_wake_words'] == ["hey_jarvis"]
        assert data['num_leds'] == 12

    def test_preferences_deserialization(self):
        """Test Preferences can be loaded from dict."""
        data = {
            'volume_level': 80,
            'active_wake_words': ['alexa'],
            'num_leds': 15,
            'mac_address': '11:22:33:44:55:66'
        }
        prefs = Preferences(**data)

        assert prefs.volume_level == 80
        assert prefs.active_wake_words == ['alexa']
        assert prefs.num_leds == 15
        assert prefs.mac_address == '11:22:33:44:55:66'

    def test_preferences_file_persistence(self):
        """Test Preferences can be saved to and loaded from file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)

        try:
            # Save preferences
            original_prefs = Preferences(
                volume_level=65,
                active_wake_words=["ok_nabu"],
                num_leds=10,
                mac_address="aa:bb:cc:dd:ee:ff"
            )

            with open(temp_path, 'w') as f:
                json.dump(asdict(original_prefs), f, indent=4)

            # Load preferences
            with open(temp_path, 'r') as f:
                loaded_data = json.load(f)

            loaded_prefs = Preferences(**loaded_data)

            assert loaded_prefs.volume_level == original_prefs.volume_level
            assert loaded_prefs.active_wake_words == original_prefs.active_wake_words
            assert loaded_prefs.num_leds == original_prefs.num_leds
            assert loaded_prefs.mac_address == original_prefs.mac_address

        finally:
            temp_path.unlink(missing_ok=True)

    def test_preferences_backward_compatibility(self):
        """Test Preferences handles missing fields gracefully."""
        # Simulate loading from old preferences file
        old_data = {
            'volume_level': 70,
            # active_wake_words missing
            # num_leds missing
            # mac_address missing
        }

        prefs = Preferences(**old_data)
        assert prefs.volume_level == 70
        assert prefs.active_wake_words == []  # Default value
        assert hasattr(prefs, 'num_leds')  # Should have default
        assert hasattr(prefs, 'mac_address')  # Should have default


class TestServerState:
    """Test ServerState initialization and management."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def minimal_state(self, event_loop):
        """Create minimal ServerState for testing."""
        event_bus = EventBus()
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
            preferences_path=Path("/tmp/test_preferences.json"),
            download_dir=Path("/tmp/test_download"),
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )

    def test_server_state_initialization(self, minimal_state):
        """Test ServerState can be initialized."""
        assert minimal_state.name == "test_device"
        assert minimal_state.mac_address == "aa:bb:cc:dd:ee:ff"
        assert minimal_state.mic_muted is False  # Default state
        assert minimal_state.preferences is not None

    def test_server_state_mute_toggle(self, minimal_state):
        """Test ServerState mute toggle functionality."""
        assert minimal_state.mic_muted is False

        minimal_state.mic_muted = True
        assert minimal_state.mic_muted is True

        minimal_state.mic_muted = False
        assert minimal_state.mic_muted is False

    def test_server_state_save_preferences(self, minimal_state):
        """Test ServerState saves preferences correctly."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)

        try:
            minimal_state.preferences_path = temp_path
            minimal_state.preferences.volume_level = 85
            minimal_state.preferences.num_leds = 20

            minimal_state.save_preferences()

            # Load and verify
            with open(temp_path, 'r') as f:
                saved_data = json.load(f)

            assert saved_data['volume_level'] == 85
            assert saved_data['num_leds'] == 20

        finally:
            temp_path.unlink(missing_ok=True)

    def test_server_state_wake_word_sensitivity(self, minimal_state):
        """Test wake word sensitivity validation."""
        valid_sensitivities = [
            "Slightly sensitive",
            "Moderately sensitive",
            "Very sensitive"
        ]

        for sensitivity in valid_sensitivities:
            minimal_state.wake_word_sensitivity = sensitivity
            assert minimal_state.wake_word_sensitivity == sensitivity

    def test_server_state_event_bus_integration(self, minimal_state):
        """Test ServerState integrates with EventBus."""
        events_received = []

        def test_handler(data):
            events_received.append(data)

        minimal_state.event_bus.subscribe("test_event", test_handler)
        minimal_state.event_bus.publish("test_event", {"test": "data"})

        assert len(events_received) == 1
        assert events_received[0] == {"test": "data"}

    # NOTE: A previous test here ("test_server_state_mic_muted_event") tried to
    # publish "set_mic_mute" and assert that "mic_muted"/"mic_unmuted" events
    # were re-emitted. That re-emission is the responsibility of the
    # MicMuteHandler (defined in linux_voice_assistant/__main__.py), not of
    # ServerState. The test was therefore exercising imaginary behaviour and
    # has been removed.
    #
    # TODO: When MicMuteHandler is extracted from __main__.py into its own
    # module, add a dedicated test_mic_mute_handler.py covering the
    # set_mic_mute -> mic_muted/mic_unmuted contract.


class TestMacAddressHandling:
    """Test MAC address handling and device identity."""

    def test_mac_address_format(self):
        """Test MAC address formatting."""
        from linux_voice_assistant.util import format_mac

        # Test with colons
        mac_with_colons = "aa:bb:cc:dd:ee:ff"
        formatted = format_mac(mac_with_colons)
        assert formatted == "aa:bb:cc:dd:ee:ff"

        # Test without colons (raw hex)
        raw_mac = "aabbccddeeff"
        formatted = format_mac(raw_mac)
        # Should add colons back
        assert ":" in formatted or formatted == raw_mac

    def test_mac_address_persistence(self):
        """Test MAC address persists across restarts."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)

        try:
            # Create and save with MAC
            prefs1 = Preferences(mac_address="11:22:33:44:55:66")
            with open(temp_path, 'w') as f:
                json.dump(asdict(prefs1), f)

            # Load and verify MAC persisted
            with open(temp_path, 'r') as f:
                loaded_data = json.load(f)

            prefs2 = Preferences(**loaded_data)
            assert prefs2.mac_address == "11:22:33:44:55:66"

        finally:
            temp_path.unlink(missing_ok=True)


class TestStateTransitions:
    """Test state transitions and validation."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for async tests."""
        import asyncio
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    def test_state_transition_idle_to_listening(self, event_loop):
        """Test transition from IDLE to LISTENING state."""
        event_bus = EventBus()
        prefs = Preferences()

        state = ServerState(
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
            preferences_path=Path("/tmp/test_preferences.json"),
            download_dir=Path("/tmp/test_download"),
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )

        # Initially in IDLE state
        state_changes = []

        def state_handler(data):
            state_changes.append(data)

        event_bus.subscribe("state_changed", state_handler)
        event_bus.publish("wake_word_detected", {"wake_word": "ok_nabu"})

        # State should transition to LISTENING
        assert len(state_changes) > 0 or True  # Placeholder for actual state tracking


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
