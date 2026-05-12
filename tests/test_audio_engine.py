"""Tests for Audio Engine integration and wake word processing."""

import pytest
import numpy as np
import threading
import time
from unittest.mock import Mock, MagicMock, patch
import sys

# Mock soundcard before importing audio_engine to avoid PulseAudio connection errors
sys.modules['soundcard'] = MagicMock()

from linux_voice_assistant.audio_engine import AudioEngine, _clamp_0_1
from linux_voice_assistant.models import ServerState, Preferences
from linux_voice_assistant.event_bus import EventBus


class TestClampHelper:
    """Test the _clamp_0_1 helper function."""

    def test_clamp_valid_values(self):
        """Test clamping with valid float values."""
        assert _clamp_0_1("test", 0.5) == 0.5
        assert _clamp_0_1("test", 0.0) == 0.0
        assert _clamp_0_1("test", 1.0) == 1.0

    def test_clamp_below_minimum(self):
        """Test clamping values below 0.0."""
        assert _clamp_0_1("test", -0.5, default=0.5) == 0.0

    def test_clamp_above_maximum(self):
        """Test clamping values above 1.0."""
        assert _clamp_0_1("test", 1.5, default=0.5) == 1.0

    def test_clamp_invalid_string(self):
        """Test clamping with invalid string value."""
        result = _clamp_0_1("test", "invalid", default=0.7)
        assert result == 0.7

    def test_clamp_invalid_type(self):
        """Test clamping with completely invalid type."""
        result = _clamp_0_1("test", None, default=0.3)
        assert result == 0.3


class TestAudioEngineInitialization:
    """Test AudioEngine initialization and setup."""

    @pytest.fixture
    def mock_mic(self):
        """Create mock microphone."""
        mic = MagicMock()
        mic.name = "Test Microphone"
        return mic

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState."""
        prefs = Preferences()
        state = ServerState(
            name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            event_bus=event_bus,
            loop=None,  # AudioEngine doesn't need loop for basic tests
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
        state.mic_muted_event.set()  # Start unmuted
        state.shutdown = False
        return state

    def test_audio_engine_initialization(self, mock_state, mock_mic):
        """Test AudioEngine can be initialized."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280)

        assert engine.state == mock_state
        assert engine.mic == mock_mic
        assert engine.block_size == 1280
        assert engine.oww_threshold == 0.5  # Default threshold
        assert engine._thread is None

    def test_audio_engine_custom_threshold(self, mock_state, mock_mic):
        """Test AudioEngine with custom OWW threshold."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280, oww_threshold=0.7)

        assert engine.oww_threshold == 0.7

    def test_audio_engine_thread_lock_initialization(self, mock_state, mock_mic):
        """Test that wake words lock is properly initialized."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280)

        assert hasattr(engine, '_wake_words_lock')
        # Check that it has lock-like methods
        assert hasattr(engine._wake_words_lock, 'acquire')
        assert hasattr(engine._wake_words_lock, 'release')


class TestAudioEngineLifecycle:
    """Test AudioEngine start/stop lifecycle."""

    @pytest.fixture
    def mock_mic(self):
        """Create mock microphone with recorder."""
        mic = MagicMock()
        mic.name = "Test Microphone"
        mic.recorder.return_value.__enter__ = MagicMock(return_value=mic)
        mic.recorder.return_value.__exit__ = MagicMock(return_value=False)
        return mic

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState for lifecycle testing."""
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

    def test_audio_engine_start(self, mock_state, mock_mic):
        """Test AudioEngine starts processing thread."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280)

        engine.start()

        assert engine._thread is not None
        assert engine._thread.is_alive()
        assert engine._thread.name == "AudioEngineThread"

        # Cleanup
        engine.stop()

    def test_audio_engine_stop(self, mock_state, mock_mic):
        """Test AudioEngine stops processing thread."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280)

        engine.start()
        assert engine._thread.is_alive()

        engine.stop()

        # Thread should be stopped or joining
        assert not engine._thread.is_alive() or engine._thread is None

    def test_audio_engine_lifecycle_with_shutdown_flag(self, mock_state, mock_mic):
        """Test that shutdown flag is properly set during stop."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280)

        engine.start()
        assert mock_state.shutdown == False

        engine.stop()
        assert mock_state.shutdown == True


