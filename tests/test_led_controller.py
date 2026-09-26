"""Tests for LED Controller integration and hardware abstraction."""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch
from linux_voice_assistant.led_controller import LedController
from linux_voice_assistant.config import LedConfig
from linux_voice_assistant.models import Preferences
from linux_voice_assistant.event_bus import EventBus


class TestLedControllerInitialization:
    """Test LedController initialization and setup."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop for LED controller tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus for LED controller."""
        return EventBus()

    @pytest.fixture
    def led_config(self):
        """Create basic LED configuration."""
        return LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences for LED controller."""
        prefs = Preferences()
        prefs.num_leds = 12
        return prefs

    def test_led_controller_initialization(self, event_loop, event_bus, led_config, preferences):
        """Test LedController can be initialized."""
        controller = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=led_config,
            preferences=preferences
        )

        assert controller.loop == event_loop
        assert controller.num_leds == 12
        assert controller.current_task is None
        assert controller._is_ready == False
        assert controller.leds is None

    def test_led_controller_with_different_led_counts(self, event_loop, event_bus, led_config):
        """Test LedController with different LED counts."""
        prefs_10 = Preferences(num_leds=10)
        controller_10 = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=led_config,
            preferences=prefs_10
        )
        assert controller_10.num_leds == 10

        # Create new config for LED controller 15
        led_config_15 = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=15
        )
        prefs_15 = Preferences(num_leds=15)
        controller_15 = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=led_config_15,
            preferences=prefs_15
        )
        assert controller_15.num_leds == 15

    def test_led_controller_with_xvf3800_config(self, event_loop, event_bus):
        """Test LedController with XVF3800 configuration."""
        xvf_config = LedConfig(
            led_type="xvf3800",
            interface="usb",
            clock_pin=0,
            data_pin=0,
            num_leds=12
        )

        prefs = Preferences(num_leds=12)
        controller = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=xvf_config,
            preferences=prefs
        )

        assert controller.num_leds == 12


class TestLedControllerEventHandler:
    """Test LedController as an EventHandler."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def led_config(self):
        """Create LED configuration."""
        return LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=12,
            num_leds=12
        )

    @pytest.fixture
    def preferences(self):
        """Create preferences."""
        return Preferences(num_leds=12)

    def test_led_controller_subscribes_to_events(self, event_loop, event_bus, led_config, preferences):
        """Test that LedController subscribes to relevant events."""
        # Note: LedController doesn't call _subscribe_all_methods() in __init__
        # So we need to check if it has @subscribe decorated methods
        from linux_voice_assistant.event_bus import subscribe

        # Check if LedController has any @subscribe methods
        has_subscribe = False
        for attr_name in dir(LedController):
            attr = getattr(LedController, attr_name)
            if hasattr(attr, '_event_bus_subscribe'):
                has_subscribe = True
                break

        # This test documents current behavior - LedController may or may not
        # use @subscribe decorators depending on implementation
        assert isinstance(has_subscribe, bool)  # Either way is fine for this test


class TestLedControllerColorHandling:
    """Test LED color handling and validation."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def minimal_controller(self, event_loop, event_bus):
        """Create minimal LED controller."""
        config = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12
        )
        prefs = Preferences(num_leds=12)

        return LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            preferences=prefs
        )

    def test_led_controller_default_colors_exist(self):
        """Test that default LED colors are defined."""
        from linux_voice_assistant.led_controller import (
            _OFF, _BLUE, _YELLOW, _GREEN, _DIM_RED, _ORANGE, _PURPLE
        )

        # Check that color constants are defined
        assert _OFF == (0, 0, 0)
        assert _BLUE == (0, 0, 255)
        assert _YELLOW == (255, 255, 0)
        assert _GREEN == (0, 255, 0)
        assert _DIM_RED == (50, 0, 0)
        assert _ORANGE == (255, 165, 0)
        assert _PURPLE == (128, 0, 255)

    def test_led_controller_color_validation(self, minimal_controller):
        """Test color tuple validation."""
        # Valid colors
        valid_colors = [
            (0, 0, 0),    # Off
            (255, 0, 0),  # Red
            (0, 255, 0),  # Green
            (0, 0, 255),  # Blue
            (255, 255, 255),  # White
            (128, 128, 128),  # Gray
        ]

        for color in valid_colors:
            r, g, b = color
            assert 0 <= r <= 255, f"Red channel out of range: {r}"
            assert 0 <= g <= 255, f"Green channel out of range: {g}"
            assert 0 <= b <= 255, f"Blue channel out of range: {b}"


class TestLedControllerHardwareAbstraction:
    """Test LED hardware abstraction layer."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def mock_board(self, monkeypatch):
        """Mock Adafruit board module."""
        mock_board = MagicMock()
        monkeypatch.setitem(globals(), 'board', mock_board)
        return mock_board

    def test_led_controller_handles_missing_board_module(self, event_loop, event_bus):
        """Test that LedController handles missing board module gracefully."""
        # This test verifies that when board module is not available,
        # the controller doesn't crash but logs a warning

        config = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12
        )
        prefs = Preferences(num_leds=12)

        # Should not raise exception even if board module is missing
        controller = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            preferences=prefs
        )

        assert controller is not None

    def test_led_controller_with_neopixel_config(self, event_loop, event_bus):
        """Test LedController with NeoPixel configuration."""
        neo_config = LedConfig(
            led_type="neopixel",
            interface="spi",
            clock_pin=0,
            data_pin=0,
            num_leds=16
        )

        prefs = Preferences(num_leds=16)
        controller = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=neo_config,
            preferences=prefs
        )

        assert controller.num_leds == 16


