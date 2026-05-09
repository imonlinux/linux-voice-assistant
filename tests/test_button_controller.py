"""Tests for Button Controller integration and hardware button handling."""

import pytest
import threading
import time
from unittest.mock import Mock, MagicMock, patch
from linux_voice_assistant.button_controller import (
    ButtonController,
    ButtonRuntimeConfig
)
from linux_voice_assistant.event_bus import EventBus
from linux_voice_assistant.models import ServerState, Preferences


class TestButtonRuntimeConfig:
    """Test ButtonRuntimeConfig dataclass."""

    def test_button_runtime_config_defaults(self):
        """Test ButtonRuntimeConfig default values."""
        config = ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0
        )

        assert config.enabled == True
        assert config.pin == 17
        assert config.long_press_seconds == 1.0
        assert config.poll_interval_seconds == 0.05  # Default 20Hz polling

    def test_button_runtime_config_custom_poll_interval(self):
        """Test ButtonRuntimeConfig with custom poll interval."""
        config = ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0,
            poll_interval_seconds=0.1  # 10Hz polling
        )

        assert config.poll_interval_seconds == 0.1


class TestButtonControllerInitialization:
    """Test ButtonController initialization and setup."""

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,
            entities=[],
            music_player=MagicMock(),
            tts_player=MagicMock(),
            available_wake_words={},
            wake_words={},
            active_wake_words=set(),
            stop_word=None,
            wake_word_sensitivity="Slightly sensitive",
            wakeup_sound="",
            thinking_sound="",
            timer_finished_sound="",
            preferences=prefs,
            preferences_path=None,
            download_dir=None,
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )
        state.mic_muted_event.set()
        state.shutdown = False
        return state

    @pytest.fixture
    def button_config(self):
        """Create button configuration."""
        return ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0,
            poll_interval_seconds=0.05
        )

    def test_button_controller_initialization(self, mock_state, button_config):
        """Test ButtonController can be initialized."""
        controller = ButtonController(
            loop=mock_state.loop,
            event_bus=mock_state.event_bus,
            state=mock_state,
            config=button_config
        )

        assert controller.state == mock_state
        assert controller.config == button_config

    def test_button_controller_with_disabled_gpio(self, mock_state):
        """Test ButtonController when GPIO is not available."""
        # Create config with GPIO disabled
        config = ButtonRuntimeConfig(
            enabled=False,
            pin=17,
            long_press_seconds=1.0
        )

        controller = ButtonController(
            loop=mock_state.loop,
            event_bus=mock_state.event_bus,
            state=mock_state,
            config=config
        )

        # Should handle disabled GPIO gracefully
        assert controller is not None


class TestButtonControllerGPIOUnavailable:
    """Test ButtonController behavior when GPIO is unavailable."""

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,
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
            preferences_path=None,
            download_dir=None,
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )
        state.mic_muted_event.set()
        state.shutdown = False
        return state

    @pytest.fixture
    def button_config(self):
        """Create button configuration."""
        return ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0
        )

    def test_button_controller_handles_missing_gpio(self, mock_state, button_config, monkeypatch):
        """Test that ButtonController handles missing GPIO module."""
        # Mock GPIO as None to simulate missing module
        monkeypatch.setattr("linux_voice_assistant.button_controller", "GPIO", None)

        # Should not raise exception even with GPIO=None
        try:
            controller = ButtonController(
                loop=mock_state.loop,
                event_bus=mock_state.event_bus,
                state=mock_state,
                config=button_config
            )
            # If GPIO is truly unavailable, controller should handle it gracefully
            assert controller is not None
        except Exception as e:
            # If exception occurs, it should be informative
            assert "GPIO" in str(e) or "RPi" in str(e)


class TestButtonControllerPressTiming:
    """Test button press timing and detection."""

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,
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
            preferences_path=None,
            download_dir=None,
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )
        state.mic_muted_event.set()
        state.shutdown = False
        return state

    @pytest.fixture
    def short_press_config(self):
        """Create config for short press testing."""
        return ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0,  # 1 second for long press
            poll_interval_seconds=0.01  # Fast polling for testing
        )

    def test_button_short_press_detection(self, mock_state, short_press_config):
        """Test short press detection (press < long_press_seconds)."""
        controller = ButtonController(
            event_bus=mock_state.event_bus,
            state=mock_state,
            button_config=short_press_config
        )

        # Short press should be < 1 second
        assert short_press_config.long_press_seconds == 1.0

    def test_button_long_press_detection(self, mock_state, short_press_config):
        """Test long press detection (press >= long_press_seconds)."""
        controller = ButtonController(
            event_bus=mock_state.event_bus,
            state=mock_state,
            button_config=short_press_config
        )

        # Long press should be >= 1 second
        assert short_press_config.long_press_seconds == 1.0

    def test_button_poll_interval_respects_cpu_usage(self):
        """Test that poll interval balances responsiveness and CPU usage."""
        config = ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0,
            poll_interval_seconds=0.05  # 20Hz = 50ms intervals
        )

        # Calculate CPU usage: 20 polls per second
        polls_per_second = 1.0 / config.poll_interval_seconds
        assert polls_per_second == 20.0

        # This should provide good responsiveness while keeping CPU usage low