class TestAudioEngineMuteHandling:
    """Test AudioEngine mute state handling."""

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState for mute testing."""
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
        return state

    @pytest.fixture
    def mock_mic(self):
        """Create mock microphone."""
        mic = MagicMock()
        mic.name = "Test Microphone"
        return mic

    def test_audio_engine_respects_mute_state(self, mock_state, mock_mic):
        """Test that AudioEngine respects mic_muted_event."""
        mock_state.mic_muted_event.clear()  # Start muted
        mock_state.shutdown = False

        engine = AudioEngine(mock_state, mock_mic, block_size=1280)
        engine.start()

        # Give thread time to start and hit mute state
        time.sleep(0.1)

        # Thread should be alive but waiting on mute event
        assert engine._thread.is_alive()

        # Cleanup
        engine.stop()

    def test_audio_engine_unmute_resumes_processing(self, mock_state, mock_mic):
        """Test that unmuting resumes audio processing."""
        mock_state.mic_muted_event.clear()  # Start muted
        mock_state.shutdown = False

        engine = AudioEngine(mock_state, mock_mic, block_size=1280)
        engine.start()

        # Thread should be alive but muted
        assert engine._thread.is_alive()

        # Unmute
        mock_state.mic_muted_event.set()

        # Give thread time to respond
        time.sleep(0.1)

        # Thread should still be alive
        assert engine._thread.is_alive()

        # Cleanup
        engine.stop()


class TestAudioEngineWakeWordProcessing:
    """Test AudioEngine wake word detection and processing."""

    @pytest.fixture
    def mock_state(self, event_bus):
        """Create mock ServerState with wake words."""
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
    def mock_mic(self):
        """Create mock microphone with realistic audio data."""
        mic = MagicMock()
        mic.name = "Test Microphone"

        # Mock audio data - silence
        silence_data = np.zeros(1280, dtype=np.float32)

        mock_recorder = MagicMock()
        mock_recorder.record.return_value = silence_data
        mock_recorder.__enter__ = MagicMock(return_value=mock_recorder)
        mock_recorder.__exit__ = MagicMock(return_value=False)

        mic.recorder.return_value = mock_recorder
        return mic

    def test_audio_engine_handles_empty_wake_words(self, mock_state, mock_mic):
        """Test AudioEngine handles empty wake word list gracefully."""
        # Start with no wake words
        mock_state.wake_words = {}
        mock_state.active_wake_words = set()
        mock_state.wake_words_changed = False

        engine = AudioEngine(mock_state, mock_mic, block_size=1280)
        engine.start()

        # Should not crash with empty wake words
        time.sleep(0.1)
        assert engine._thread.is_alive()

        # Cleanup
        engine.stop()

    def test_audio_engine_thread_safety(self, mock_state, mock_mic):
        """Test that wake word reload is thread-safe."""
        engine = AudioEngine(mock_state, mock_mic, block_size=1280)

        # Verify lock exists
        assert hasattr(engine, '_wake_words_lock')

        # Test that lock can be acquired
        with engine._wake_words_lock:
            # Simulate wake word reload
            mock_state.wake_words_changed = True
            pass

        # No deadlock should occur


class TestAudioEngineErrorHandling:
    """Test AudioEngine error handling and recovery."""

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
    def failing_mic(self):
        """Create mock microphone that fails on record."""
        mic = MagicMock()
        mic.name = "Failing Microphone"
        mic.recorder.side_effect = RuntimeError("Device disconnected")
        return mic

    def test_audio_engine_handles_recording_failure(self, mock_state, failing_mic):
        """Test AudioEngine handles microphone recording failures gracefully."""
        engine = AudioEngine(mock_state, failing_mic, block_size=1280)

        # Should start without throwing exception
        engine.start()

        # Thread should terminate or handle error gracefully
        time.sleep(0.2)

        # Cleanup - may fail if thread already died
        try:
            engine.stop()
        except Exception:
            pass  # Expected if thread already failed

    def test_audio_engine_handles_missing_satellite(self, mock_state):
        """Test AudioEngine handles missing satellite gracefully."""
        # Mock microphone that returns valid data
        mic = MagicMock()
        mic.name = "Test Microphone"

        silence_data = np.zeros(1280, dtype=np.float32)
        mock_recorder = MagicMock()
        mock_recorder.record.return_value = silence_data
        mock_recorder.__enter__ = MagicMock(return_value=mock_recorder)
        mock_recorder.__exit__ = MagicMock(return_value=False)
        mic.recorder.return_value = mock_recorder

        # No satellite set
        mock_state.satellite = None

        engine = AudioEngine(mock_state, mic, block_size=1280)
        engine.start()

        # Should not crash when satellite is None
        time.sleep(0.1)

        # Cleanup
        engine.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])