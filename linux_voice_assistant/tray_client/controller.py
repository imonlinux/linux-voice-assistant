"""
Tray Controller
Business logic for the LVA Tray Client.
Handles MQTT communication, state management, and service control.
"""

import json
import logging
import subprocess
from typing import Dict, Tuple

import paho.mqtt.client as mqtt
from PyQt5.QtCore import QObject, pyqtSignal

from ..config import Config
from ..models import SatelliteState
from ..util import slugify_device_id

_LOGGER = logging.getLogger(__name__)


class TrayController(QObject):
    """
    Manages the application state and MQTT connection.
    Emits signals when state changes so the UI can update safely.
    """

    # Signals for UI updates
    # available, state_name, rgb_tuple, is_muted
    state_updated = pyqtSignal(bool, str, tuple, bool)
    
    def __init__(self, config: Config):
        super().__init__()
        self._config = config
        
        self._device_name = config.app.name
        self._device_id = slugify_device_id(self._device_name)
        self._topic_prefix = f"lva/{self._device_id}"

        # MQTT Config
        self._mqtt_host = config.mqtt.host or "127.0.0.1"
        self._mqtt_port = config.mqtt.port
        self._mqtt_username = config.mqtt.username
        self._mqtt_password = config.mqtt.password

        # Internal State
        self._available = False
        self._muted = False
        self._current_state = SatelliteState.IDLE.value
        
        # Color mapping (State -> (r, g, b))
        # Default colors
        self._colors: Dict[str, Tuple[int, int, int]] = {
            SatelliteState.IDLE.value: (128, 0, 255),       # purple
            SatelliteState.LISTENING.value: (0, 0, 255),    # blue
            SatelliteState.THINKING.value: (255, 255, 0),   # yellow
            SatelliteState.RESPONDING.value: (0, 255, 0),   # green
            SatelliteState.ERROR.value: (255, 165, 0),      # orange
        }

        # Initialize MQTT Client
        self._client = mqtt.Client()
        if self._mqtt_username:
            self._client.username_pw_set(self._mqtt_username, self._mqtt_password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    def start(self):
        """Start the MQTT client."""
        _LOGGER.debug("Connecting to MQTT %s:%s", self._mqtt_host, self._mqtt_port)
        try:
            self._client.connect(self._mqtt_host, self._mqtt_port, 60)
            self._client.loop_start()
        except Exception:
            _LOGGER.exception("Failed to connect to MQTT broker")

    def stop(self):
        """Stop the MQTT client."""
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Actions (Called by UI)
    # -------------------------------------------------------------------------

    def toggle_mute(self, mute_on: bool):
        """Publish mute command to MQTT."""
        topic = f"{self._topic_prefix}/mute/set"
        payload = "ON" if mute_on else "OFF"
        _LOGGER.info("Publishing mute command: %s -> %s", topic, payload)
        try:
            self._client.publish(topic, payload, retain=False)
            # Optimistic update
            self._muted = mute_on
            self._emit_update()
        except Exception:
            _LOGGER.exception("Failed to publish mute command")

    def control_service(self, action: str):
        """Run systemctl commands for the main service."""
        service = "linux-voice-assistant.service"
        cmd = ["systemctl", "--user", action, service]
        _LOGGER.info("Running: %s", " ".join(cmd))
        subprocess.run(cmd, check=False)

    def get_device_name(self) -> str:
        return self._device_name

    def is_muted(self) -> bool:
        return self._muted

    # -------------------------------------------------------------------------
    # MQTT Callbacks (Run on background thread)
    # -------------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker")
            client.subscribe(f"{self._topic_prefix}/availability")
            client.subscribe(f"{self._topic_prefix}/mute/state")
            client.subscribe(f"{self._topic_prefix}/#")
        else:
            _LOGGER.error("MQTT Connect failed: %s", rc)

    def _on_disconnect(self, client, userdata, rc):
        _LOGGER.warning("MQTT Disconnected: %s", rc)
        self._available = False
        self._emit_update()

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode()
            retained = bool(msg.retain)  # Check retain flag
            
            # Log all incoming messages at DEBUG level for troubleshooting
            _LOGGER.debug("MQTT RX: %s | Payload: %s | Retained: %s", topic, payload, retained)

            if topic == f"{self._topic_prefix}/availability":
                self._available = (payload.strip().lower() == "online")
                _LOGGER.info("Availability changed: %s", self._available)
                self._emit_update()
                return

            if topic == f"{self._topic_prefix}/mute/state":
                self._muted = (payload.strip().upper() == "ON")
                _LOGGER.info("Mute state changed: %s", self._muted)
                self._emit_update()
                return

            if topic.endswith("_light/state"):
                self._handle_light_state(topic, payload, retained)
                return

        except Exception:
            _LOGGER.exception("Error processing MQTT message")

    def _handle_light_state(self, topic: str, payload: str, retained: bool):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Failed to decode JSON from %s: %s", topic, payload)
            return

        # Extract state name: lva/<device>/<state>_light/state
        parts = topic.split("/")
        if len(parts) < 4:
            return
        
        # e.g., "idle_light" -> "idle"
        state_part = parts[-2]
        if not state_part.endswith("_light"):
            return
        state_name = state_part[:-6]

        if state_name not in self._colors:
            return

        # 1. ALWAYS update the color definition (so custom colors work immediately)
        color_dict = data.get("color", {})
        r = int(color_dict.get("r", 0))
        g = int(color_dict.get("g", 0))
        b = int(color_dict.get("b", 0))
        
        # Parse Brightness
        brightness = int(data.get("brightness", 255))
        scale = brightness / 255.0 if brightness > 0 else 0.0
        
        # Scale color
        final_rgb = (
            max(0, min(255, int(r * scale))),
            max(0, min(255, int(g * scale))),
            max(0, min(255, int(b * scale)))
        )
        
        # Update color map
        self._colors[state_name] = final_rgb

        # 2. ONLY update the current state if this is NOT a retained message
        #    Retained messages are just history/config; fresh messages are events.
        if retained:
            _LOGGER.debug("Updated color for %s from retained message; ignoring state transition", state_name)
            # We still emit update in case the color of the *current* state changed
            self._emit_update()
            return

        # Update Current State logic
        state_flag = data.get("state", "OFF").upper()
        
        _LOGGER.debug("Handling light update: state_name=%s, flag=%s", state_name, state_flag)

        if state_flag == "ON":
            # If a state turns ON, it becomes the active state
            if self._current_state != state_name:
                _LOGGER.info("State transition: %s -> %s", self._current_state, state_name)
                self._current_state = state_name
        elif state_flag == "OFF":
            # If the current active state turns OFF, fall back to IDLE
            if self._current_state == state_name:
                 _LOGGER.info("Current state %s turned OFF, falling back to IDLE", state_name)
                 self._current_state = SatelliteState.IDLE.value

        self._emit_update()

    def _emit_update(self):
        """Emit the current state to the UI (thread-safe)."""
        color = self._colors.get(self._current_state, (128, 128, 128))
        # emit(available, state_name, (r,g,b), is_muted)
        self.state_updated.emit(
            self._available,
            self._current_state,
            color,
            self._muted
        )
