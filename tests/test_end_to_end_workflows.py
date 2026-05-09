"""End-to-end / integration workflow tests for linux-voice-assistant.

These tests wire together two or more real components and verify their
contract — for example, "an MQTT 'mute set' command turns into a state
change on ServerState and a re-published 'mute state' MQTT message".

Scope and design notes
----------------------
The previous version of this file tried to test "complete user workflows"
end-to-end including the audio capture thread, the LED controller, and the
Sendspin WebSocket client. That ended up wedged between two stools:

* It wasn't a unit test, because it instantiated half a dozen real objects.
* It wasn't a real integration test either, because the components it most
  needed (the satellite, the audio engine, the LED hardware) had to be
  mocked away to even reach the assertions.

The honest replacement is the small set of tests below. They:

* use a **real** ``EventBus`` (with ``track_events=True`` so we can introspect)
* use a **real** ``ServerState``
* mock paho's ``mqtt.Client`` at the import site, which is the only external
  dependency that actually has to be faked
* re-implement the ``MicMuteHandler`` contract inline as ``_TestMicMuteHandler``

The audio engine / LED / Sendspin / wake-word workflows that used to live
here are already covered by their own unit tests
(``test_audio_engine.py``, ``test_led_controller.py``,
``test_sendspin_client.py`` etc.). Asserting them again here doesn't add
coverage, it just adds a broken duplicate.

TODO: Once ``MicMuteHandler`` is extracted from ``linux_voice_assistant/
__main__.py`` into its own module, replace ``_TestMicMuteHandler`` below
with a direct import so we test the real handler.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from linux_voice_assistant.config import MqttConfig
from linux_voice_assistant.event_bus import EventBus, EventHandler, subscribe
from linux_voice_assistant.models import Preferences, ServerState
from linux_voice_assistant.mqtt_controller import MqttController


# ---------------------------------------------------------------------------
# Test-local re-implementation of MicMuteHandler
# ---------------------------------------------------------------------------
#
# The real MicMuteHandler lives in linux_voice_assistant/__main__.py. Importing
# it from there pulls in soundcard / pymicro_wakeword / pyopen_wakeword at
# module load, which is a heavy dependency for a unit test. Restating the
# contract here keeps the test self-contained. If the production handler's
# behaviour changes, this re-statement must be updated to match (or — better —
# the production handler should be extracted into its own module so tests can
# import it directly).

class _TestMicMuteHandler(EventHandler):
    """Minimal stand-in for the production MicMuteHandler.

    Subscribes to ``set_mic_mute`` and:
      * updates ``state.mic_muted``
      * sets/clears ``state.mic_muted_event``
      * forwards to ``mqtt_controller.publish_mute_state`` if provided
      * re-publishes ``mic_muted`` / ``mic_unmuted`` events on the bus
    """

    def __init__(
        self,
        event_bus: EventBus,
        state: ServerState,
        mqtt_controller: Optional[MqttController] = None,
    ):
        super().__init__(event_bus)
        self.state = state
        self.mqtt_controller = mqtt_controller
        self._subscribe_all_methods()

    @subscribe
    def set_mic_mute(self, data: dict):
        is_muted = bool(data.get("state", False))
        if self.state.mic_muted == is_muted:
            return

        self.state.mic_muted = is_muted
        if is_muted:
            self.state.mic_muted_event.clear()
        else:
            self.state.mic_muted_event.set()

        if self.mqtt_controller is not None:
            self.mqtt_controller.publish_mute_state(is_muted)

        self.event_bus.publish(
            "mic_muted" if is_muted else "mic_unmuted",
            {},
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def real_event_loop():
    """An asyncio loop scoped to a single test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tracked_event_bus():
    """Real EventBus with event tracking enabled for assertions."""
    return EventBus(track_events=True)


