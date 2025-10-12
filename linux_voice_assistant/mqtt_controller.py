import json
import logging
from typing import TYPE_CHECKING, List

import paho.mqtt.client as mqtt

from .event_bus import EventHandler, subscribe

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)

class MqttController(EventHandler):
    def __init__(self, state: "ServerState", host: str, port: int, username: str, password: str):
        super().__init__(state)
        self._host = host
        self._port = port
        self._username = username
        self._password = password

        self._device_name = self.state.name
        self._device_id = self.state.name.lower().replace(" ", "_")

        self._topic_prefix = f"lva/{self._device_id}"
        self.topics = {
            "mute": {
                "command": f"{self._topic_prefix}/mute/set",
                "state": f"{self._topic_prefix}/mute/state",
            },
            "num_leds": {
                "command": f"{self._topic_prefix}/num_leds/set",
                "state": f"{self._topic_prefix}/num_leds/state",
            }
        }
        for state_name in ["idle", "listening", "thinking", "responding", "error"]:
            self.topics[state_name] = {
                "effect_command": f"{self._topic_prefix}/{state_name}_effect/set",
                "effect_state": f"{self._topic_prefix}/{state_name}_effect/state",
                "light_command": f"{self._topic_prefix}/{state_name}_light/set",
                "light_state": f"{self._topic_prefix}/{state_name}_light/state",
            }
        
        self._client = mqtt.Client()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

    def start(self):
        try:
            if self._username:
                self._client.username_pw_set(self._username, self._password)
            
            _LOGGER.debug("Connecting to MQTT broker at %s:%s", self._host, self._port)
            self._client.connect(self._host, self._port, 60)
            self._client.loop_start()
        except Exception:
            _LOGGER.exception("Failed to connect to MQTT broker")

    def stop(self):
        self._client.publish(f"{self._topic_prefix}/availability", "offline", retain=True)
        self._client.loop_stop()
        self._client.disconnect()
        _LOGGER.debug("Disconnected from MQTT broker")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            for entity_type in self.topics.values():
                for topic_type, topic in entity_type.items():
                    if "command" in topic_type:
                        client.subscribe(topic)
            self._publish_discovery_configs()
        else:
            _LOGGER.error("Failed to connect to MQTT, return code %d", rc)

    def _on_message(self, client, userdata, msg):
        payload_str = msg.payload.decode()
        _LOGGER.debug("Received MQTT message on topic %s", msg.topic)

        if msg.topic == self.topics["mute"]["command"]:
            self.state.event_bus.publish("set_mic_mute", {"state": payload_str.upper() == "ON"})
        
        # --- THIS IS THE FIX ---
        # The logic to save the preference was missing. It is now re-added.
        elif msg.topic == self.topics["num_leds"]["command"]:
            try:
                num_leds = int(payload_str)
                # First, publish the internal event so the LedController can log the message
                self.state.event_bus.publish("set_num_leds", {"num_leds": num_leds})
                # Then, save the setting to the preferences file
                if self.state.preferences.num_leds != num_leds:
                    self.state.preferences.num_leds = num_leds
                    self.state.save_preferences()
                # Finally, publish the state back to MQTT to confirm the change in HA
                self.publish_num_leds_state(num_leds)
            except ValueError:
                _LOGGER.warning("Received invalid value for num_leds: %s", payload_str)
        
        for state_name in ["idle", "listening", "thinking", "responding", "error"]:
            state_topics = self.topics[state_name]
            if msg.topic == state_topics["effect_command"]:
                effect_id = payload_str.lower().replace(" ", "_")
                self.state.event_bus.publish(f"set_{state_name}_effect", {"effect": effect_id})
            elif msg.topic == state_topics["light_command"]:
                try:
                    data = json.loads(payload_str)
                    if data.get("state", "ON").upper() == "OFF":
                        self.state.event_bus.publish(f"set_{state_name}_effect", {"effect": "off"})
                    else:
                        self.state.event_bus.publish(f"set_{state_name}_effect", {"effect": "solid"})
                        self.state.event_bus.publish(f"set_{state_name}_color", data)
                except json.JSONDecodeError: pass


    def _publish_discovery_configs(self):
        availability_topic = f"{self._topic_prefix}/availability"
        device_info = { "identifiers": [self._device_id], "name": self._device_name, "manufacturer": "LVA Project" }
        options = ["Off", "Solid", "Slow Pulse", "Medium Pulse", "Fast Pulse", "Slow Blink", "Medium Blink", "Fast Blink", "Spin"]

        mute_cfg = { "name": "Mute Microphone", "unique_id": f"{self._device_id}_mute", "command_topic": self.topics["mute"]["command"], "state_topic": self.topics["mute"]["state"], "availability_topic": availability_topic, "icon": "mdi:microphone-off", "device": device_info }
        self._client.publish(f"homeassistant/switch/{self._device_id}_mute/config", json.dumps(mute_cfg), retain=True)

        num_leds_cfg = { "name": "Number of LEDs", "unique_id": f"{self._device_id}_num_leds", "command_topic": self.topics["num_leds"]["command"], "state_topic": self.topics["num_leds"]["state"], "availability_topic": availability_topic, "min": 1, "max": 256, "step": 1, "icon": "mdi:counter", "device": device_info, "mode": "box", "entity_category": "config" }
        self._client.publish(f"homeassistant/number/{self._device_id}_num_leds/config", json.dumps(num_leds_cfg), retain=True)

        for state_name in ["idle", "listening", "thinking", "responding", "error"]:
            capital_name = state_name.title()
            
            select_cfg = { "name": f"{capital_name} Effect", "unique_id": f"{self._device_id}_{state_name}_effect", "command_topic": self.topics[state_name]["effect_command"], "state_topic": self.topics[state_name]["effect_state"], "availability_topic": availability_topic, "options": options, "icon": "mdi:palette-swatch-variant", "device": device_info, "entity_category": "config" }
            self._client.publish(f"homeassistant/select/{self._device_id}_{state_name}_effect/config", json.dumps(select_cfg), retain=True)

            light_cfg = { "name": f"{capital_name} Color", "unique_id": f"{self._device_id}_{state_name}_color", "schema": "json", "command_topic": self.topics[state_name]["light_command"], "state_topic": self.topics[state_name]["light_state"], "availability_topic": availability_topic, "brightness": True, "color_mode": True, "supported_color_modes": ["rgb"], "device": device_info, "entity_category": "config" }
            self._client.publish(f"homeassistant/light/{self._device_id}_{state_name}_color/config", json.dumps(light_cfg), retain=True)

        _LOGGER.debug("Published all MQTT discovery configs")
        self._client.publish(availability_topic, "online", retain=True)
        
        self.publish_mute_state(self.state.mic_muted)
        self.publish_num_leds_state(self.state.preferences.num_leds)

    def publish_mute_state(self, is_muted: bool):
        self._client.publish(self.topics["mute"]["state"], "ON" if is_muted else "OFF", retain=True)
    
    def publish_num_leds_state(self, num_leds: int):
        self._client.publish(self.topics["num_leds"]["state"], str(num_leds), retain=True)

    @subscribe
    def publish_state_to_mqtt(self, data: dict):
        state_name = data.get("state_name")
        if state_name in self.topics:
            state_topics = self.topics[state_name]
            
            effect_name = data.get("effect", "off").replace("_", " ").title()
            self._client.publish(state_topics["effect_state"], effect_name, retain=True)

            light_state = {
                "state": "ON" if data.get("effect") != "off" else "OFF",
                "brightness": int(data.get("brightness", 0.5) * 255),
                "color": { "r": data.get("color")[0], "g": data.get("color")[1], "b": data.get("color")[2] }
            }
            self._client.publish(state_topics["light_state"], json.dumps(light_state), retain=True)
