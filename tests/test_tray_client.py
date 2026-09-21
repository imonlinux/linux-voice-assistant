"""Tests for the LVA tray client state handling.

The critical regression covered here: on (re)connect the broker replays
every retained per-state light topic. Those light topics are retained-ON
for every state that has EVER been active (a normal turn leaves idle,
listening, thinking and responding all retained-ON), and mosquitto
delivers retained messages in topic order (error < idle < listening <
responding < state < thinking). Deriving the displayed state from them
left the tray stuck on "thinking" while the satellite was idle.

The consolidated lva/<device_id>/state topic is the single source of
truth for the displayed state; light topics are color configuration only.
"""

import json
import logging
import os
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "PyQt5", reason="tray client tests require PyQt5 (pip install .[tray])"
)

from PyQt5 import QtWidgets  # noqa: E402

from linux_voice_assistant.config import AppConfig, Config, MqttConfig  # noqa: E402
from linux_voice_assistant.tray_client.client import LvaTrayClient  # noqa: E402

_LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def qapp():
    """Headless QApplication for tray construction."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def tray(qapp):
    """LvaTrayClient with a mocked MQTT transport (no network I/O)."""
    config = Config(
        app=AppConfig(name="Test Device"),
        mqtt=MqttConfig(
            enabled=True,
            host="localhost",
            port=1883,
            username=None,
            password=None,
        ),
    )

    with patch("linux_voice_assistant.tray_client.client.mqtt.Client") as mock_client:
        mock_client.return_value = MagicMock()
        tray = LvaTrayClient(qapp, config)

    tray._available = True
    return tray


def _message(topic: str, payload: str) -> MagicMock:
    msg = MagicMock()
    msg.topic = topic
    msg.payload = payload.encode("utf-8")
    msg.retain = True
    return msg


class TestTrayVoiceState:
    """Consolidated voice-state topic drives the displayed state."""

    def test_voice_state_topic_sets_state(self, tray):
        tray._on_message(
            None, None, _message(f"{tray._topic_prefix}/state", "thinking")
        )
        assert tray._current_state == "thinking"

        tray._on_message(None, None, _message(f"{tray._topic_prefix}/state", "idle"))
        assert tray._current_state == "idle"

    def test_voice_state_topic_rejects_unknown_payload(self, tray):
        tray._on_message(None, None, _message(f"{tray._topic_prefix}/state", "idle"))
        tray._on_message(None, None, _message(f"{tray._topic_prefix}/state", "banana"))
        assert tray._current_state == "idle"

    def test_voice_state_topic_is_case_insensitive(self, tray):
        tray._on_message(
            None, None, _message(f"{tray._topic_prefix}/state", "LISTENING\n")
        )
        assert tray._current_state == "listening"


class TestRetainedReplayRegression:
    """Broker retained-message replay must not corrupt the displayed state."""

    def test_replay_ending_on_thinking_light_leaves_state_authoritative(self, tray):
        """Full retained replay in mosquitto topic order after a voice turn.

        Alphabetical topic order is: error_light, idle_light, listening,
        responding, state (consolidated), thinking_light. The old logic
        processed "last non-idle ON wins" and landed on thinking; the
        consolidated topic must win regardless of position in the replay.
        """
        prefix = tray._topic_prefix
        replay = [
            (
                f"{prefix}/error_light/state",
                json.dumps(
                    {
                        "state": "ON",
                        "brightness": 255,
                        "color": {"r": 255, "g": 165, "b": 0},
                    }
                ),
            ),
            (
                f"{prefix}/idle_light/state",
                json.dumps(
                    {
                        "state": "ON",
                        "brightness": 255,
                        "color": {"r": 128, "g": 0, "b": 255},
                    }
                ),
            ),
            (
                f"{prefix}/listening_light/state",
                json.dumps(
                    {
                        "state": "ON",
                        "brightness": 255,
                        "color": {"r": 0, "g": 0, "b": 255},
                    }
                ),
            ),
            (
                f"{prefix}/responding_light/state",
                json.dumps(
                    {
                        "state": "ON",
                        "brightness": 255,
                        "color": {"r": 0, "g": 255, "b": 0},
                    }
                ),
            ),
            (f"{prefix}/state", "idle"),
            (
                f"{prefix}/thinking_light/state",
                json.dumps(
                    {
                        "state": "ON",
                        "brightness": 255,
                        "color": {"r": 255, "g": 255, "b": 0},
                    }
                ),
            ),
        ]

        for topic, payload in replay:
            tray._on_message(None, None, _message(topic, payload))

        assert tray._current_state == "idle"

    def test_light_state_messages_never_change_state(self, tray):
        """Light topics configure colors only; they never drive state."""
        tray._on_message(None, None, _message(f"{tray._topic_prefix}/state", "idle"))

        for state_name in ("thinking", "listening", "responding", "error"):
            payload = json.dumps(
                {"state": "ON", "brightness": 255, "color": {"r": 10, "g": 20, "b": 30}}
            )
            tray._on_message(
                None,
                None,
                _message(f"{tray._topic_prefix}/{state_name}_light/state", payload),
            )
            assert tray._current_state == "idle"

    def test_light_state_messages_update_color_cache(self, tray):
        """Light topics still feed the per-state color cache."""
        payload = json.dumps(
            {"state": "ON", "brightness": 128, "color": {"r": 255, "g": 255, "b": 0}}
        )
        tray._on_message(
            None, None, _message(f"{tray._topic_prefix}/thinking_light/state", payload)
        )

        color = tray._last_color_by_state["thinking"]
        # Brightness 128/255 scaling on the pure-yellow source color
        assert (color.red(), color.green(), color.blue()) == (128, 128, 0)