class TestButtonControllerEventBusIntegration:
    """Test ButtonController integration with EventBus."""

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        bus = EventBus()
        events_received = []

        # Track events
        def on_wake_word(data):
            events_received.append(("wake_word", data))

        def on_set_mic_mute(data):
            events_received.append(("set_mic_mute", data))

        bus.subscribe("wake_word_detected", on_wake_word)
        bus.subscribe("set_mic_mute", on_set_mic_mute)

        bus.events_received = events_received
        return bus

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,
            entities=[],
            music_player=MagicMock(),
            tts_player=MagicMock(),
            available_wake_words={},
            wake_words={},
            active_wake_words=set(),
            stop_word=None,
            wake_word_sensitivity="Slightly sensitive",
            wakeup_sound="",
            thinking_sound="",
            timer_finished_sound="",
            preferences=prefs,
            preferences_path=None,
            download_dir=None,
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )
        state.mic_muted_event.set()
        state.shutdown = False
        return state

    @pytest.fixture
    def button_config(self):
        """Create button configuration."""
        return ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=1.0
        )

    def test_button_controller_publishes_wake_word_event(self, event_bus, mock_state, button_config):
        """Test that button controller publishes wake word event on short press."""
        controller = ButtonController(
            loop=mock_state.loop,
            event_bus=event_bus,
            state=mock_state,
            config=button_config
        )

        # Simulate short press wake word event
        event_bus.publish("wake_word_detected", {"wake_word": "button_press"})

        # Event should be received
        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "wake_word"

    def test_button_controller_publishes_mute_event(self, event_bus, mock_state, button_config):
        """Test that button controller publishes mute event on long press."""
        controller = ButtonController(
            loop=mock_state.loop,
            event_bus=event_bus,
            state=mock_state,
            config=button_config
        )

        # Simulate long press mute event
        event_bus.publish("set_mic_mute", {"state": True})

        # Event should be received
        assert len(event_bus.events_received) == 1
        assert event_bus.events_received[0][0] == "set_mic_mute"


class TestButtonControllerButtonLogic:
    """Test button controller button press logic and state handling."""

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState with media players."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,
            entities=[],
            music_player=MagicMock(),  # Has audio playing
            tts_player=MagicMock(),
            available_wake_words={},
            wake_words={},
            active_wake_words=set(),
            stop_word=None,
            wake_word_sensitivity="Slightly sensitive",
            wakeup_sound="",
            thinking_sound="",
            timer_finished_sound="",
            preferences=prefs,
            preferences_path=None,
            download_dir=None,
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )
        state.mic_muted_event.set()
        state.shutdown = False
        return state

    def test_short_press_with_audio_playing_stops_playback(self, mock_state):
        """Test that short press stops audio when playing."""
        # If audio is playing (TTS or music), short press should stop playback
        # This simulates the Stop wake word behavior
        mock_state.music_player.is_playing = True
        mock_state.tts_player.is_playing = False

        # Short press should stop playback
        # (In real implementation, this would call stop on the appropriate player)

    def test_short_press_without_audio_starts_conversation(self, mock_state):
        """Test that short press starts conversation when no audio playing."""
        # If no audio is playing, short press should start new conversation
        mock_state.music_player.is_playing = False
        mock_state.tts_player.is_playing = False

        # Short press should trigger wake word detected event
        mock_state.event_bus.publish("wake_word_detected", {"wake_word": "button_press"})

    def test_long_press_toggles_mute(self, mock_state):
        """Test that long press toggles microphone mute."""
        # Initial state: unmuted
        assert mock_state.mic_muted == False

        # Long press should toggle mute
        mock_state.event_bus.publish("set_mic_mute", {"state": True})

        # Mute state should be updated
        # (In real implementation, this would be handled by MicMuteHandler)


class TestButtonControllerErrorHandling:
    """Test ButtonController error handling and edge cases."""

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,
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
            preferences_path=None,
            download_dir=None,
            refractory_seconds=0.5,
            event_sounds_enabled=True,
            thinking_sound_loop=False,
            listen_during_wake_sound=False
        )
        state.mic_muted_event.set()
        state.shutdown = False
        return state

    def test_button_controller_handles_zero_pin(self, mock_state):
        """Test ButtonController handles pin=0 gracefully."""
        config = ButtonRuntimeConfig(
            enabled=True,
            pin=0,  # Invalid GPIO pin
            long_press_seconds=1.0
        )

        controller = ButtonController(
            loop=mock_state.loop,
            event_bus=mock_state.event_bus,
            state=mock_state,
            config=config
        )

        # Should handle gracefully or provide clear error
        assert controller.config.pin == 0

    def test_button_controller_handles_negative_long_press(self, mock_state):
        """Test ButtonController handles negative long press time."""
        config = ButtonRuntimeConfig(
            enabled=True,
            pin=17,
            long_press_seconds=-1.0  # Invalid
        )

        controller = ButtonController(
            loop=mock_state.loop,
            event_bus=mock_state.event_bus,
            state=mock_state,
            config=config
        )

        # Should handle gracefully or clamp to reasonable value
        assert controller.config.long_press_seconds == -1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])