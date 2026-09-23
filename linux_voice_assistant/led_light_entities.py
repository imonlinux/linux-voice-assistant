"""Fork: native per-state LED light entities (retire_mqtt groundwork).

One ESPHome Light entity per configurable LED state (idle, listening,
thinking, responding, error), so Home Assistant controls the in-daemon
LED ring through the native API instead of MQTT discovery.

Each entity translates LightCommandRequest into the exact EventBus
topics the LedController already consumes, the same translation the
MQTT handler performs for its light/select topics:

    set_<state>_effect  {"effect": "<id>"}   id like "slow_pulse"
    set_<state>_color   {"color": {"r","g","b"}, "brightness"} 0-255 scale

MQTT stays fully functional in parallel; retirement is a config change
(mqtt.enabled = false), not a code path switch.

State flows back to HA through the "publish_state_to_mqtt" EventBus
topic — the same truth source the MQTT light_state topic and the tray
client already follow — so HA shows what the ring is actually doing
(e.g. the active voice state's color) rather than only what it last
commanded.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, Iterable, Optional

from .entity import LEDLightEntity, LightCommandRequest
from .event_bus import EventBus

if TYPE_CHECKING:  # pragma: no cover
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)

# The configurable LED states, mirroring MqttController.CONFIGURABLE_STATES
# (kept local so the native path does not depend on the MQTT module).
LED_STATES = ("idle", "listening", "thinking", "responding", "error")

# Effect display names offered to HA. Identical wording to the MQTT effect
# select options; normalization ("Slow Pulse" -> "slow_pulse") matches the
# MQTT handler so LedController receives the ids it already knows.
LED_EFFECTS = (
    "Off",
    "Solid",
    "Slow Pulse",
    "Medium Pulse",
    "Fast Pulse",
    "Slow Blink",
    "Medium Blink",
    "Fast Blink",
    "Spin",
)

# Initial entity state when no LED controller config is available
# (LED controller failed to init). Matches LedController's idle defaults.
_FALLBACK_INITIAL = {
    "effect": "off",
    "color": (128, 0, 255),
    "brightness": 0.5,
}


def _effect_to_id(effect_display: str) -> str:
    """'Slow Pulse' -> 'slow_pulse' (same rule as the MQTT handler)."""
    return effect_display.lower().replace(" ", "_")


def _effect_to_display(effect_id: str) -> str:
    """'slow_pulse' -> 'Slow Pulse' (same rule as MQTT state publishing)."""
    return effect_id.replace("_", " ").title()


class LedStateLightEntity(LEDLightEntity):
    """Native HA Light for one configurable LED state.

    Translates HA light commands into LedController EventBus topics and
    mirrors device-driven state changes (voice transitions) back to HA.
    """

    def __init__(
        self,
        server: Any,
        key: int,
        state_name: str,
        event_bus: EventBus,
        initial: Optional[Dict[str, Any]] = None,
    ) -> None:
        config = initial or _FALLBACK_INITIAL
        effect_display = _effect_to_display(str(config.get("effect", "off")))
        color = config.get("color", _FALLBACK_INITIAL["color"])
        self.state_name = state_name
        # Last non-off effect, used to restore when HA turns the light ON
        # without naming an effect (the MQTT path published a turn_on_
        # topic that had no subscriber, i.e. ON was a silent no-op there).
        self._last_effect_display = (
            effect_display if effect_display != "Off" else "Solid"
        )
        self._event_bus = event_bus
        self._sync_subscribed = False

        super().__init__(
            server=server,
            key=key,
            name=f"LED {state_name.title()}",
            object_id=f"led_{state_name}",
            effects=list(LED_EFFECTS),
            supports_rgb=True,
            supports_brightness=True,
            # EventBus translation happens in handle_message below; no
            # peripheral on_changed forwarding for these entities.
            on_changed=None,
            icon="mdi:led-strip-variant",
        )

        # Seed the mirrored state from the LED controller's actual config
        # so HA shows truth at discovery time instead of a hardcoded color.
        self.effect = effect_display if effect_display in LED_EFFECTS else "Off"
        self.is_on = self.effect != "Off"
        self.brightness = max(0.0, min(1.0, float(config.get("brightness", 0.5))))
        try:
            self.red = max(0.0, min(1.0, int(color[0]) / 255.0))
            self.green = max(0.0, min(1.0, int(color[1]) / 255.0))
            self.blue = max(0.0, min(1.0, int(color[2]) / 255.0))
        except (TypeError, ValueError, IndexError):
            pass

    # ------------------------------------------------------------------
    # HA -> device translation
    # ------------------------------------------------------------------

    def _publish_effect(self, effect_id: str) -> None:
        self._event_bus.publish(
            f"set_{self.state_name}_effect", {"effect": effect_id}
        )

    def _publish_color(self) -> None:
        self._event_bus.publish(
            f"set_{self.state_name}_color",
            {
                "color": {
                    "r": int(round(self.red * 255)),
                    "g": int(round(self.green * 255)),
                    "b": int(round(self.blue * 255)),
                },
                # LedController divides by 255; keep the MQTT wire scale.
                "brightness": int(round(self.brightness * 255)),
            },
        )

    def _translate_command(self, msg: Any) -> None:
        """Mirror the MQTT handler's light_command translation.

        Deliberate improvement over MQTT parity: turning the light ON
        restores the last non-off effect (MQTT published turn_on_<state>,
        a topic nothing subscribes to, so ON was a silent no-op).
        """
        if msg.has_state and not msg.has_effect:
            if bool(msg.state):
                self._publish_effect(_effect_to_id(self._last_effect_display))
            else:
                self._publish_effect("off")

        if msg.has_effect:
            effect_display = str(msg.effect)
            effect_id = _effect_to_id(effect_display)
            if effect_id == "off":
                self._publish_effect("off")
            else:
                self._last_effect_display = effect_display
                self._publish_effect(effect_id)

        if msg.has_rgb or msg.has_brightness:
            self._publish_color()

    def handle_message(self, msg: Any) -> Iterable[Any]:
        if isinstance(msg, LightCommandRequest) and msg.key == self.key:
            # Parent applies the command to the mirrored state first
            # (clamping, effect validation), then the translation reads
            # the mirror so published values match what was applied.
            responses = list(super().handle_message(msg))
            self._translate_command(msg)
            return iter(responses)
        return super().handle_message(msg)

    # ------------------------------------------------------------------
    # Device -> HA state sync
    # ------------------------------------------------------------------

    def subscribe_state_sync(self) -> None:
        """Follow 'publish_state_to_mqtt' for this entity's state.

        Idempotent: the EventBus outlives satellite reconstruction (HA
        reconnects), so a reattached entity must not subscribe twice.
        """
        if self._sync_subscribed:
            return
        self._sync_subscribed = True
        self._event_bus.subscribe("publish_state_to_mqtt", self._on_state_published)

    def _on_state_published(self, data: Dict[str, Any]) -> None:
        if data.get("state_name") != self.state_name:
            return

        effect_display = _effect_to_display(str(data.get("effect", "off")))
        if effect_display not in LED_EFFECTS:
            effect_display = "Off"
        is_on = effect_display != "Off"
        brightness = max(0.0, min(1.0, float(data.get("brightness", 0.5))))
        color = data.get("color", _FALLBACK_INITIAL["color"])
        red = max(0.0, min(1.0, int(color[0]) / 255.0))
        green = max(0.0, min(1.0, int(color[1]) / 255.0))
        blue = max(0.0, min(1.0, int(color[2]) / 255.0))

        changed = (
            self.is_on != is_on
            or self.effect != effect_display
            or abs(self.brightness - brightness) > 0.001
            or abs(self.red - red) > 0.001
            or abs(self.green - green) > 0.001
            or abs(self.blue - blue) > 0.001
        )
        if not changed:
            return

        self.is_on = is_on
        self.effect = effect_display
        self.brightness = brightness
        self.red = red
        self.green = green
        self.blue = blue
        if effect_display != "Off":
            self._last_effect_display = effect_display

        # Push the update to every connected client (the change came from
        # outside any request; self.server may even be a stale connection).
        response = self._state_response()
        state = getattr(self.server, "state", None)
        if state is not None:
            state.broadcast([response])