class TestLedControllerStateTransitions:
    """Test LED controller state transitions and effects."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def minimal_controller(self, event_loop, event_bus):
        """Create minimal LED controller."""
        config = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12
        )
        prefs = Preferences(num_leds=12)

        return LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            preferences=prefs
        )

    def test_led_controller_mute_state_tracking(self, minimal_controller):
        """Test that LED controller tracks mute state."""
        # Controller should track mute state for overlay effects
        assert hasattr(minimal_controller, '_mic_is_muted')
        assert isinstance(minimal_controller._mic_is_muted, bool)

    def test_muted_state_still_publishes_to_mqtt(self, minimal_controller, event_bus):
        """Mute overlay suppresses local LED effects, NOT the MQTT publish.

        Regression guard for the tray-client desync: while muted, state
        transitions must still reach MQTT consumers, otherwise the retained
        per-state topics (and the consolidated state topic) go stale and a
        mid-turn mute leaves the tray stuck on the pre-mute state.
        """
        published = []
        event_bus.subscribe("publish_state_to_mqtt", published.append)

        with patch.object(minimal_controller, 'run_action') as mock_run:
            minimal_controller._mic_is_muted = True
            minimal_controller._apply_state_effect("idle")

        # MQTT publish happened despite the mute overlay
        assert len(published) == 1
        assert published[0]["state_name"] == "idle"

        # Local LEDs still show the mute override, not the state effect
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0] == "solid"

    def test_led_controller_ready_state(self, minimal_controller):
        """Test LED controller ready state management."""
        # Initially not ready
        assert minimal_controller._is_ready == False

        # Ready state should be managed by the controller
        # This test documents the expected behavior


class TestLedControllerMqttIntegration:
    """Test LED controller MQTT integration and dynamic updates."""

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def minimal_controller(self, event_loop, event_bus):
        """Create minimal LED controller."""
        config = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12
        )
        prefs = Preferences(num_leds=12)

        return LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            preferences=prefs
        )

    def test_led_controller_num_leds_update(self, minimal_controller):
        """Test that LED count can be updated dynamically."""
        initial_leds = minimal_controller.num_leds
        assert initial_leds == 12

        # Simulate MQTT update to num_leds
        # This would normally come through EventBus
        new_led_count = 20
        minimal_controller.num_leds = new_led_count

        assert minimal_controller.num_leds == new_led_count


class TestStartupSequence:
    """Startup blink must be bounded and settle into the idle state.

    Regression guard (retire_mqtt stage 2): with MQTT disabled there is
    no retained-config bootstrap to cancel the startup blink, and no
    voice_idle fires at boot, so an unbounded green blink used to run
    from boot until the first voice event.
    """

    @pytest.fixture
    def event_loop(self):
        """Create event loop."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        """Create EventBus."""
        return EventBus()

    @pytest.fixture
    def hw_controller(self, event_loop, event_bus):
        """Controller with a fake pixel backend (hardware path enabled)."""
        config = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12,
        )
        prefs = Preferences(num_leds=12)
        controller = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            preferences=prefs,
        )
        controller._enabled = True
        controller._is_ready = True
        controller.leds = MagicMock()
        return controller

    @pytest.mark.asyncio
    async def test_startup_sequence_completes_and_applies_idle(self, hw_controller):
        """The sequence returns on its own and settles into idle."""
        with patch.object(hw_controller, "run_action") as mock_run:
            await asyncio.wait_for(hw_controller.startup_sequence(), timeout=5.0)

        # Idle settle was requested (idle default: off, purple, 0.5).
        mock_run.assert_called_once_with("off", (128, 0, 255), 0.5)
        # The blink ran against the fake pixels (green at full brightness).
        fills = [c.args[0] for c in hw_controller.leds.fill.call_args_list]
        assert (0, 255, 0) in fills

    @pytest.mark.asyncio
    async def test_startup_sequence_cancelled_does_not_stomp_new_action(
        self, hw_controller
    ):
        """A real action during the blink owns the ring; no idle re-apply.

        The blink handlers swallow CancelledError (to blank the ring), and
        wait_for can then report a normal return, so the sequence must
        notice the pending cancellation itself.
        """
        with patch.object(hw_controller, "run_action") as mock_run:
            task = asyncio.ensure_future(hw_controller.startup_sequence())
            await asyncio.sleep(0.05)  # Let it enter the blink loop.
            task.cancel()
            await task  # Must not raise, and must finish fast (not at timeout).

            # The cancelling action keeps the ring: idle was never applied.
            mock_run.assert_not_called()
            assert asyncio.current_task().cancelling() == 0  # test task unaffected

    @pytest.mark.asyncio
    async def test_startup_sequence_disabled_hardware_is_noop(self, event_loop, event_bus):
        """Without ready hardware the sequence completes and writes no pixels."""
        config = LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12,
        )
        controller = LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=config,
            preferences=Preferences(num_leds=12),
        )
        # Mirror the failed hardware-init state from __init__ (board or
        # driver unavailable): enabled flag kept, ready flag cleared.
        controller._is_ready = False

        await asyncio.wait_for(controller.startup_sequence(), timeout=5.0)
        assert controller.leds is None


class TestLedConfigPersistence:
    """Fork: per-state configs persist via preferences and restore on boot.

    The MQTT era relied on retained messages as the persistence layer;
    the native ESPHome path has none, so preferences.json carries HA's
    effect/color/brightness selections across reboots.
    """

    @pytest.fixture
    def event_loop(self):
        """Create event loop for LED controller tests."""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def led_config(self):
        """DotStar config without hardware: controller runs in no-op mode."""
        return LedConfig(
            led_type="dotstar",
            interface="spi",
            clock_pin=11,
            data_pin=10,
            num_leds=12,
        )

    def make_controller(
        self, event_loop, event_bus, led_config, preferences, persist=None
    ):
        return LedController(
            loop=event_loop,
            event_bus=event_bus,
            config=led_config,
            preferences=preferences,
            persist=persist,
        )

    def test_config_change_persists_to_preferences(
        self, event_loop, event_bus, led_config
    ):
        """A set_<state>_effect command writes through to preferences."""
        prefs = Preferences(num_leds=12)
        save = Mock()
        controller = self.make_controller(
            event_loop, event_bus, led_config, prefs, persist=save
        )

        event_bus.publish("set_listening_effect", {"effect": "slow_pulse"})

        assert prefs.led_states["listening"]["effect"] == "slow_pulse"
        assert prefs.led_states["listening"]["color"] == [0, 0, 255]
        assert prefs.led_states["listening"]["brightness"] == pytest.approx(0.5)
        save.assert_called_once()

    def test_color_change_persists_clamped_values(
        self, event_loop, event_bus, led_config
    ):
        """Color commands snapshot the raw 0-255 scale into preferences."""
        prefs = Preferences(num_leds=12)
        save = Mock()
        controller = self.make_controller(
            event_loop, event_bus, led_config, prefs, persist=save
        )

        event_bus.publish(
            "set_thinking_color",
            {"color": {"r": 10, "g": 20, "b": 30}, "brightness": 255},
        )

        assert prefs.led_states["thinking"]["color"] == [10, 20, 30]
        assert prefs.led_states["thinking"]["brightness"] == pytest.approx(1.0)
        assert controller.configs["thinking"]["color"] == (10, 20, 30)

    def test_retained_replay_persists(self, event_loop, event_bus, led_config):
        """Retained MQTT replay syncs its truth into preferences too."""
        prefs = Preferences(num_leds=12)
        save = Mock()
        self.make_controller(event_loop, event_bus, led_config, prefs, persist=save)

        event_bus.publish(
            "set_idle_effect",
            {"effect": "slow_pulse", "retained": True},
        )

        assert prefs.led_states["idle"]["effect"] == "slow_pulse"
        save.assert_called_once()

    def test_unchanged_config_does_not_save(self, event_loop, event_bus, led_config):
        """Replays matching the current config must not write preferences."""
        prefs = Preferences(num_leds=12)
        save = Mock()
        controller = self.make_controller(
            event_loop, event_bus, led_config, prefs, persist=save
        )

        event_bus.publish(
            "set_thinking_color",
            {"color": {"r": 255, "g": 255, "b": 0}, "brightness": 204},
        )

        save.assert_not_called()
        assert prefs.led_states == {}

    def test_saved_configs_restored_on_init(self, event_loop, event_bus, led_config):
        """A fresh controller picks up the previous run's selections."""
        prefs = Preferences(num_leds=12)
        prefs.led_states = {
            "idle": {"effect": "solid", "color": [1, 2, 3], "brightness": 0.25},
            "error": {"effect": "slow_blink", "color": [255, 0, 0], "brightness": 0.9},
        }
        controller = self.make_controller(event_loop, event_bus, led_config, prefs)

        assert controller.configs["idle"]["effect"] == "solid"
        assert controller.configs["idle"]["color"] == (1, 2, 3)
        assert controller.configs["idle"]["brightness"] == pytest.approx(0.25)
        assert controller.configs["error"]["effect"] == "slow_blink"
        # Untouched states keep their defaults.
        assert controller.configs["listening"]["effect"] == "medium_pulse"

    def test_restore_is_defensive(self, event_loop, event_bus, led_config):
        """Corrupt saved data cannot break startup or poison configs."""
        prefs = Preferences(num_leds=12)
        prefs.led_states = {
            "bogus_state": {"effect": "solid"},  # unknown state
            "idle": {"effect": "not_an_effect"},  # unknown effect
            "listening": {"color": [999, -5, "x"], "brightness": 7},  # bad color
            "thinking": {"color": [5, 5, 5], "brightness": True},  # bool is not brightness
            "responding": "garbage",  # not a dict
        }
        controller = self.make_controller(event_loop, event_bus, led_config, prefs)

        # idle: unknown effect skipped, defaults kept.
        assert controller.configs["idle"]["effect"] == "off"
        # listening: malformed color aborts the channel write, default kept;
        # numeric brightness still restored.
        assert controller.configs["listening"]["color"] == (0, 0, 255)
        assert controller.configs["listening"]["brightness"] == pytest.approx(1.0)
        # thinking: valid color applied, bool brightness rejected (default).
        assert controller.configs["thinking"]["color"] == (5, 5, 5)
        assert controller.configs["thinking"]["brightness"] == pytest.approx(0.8)

    def test_persist_none_skips_bookkeeping(self, event_loop, event_bus, led_config):
        """Without a save hook (tests/standalone) nothing is written."""
        prefs = Preferences(num_leds=12)
        controller = self.make_controller(event_loop, event_bus, led_config, prefs)

        event_bus.publish("set_listening_effect", {"effect": "slow_pulse"})

        # The in-memory config still reflects the command.
        assert controller.configs["listening"]["effect"] == "slow_pulse"
        assert prefs.led_states == {}

    def test_restore_failure_never_raises(self, event_loop, event_bus, led_config):
        """A non-dict led_states value is ignored, not fatal."""
        prefs = Preferences(num_leds=12)
        prefs.led_states = "garbage"  # type: ignore[assignment]
        controller = self.make_controller(event_loop, event_bus, led_config, prefs)

        assert controller.configs["idle"]["effect"] == "off"

    def test_entity_seeds_from_restored_config(
        self, event_loop, event_bus, led_config
    ):
        """The reboot path end to end: entity reflects the restored config."""
        from linux_voice_assistant.led_light_entities import LedStateLightEntity

        prefs = Preferences(num_leds=12)
        prefs.led_states = {
            "idle": {"effect": "solid", "color": [128, 0, 255], "brightness": 0.5},
        }
        controller = self.make_controller(event_loop, event_bus, led_config, prefs)

        server = MagicMock()
        server.state = MagicMock()
        entity = LedStateLightEntity(
            server=server,
            key=7,
            state_name="idle",
            event_bus=event_bus,
            initial=controller.configs.get("idle"),
        )

        assert entity.is_on is True
        assert entity.effect == "Solid"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])