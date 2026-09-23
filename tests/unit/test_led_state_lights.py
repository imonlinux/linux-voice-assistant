"""Unit tests for the native per-state LED light entities.

retire_mqtt groundwork: LedStateLightEntity translates HA light commands
into the EventBus topics LedController consumes, mirroring the MQTT
handler's translation, and syncs device-driven state changes back to HA.
"""

from unittest.mock import MagicMock

from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    LightCommandRequest,
    ListEntitiesRequest,
    SubscribeHomeAssistantStatesRequest,
)

from linux_voice_assistant.event_bus import EventBus
from linux_voice_assistant.led_light_entities import (
    LED_EFFECTS,
    LED_STATES,
    LedStateLightEntity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IDLE_CONFIG = {
    "effect": "solid",
    "color": (128, 0, 255),
    "brightness": 0.5,
}


def make_server():
    server = MagicMock()
    server.state = MagicMock()
    return server


_UNSET = object()


def make_light_command(key, **fields):
    """Build a LightCommandRequest with explicit presence flags.

    aioesphomeapi does not mark has_* presence from constructor kwargs
    (real HA traffic carries them through wire decoding), so tests set
    the flags the way the wire would.
    """
    msg = LightCommandRequest(key=key)
    for name, value in fields.items():
        setattr(msg, name, value)
    return msg


def make_entity(state_name="idle", server=None, bus=None, initial=_UNSET, key=7):
    server = server or make_server()
    # track_events so tests can assert the exact published payloads.
    bus = bus or EventBus(track_events=True)
    entity = LedStateLightEntity(
        server=server,
        key=key,
        state_name=state_name,
        event_bus=bus,
        initial=dict(_IDLE_CONFIG) if initial is _UNSET else initial,
    )
    return entity, server, bus


def events_for(bus, topic):
    return [data for t, data in bus.events_received if t == topic]


# ---------------------------------------------------------------------------
# Construction / discovery
# ---------------------------------------------------------------------------


class TestLedStateLightEntityInit:
    def test_seeds_state_from_controller_config(self):
        entity, _, _ = make_entity(initial=_IDLE_CONFIG)
        assert entity.object_id == "led_idle"
        assert entity.name == "LED Idle"
        assert entity.is_on is True
        assert entity.effect == "Solid"
        assert entity.red == 128 / 255.0
        assert entity.green == 0.0
        assert entity.blue == 255 / 255.0
        assert entity.brightness == 0.5

    def test_fallback_initial_without_controller_config(self):
        entity, _, _ = make_entity(initial=None)
        assert entity.is_on is False
        assert entity.effect == "Off"

    def test_off_effect_seeds_last_effect_to_solid(self):
        entity, _, _ = make_entity(
            initial={"effect": "off", "color": (0, 0, 0), "brightness": 0.5}
        )
        # Turning ON later must restore something sensible, not "Off".
        assert entity._last_effect_display == "Solid"

    def test_states_and_effects_of_record(self):
        assert LED_STATES == (
            "idle",
            "listening",
            "thinking",
            "responding",
            "error",
        )
        assert LED_EFFECTS[0] == "Off"
        assert "Spin" in LED_EFFECTS

    def test_list_entities_advertises_effects(self):
        entity, _, _ = make_entity()
        responses = list(entity.handle_message(ListEntitiesRequest()))
        assert len(responses) == 1
        response = responses[0]
        assert response.object_id == "led_idle"
        assert list(response.effects) == list(LED_EFFECTS)


# ---------------------------------------------------------------------------
# HA -> device translation
# ---------------------------------------------------------------------------


class TestCommandTranslation:
    def test_effect_command_normalizes_to_event_bus_id(self):
        entity, _, bus = make_entity(state_name="thinking")
        list(
            entity.handle_message(
                make_light_command(7, effect="Slow Pulse", has_effect=True)
            )
        )
        assert events_for(bus, "set_thinking_effect") == [{"effect": "slow_pulse"}]
        assert events_for(bus, "set_thinking_color") == []

    def test_turn_off_publishes_off_effect(self):
        entity, _, bus = make_entity()
        list(entity.handle_message(make_light_command(7, state=False, has_state=True)))
        assert events_for(bus, "set_idle_effect") == [{"effect": "off"}]

    def test_turn_on_restores_last_effect(self):
        entity, _, bus = make_entity(
            initial={"effect": "spin", "color": (255, 255, 0), "brightness": 0.8}
        )
        list(entity.handle_message(make_light_command(7, state=False, has_state=True)))
        list(entity.handle_message(make_light_command(7, state=True, has_state=True)))
        effects = events_for(bus, "set_idle_effect")
        assert effects == [{"effect": "off"}, {"effect": "spin"}]

    def test_turn_on_without_prior_effect_falls_back_to_solid(self):
        entity, _, bus = make_entity(
            initial={"effect": "off", "color": (0, 0, 0), "brightness": 0.5}
        )
        list(entity.handle_message(make_light_command(7, state=True, has_state=True)))
        assert events_for(bus, "set_idle_effect") == [{"effect": "solid"}]

    def test_explicit_off_effect_publishes_off(self):
        entity, _, bus = make_entity()
        list(entity.handle_message(make_light_command(7, effect="Off", has_effect=True)))
        assert events_for(bus, "set_idle_effect") == [{"effect": "off"}]

    def test_rgb_and_brightness_scale_to_wire_format(self):
        entity, _, bus = make_entity()
        list(
            entity.handle_message(
                make_light_command(
                    7,
                    red=1.0,
                    green=0.5,
                    blue=0.0,
                    has_rgb=True,
                    brightness=0.2,
                    has_brightness=True,
                )
            )
        )
        assert events_for(bus, "set_idle_color") == [
            {
                "color": {"r": 255, "g": 128, "b": 0},
                "brightness": 51,
            }
        ]

    def test_combined_state_and_effect_command(self):
        entity, _, bus = make_entity(state_name="error")
        list(
            entity.handle_message(
                make_light_command(
                    7, state=True, has_state=True, effect="Fast Blink", has_effect=True
                )
            )
        )
        # An explicit effect supersedes the ON restore path: exactly one
        # effect publish, no redundant restore.
        assert events_for(bus, "set_error_effect") == [{"effect": "fast_blink"}]

    def test_unknown_key_ignored(self):
        entity, _, bus = make_entity()
        list(
            entity.handle_message(make_light_command(99, state=False, has_state=True))
        )
        assert events_for(bus, "set_idle_effect") == []

    def test_command_yields_state_response_and_updates_mirror(self):
        entity, _, _ = make_entity()
        responses = list(
            entity.handle_message(
                make_light_command(7, state=False, has_state=True, effect="Off", has_effect=True)
            )
        )
        assert len(responses) == 1
        assert responses[0].key == 7
        assert entity.is_on is False
        assert entity.effect == "Off"


# ---------------------------------------------------------------------------
# Device -> HA state sync
# ---------------------------------------------------------------------------


class TestStateSync:
    def test_matching_state_updates_and_broadcasts(self):
        entity, server, bus = make_entity(state_name="listening")
        entity.subscribe_state_sync()
        bus.publish(
            "publish_state_to_mqtt",
            {
                "state_name": "listening",
                "effect": "medium_pulse",
                "color": (0, 0, 255),
                "brightness": 0.5,
            },
        )
        assert entity.is_on is True
        assert entity.effect == "Medium Pulse"
        server.state.broadcast.assert_called_once()
        (response,) = server.state.broadcast.call_args[0][0]
        assert response.key == entity.key
        assert response.state is True

    def test_non_matching_state_ignored(self):
        entity, server, bus = make_entity(state_name="idle")
        entity.subscribe_state_sync()
        bus.publish(
            "publish_state_to_mqtt",
            {
                "state_name": "thinking",
                "effect": "spin",
                "color": (255, 255, 0),
                "brightness": 0.8,
            },
        )
        assert entity.effect != "Spin"
        server.state.broadcast.assert_not_called()

    def test_no_broadcast_when_unchanged(self):
        entity, server, bus = make_entity(state_name="idle")
        entity.subscribe_state_sync()
        bus.publish(
            "publish_state_to_mqtt",
            {
                "state_name": "idle",
                "effect": "solid",
                "color": (128, 0, 255),
                "brightness": 0.5,
            },
        )
        server.state.broadcast.assert_not_called()

    def test_turn_off_via_voice_state_marks_light_off(self):
        entity, server, bus = make_entity(
            state_name="idle",
            initial={"effect": "solid", "color": (128, 0, 255), "brightness": 0.5},
        )
        entity.subscribe_state_sync()
        bus.publish(
            "publish_state_to_mqtt",
            {
                "state_name": "idle",
                "effect": "off",
                "color": (128, 0, 255),
                "brightness": 0.5,
            },
        )
        assert entity.is_on is False
        assert entity.effect == "Off"
        server.state.broadcast.assert_called_once()

    def test_subscribe_state_sync_is_idempotent(self):
        entity, _, bus = make_entity()
        entity.subscribe_state_sync()
        entity.subscribe_state_sync()
        assert len(bus.topics["publish_state_to_mqtt"]) == 1


# ---------------------------------------------------------------------------
# ESPHome color wire model (api >= 1.6)
# ---------------------------------------------------------------------------


class TestColorWireModel:
    """HA (api >= 1.6) commands rgb as a hue vector (max channel 1.0) plus a
    separate color_brightness intensity and displays red x color_brightness x
    255. The entity must keep the color wheel and the master brightness
    slider independent below 100%."""

    def test_seed_splits_raw_color_into_hue_and_intensity(self):
        entity, _, _ = make_entity(
            initial={"effect": "solid", "color": (64, 0, 128), "brightness": 0.5}
        )
        assert (entity.red, entity.green, entity.blue) == (0.5, 0.0, 1.0)
        assert abs(entity._color_brightness - 128 / 255.0) < 0.001

    def test_black_color_seeds_zero_intensity(self):
        entity, _, _ = make_entity(
            initial={"effect": "solid", "color": (0, 0, 0), "brightness": 0.5}
        )
        assert (entity.red, entity.green, entity.blue) == (0.0, 0.0, 0.0)
        assert entity._color_brightness == 0.0

    def test_response_color_brightness_independent_of_brightness(self):
        entity, _, _ = make_entity()  # (128, 0, 255), brightness 0.5
        response = entity._state_response()
        assert abs(response.color_brightness - 1.0) < 0.001
        assert abs(response.brightness - 0.5) < 0.001

    def test_ha_wheel_command_keeps_brightness(self):
        # Pure red pick at 40% color intensity while master brightness sits
        # at 50%: HA sends hue (1,0,0) + color_brightness 0.4 + brightness.
        entity, _, bus = make_entity(
            initial={"effect": "solid", "color": (0, 255, 0), "brightness": 0.5}
        )
        list(
            entity.handle_message(
                make_light_command(
                    7,
                    red=1.0,
                    green=0.0,
                    blue=0.0,
                    has_rgb=True,
                    color_brightness=0.4,
                    has_color_brightness=True,
                    brightness=0.5,
                    has_brightness=True,
                )
            )
        )
        assert events_for(bus, "set_idle_color") == [
            {"color": {"r": 102, "g": 0, "b": 0}, "brightness": 128}
        ]
        assert (entity.red, entity.green, entity.blue) == (1.0, 0.0, 0.0)
        response = entity._state_response()
        assert abs(response.color_brightness - 0.4) < 0.001
        assert abs(response.brightness - 0.5) < 0.001

    def test_brightness_only_command_does_not_move_color(self):
        entity, _, bus = make_entity(
            initial={"effect": "solid", "color": (255, 0, 0), "brightness": 1.0}
        )
        list(
            entity.handle_message(
                make_light_command(7, brightness=0.25, has_brightness=True)
            )
        )
        assert events_for(bus, "set_idle_color") == [
            {"color": {"r": 255, "g": 0, "b": 0}, "brightness": 64}
        ]
        response = entity._state_response()
        assert entity.red == 1.0
        assert abs(entity._color_brightness - 1.0) < 0.001
        assert abs(response.brightness - 0.25) < 0.001

    def test_legacy_raw_rgb_without_color_brightness_splits(self):
        # Legacy clients fold intensity into rgb (max channel < 1.0).
        entity, _, bus = make_entity(
            initial={"effect": "solid", "color": (0, 0, 255), "brightness": 0.5}
        )
        list(
            entity.handle_message(
                make_light_command(7, red=0.5, green=0.0, blue=0.0, has_rgb=True)
            )
        )
        assert events_for(bus, "set_idle_color") == [
            {"color": {"r": 128, "g": 0, "b": 0}, "brightness": 128}
        ]
        assert (entity.red, entity.green, entity.blue) == (1.0, 0.0, 0.0)
        assert abs(entity._color_brightness - 0.5) < 0.001

    def test_device_sync_round_trip_is_stable(self):
        # HA pick (hue red, color_brightness 0.4) -> ring raw (102, 0, 0) ->
        # controller echoes (102, 0, 0); the mirror must not drift and no
        # redundant broadcast may fire.
        entity, server, bus = make_entity(
            initial={"effect": "solid", "color": (0, 255, 0), "brightness": 0.5}
        )
        list(
            entity.handle_message(
                make_light_command(
                    7,
                    red=1.0,
                    green=0.0,
                    blue=0.0,
                    has_rgb=True,
                    color_brightness=0.4,
                    has_color_brightness=True,
                )
            )
        )
        entity.subscribe_state_sync()
        bus.publish(
            "publish_state_to_mqtt",
            {
                "state_name": "idle",
                "effect": "solid",
                "color": (102, 0, 0),
                "brightness": 0.5,
            },
        )
        assert (entity.red, entity.green, entity.blue) == (1.0, 0.0, 0.0)
        assert abs(entity._color_brightness - 0.4) < 0.001
        server.state.broadcast.assert_not_called()

    def test_sync_broadcast_response_carries_color_brightness(self):
        entity, server, bus = make_entity(
            state_name="listening",
            initial={"effect": "solid", "color": (0, 0, 255), "brightness": 0.5},
        )
        entity.subscribe_state_sync()
        bus.publish(
            "publish_state_to_mqtt",
            {
                "state_name": "listening",
                "effect": "medium_pulse",
                "color": (0, 0, 64),
                "brightness": 0.5,
            },
        )
        (response,) = server.state.broadcast.call_args[0][0]
        assert abs(response.color_brightness - 64 / 255.0) < 0.001
        assert (response.red, response.green, response.blue) == (0.0, 0.0, 1.0)