@pytest.fixture
def server_state(real_event_loop, tracked_event_bus, tmp_path):
    """A real ServerState wired to the test's event_bus + loop.

    All hardware/model fields are left empty/None — this state is intended
    for tests that exercise control-plane events, not audio or wake-word
    processing.
    """
    prefs = Preferences()
    state = ServerState(
        name="test_device",
        mac_address="aa:bb:cc:dd:ee:ff",
        event_bus=tracked_event_bus,
        loop=real_event_loop,
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
        preferences_path=tmp_path / "preferences.json",
        download_dir=tmp_path / "downloads",
        refractory_seconds=0.5,
        event_sounds_enabled=True,
        thinking_sound_loop=False,
        listen_during_wake_sound=False,
    )
    state.mic_muted_event.set()  # Start unmuted
    state.shutdown = False
    return state


@pytest.fixture
def mqtt_config():
    return MqttConfig(host="localhost", port=1883, username=None, password=None)


@pytest.fixture
def mqtt_controller_with_mocked_client(real_event_loop, tracked_event_bus, mqtt_config, server_state):
    """An MqttController whose paho client is mocked.

    Yields ``(controller, mock_client)`` so tests can assert against publish/
    subscribe calls.
    """
    with patch("linux_voice_assistant.mqtt_controller.mqtt.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        controller = MqttController(
            loop=real_event_loop,
            event_bus=tracked_event_bus,
            config=mqtt_config,
            app_name="test_device",
            mac_address="aa:bb:cc:dd:ee:ff",
            preferences=server_state.preferences,
        )
        yield controller, mock_client


# ---------------------------------------------------------------------------
# MQTT integration
# ---------------------------------------------------------------------------

class TestMqttIntegrationWorkflow:
    """Verify MqttController <-> EventBus round-trips."""

    def test_on_connect_subscribes_and_publishes_discovery(
        self, mqtt_controller_with_mocked_client
    ):
        """Connecting should subscribe to the command topic + publish discovery."""
        controller, mock_client = mqtt_controller_with_mocked_client

        # Simulate a successful connect callback (rc=0).
        controller._on_connect(mock_client, None, {}, 0)

        # The controller must subscribe to the command-topic wildcard.
        sub_calls = [c.args[0] for c in mock_client.subscribe.call_args_list]
        assert any(
            arg.endswith("/+/set") for arg in sub_calls
        ), f"Expected a subscribe to '<prefix>/+/set'; got {sub_calls!r}"

        # And it should have published at least one discovery config.
        publish_topics = [c.args[0] for c in mock_client.publish.call_args_list]
        assert any(
            t.startswith("homeassistant/") for t in publish_topics
        ), f"Expected a homeassistant/* discovery publish; got {publish_topics!r}"

        # Internal flag should now report connected.
        assert controller._connected is True

    def test_mqtt_mute_command_publishes_set_mic_mute_event(
        self, mqtt_controller_with_mocked_client, tracked_event_bus
    ):
        """A 'mute/set' MQTT message should fan out as a set_mic_mute event."""
        controller, _mock_client = mqtt_controller_with_mocked_client

        # Skip bootstrap so a non-retained command is honoured.
        controller._bootstrap_state_sync = False

        topic = controller.topics["mute"]["command"]
        controller._handle_message_on_loop(topic, "ON", retained=False)

        published_topics = [t for t, _ in tracked_event_bus.events_received]
        assert "set_mic_mute" in published_topics

        # And the payload should carry state=True.
        for t, data in tracked_event_bus.events_received:
            if t == "set_mic_mute":
                assert data.get("state") is True
                break


class TestMqttConnectionRecoveryWorkflow:
    """Verify the disconnect -> reconnect path keeps internal state sane."""

    def test_disconnect_then_reconnect_resets_connected_flag(
        self, mqtt_controller_with_mocked_client
    ):
        controller, mock_client = mqtt_controller_with_mocked_client

        # Initial connect.
        controller._on_connect(mock_client, None, {}, 0)
        assert controller._connected is True

        # Simulate a disconnect.
        controller._on_disconnect(mock_client, None, 0)

        # Reconnect.
        controller._on_connect(mock_client, None, {}, 0)
        assert controller._connected is True
        # Bootstrap should re-arm on every fresh connect.
        assert controller._bootstrap_state_sync is True


# ---------------------------------------------------------------------------
# Mute round-trip: software command -> state -> MQTT re-publish
# ---------------------------------------------------------------------------

class TestMuteToggleWorkflow:
    """Verify that publishing set_mic_mute drives state and MQTT correctly."""

    def test_set_mic_mute_updates_state_and_publishes_mqtt(
        self, mqtt_controller_with_mocked_client, server_state, tracked_event_bus
    ):
        """set_mic_mute -> state.mic_muted, mic_muted_event, mute MQTT publish."""
        controller, mock_client = mqtt_controller_with_mocked_client
        _TestMicMuteHandler(tracked_event_bus, server_state, controller)

        # Pre-state.
        assert server_state.mic_muted is False
        assert server_state.mic_muted_event.is_set()

        # Action: HA / button / whoever publishes set_mic_mute.
        tracked_event_bus.publish("set_mic_mute", {"state": True})

        # State must have flipped.
        assert server_state.mic_muted is True
        assert not server_state.mic_muted_event.is_set()

        # And the MQTT side should have published the new mute state on the
        # state topic. We don't assert on the call_args_list count because
        # the controller may also have published other things during init.
        mute_state_topic = controller.topics["mute"]["state"]
        mute_publishes = [
            c for c in mock_client.publish.call_args_list
            if c.args[0] == mute_state_topic
        ]
        assert mute_publishes, (
            f"Expected publish to {mute_state_topic}; "
            f"saw {[c.args[0] for c in mock_client.publish.call_args_list]}"
        )
        # The most recent publish on that topic should reflect 'ON'.
        assert mute_publishes[-1].args[1] == "ON"

        # And the bus should have seen the secondary mic_muted event.
        secondary = [t for t, _ in tracked_event_bus.events_received if t == "mic_muted"]
        assert secondary, "Expected mic_muted to be re-published after set_mic_mute"

    def test_set_mic_mute_idempotent_when_already_muted(
        self, mqtt_controller_with_mocked_client, server_state, tracked_event_bus
    ):
        """Re-asserting the current state should not produce duplicate work."""
        controller, mock_client = mqtt_controller_with_mocked_client
        _TestMicMuteHandler(tracked_event_bus, server_state, controller)

        # Move to muted, then clear the mock so we only see follow-up calls.
        tracked_event_bus.publish("set_mic_mute", {"state": True})
        mock_client.publish.reset_mock()
        tracked_event_bus.clear_events()

        # Re-publish the same state.
        tracked_event_bus.publish("set_mic_mute", {"state": True})

        # No new publish on the mute state topic.
        mute_state_topic = controller.topics["mute"]["state"]
        assert not [
            c for c in mock_client.publish.call_args_list
            if c.args[0] == mute_state_topic
        ]
        # And no secondary mic_muted event either (only the original
        # set_mic_mute we just published is in the bus history).
        secondary = [t for t, _ in tracked_event_bus.events_received if t == "mic_muted"]
        assert not secondary


# ---------------------------------------------------------------------------
# HA -> MQTT -> EventBus -> state, end-to-end
# ---------------------------------------------------------------------------

class TestHomeAssistantMuteAutomationWorkflow:
    """Full path: HA-style MQTT command in, ServerState change out."""

    def test_ha_mqtt_mute_command_drives_state(
        self, mqtt_controller_with_mocked_client, server_state, tracked_event_bus
    ):
        controller, mock_client = mqtt_controller_with_mocked_client
        _TestMicMuteHandler(tracked_event_bus, server_state, controller)

        # Simulate the controller being post-bootstrap and an HA command arriving.
        controller._on_connect(mock_client, None, {}, 0)
        controller._bootstrap_state_sync = False

        topic = controller.topics["mute"]["command"]
        controller._handle_message_on_loop(topic, "ON", retained=False)

        # State updated.
        assert server_state.mic_muted is True
        assert not server_state.mic_muted_event.is_set()

        # And we published the new state back out to MQTT.
        mute_state_topic = controller.topics["mute"]["state"]
        latest_state_publish = next(
            (c for c in reversed(mock_client.publish.call_args_list)
             if c.args[0] == mute_state_topic),
            None,
        )
        assert latest_state_publish is not None
        assert latest_state_publish.args[1] == "ON"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
