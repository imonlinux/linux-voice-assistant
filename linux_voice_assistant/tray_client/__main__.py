#!/usr/bin/env python3
"""
LVA Tray Client

- Reads linux_voice_assistant/config.json using the shared config loader.
- Subscribes to the LVA's MQTT topics to track availability, mute, and LED states.
- Shows a system tray icon whose color matches the current LVA state.
- Provides a tray menu to control the systemd service and toggle mute.
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import paho.mqtt.client as mqtt
from PyQt5 import QtCore, QtGui, QtWidgets

from ..config import Config, load_config_from_json
from ..models import SatelliteState
from ..util import slugify_device_id

_LOGGER = logging.getLogger("lva_tray_client")

# ---------------------------------------------------------------------------
# Tray client
# ---------------------------------------------------------------------------


class LvaTrayClient(QtWidgets.QSystemTrayIcon):
    """System tray integration for the Linux Voice Assistant."""

    def __init__(self, app: QtWidgets.QApplication, config: Config):
        # QSystemTrayIcon init
        super().__init__(parent=None)
        self._app = app
        self._config = config

        self._device_name = config.app.name
        self._device_id = slugify_device_id(self._device_name)
        
        # MQTT Config
        self._mqtt_host = config.mqtt.host or "127.0.0.1"
        self._mqtt_port = config.mqtt.port
        self._mqtt_username = config.mqtt.username
        self._mqtt_password = config.mqtt.password

        self._topic_prefix = f"lva/{self._device_id}"

        # Current state
        self._available: bool = False
        self._muted: bool = False
        self._current_state: str = SatelliteState.IDLE.value

        # Default colors per state
        # Note: We filter out STARTING as it is transient/internal
        self._default_colors: Dict[str, QtGui.QColor] = {
            SatelliteState.IDLE.value: QtGui.QColor(128, 0, 255),       # purple
            SatelliteState.LISTENING.value: QtGui.QColor(0, 0, 255),    # blue
            SatelliteState.THINKING.value: QtGui.QColor(255, 255, 0),   # yellow
            SatelliteState.RESPONDING.value: QtGui.QColor(0, 255, 0),   # green
            SatelliteState.ERROR.value: QtGui.QColor(255, 165, 0),      # orange
        }
        
        # Last MQTT-derived color per state
        self._last_color_by_state: Dict[str, QtGui.QColor] = dict(self._default_colors)

        # Build context menu
        self._build_menu()

        # Initial icon: offline
        self._set_icon_by_key("offline")

        # MQTT setup
        self._client = mqtt.Client()
        if self._mqtt_username:
            self._client.username_pw_set(self._mqtt_username, self._mqtt_password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        _LOGGER.debug(
            "MQTT connecting to %s:%s", self._mqtt_host, self._mqtt_port
        )
        try:
            self._client.connect(self._mqtt_host, self._mqtt_port, 60)
            self._client.loop_start()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to connect to MQTT broker")

        # Show tray icon
        self.setVisible(True)
        _LOGGER.info(
            "LVA Tray Client started for device_id=%s", self._device_id
        )

    # ------------------------------------------------------------------
    # Menu / actions
    # ------------------------------------------------------------------

    def _build_menu(self):
        menu = QtWidgets.QMenu()

        # Status label
        self._status_action = QtWidgets.QAction("LVA Tray Client", self)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        # Service control
        start_action = QtWidgets.QAction("Start LVA", self)
        start_action.triggered.connect(self._start_lva)
        menu.addAction(start_action)

        stop_action = QtWidgets.QAction("Stop LVA", self)
        stop_action.triggered.connect(self._stop_lva)
        menu.addAction(stop_action)

        restart_action = QtWidgets.QAction("Restart LVA", self)
        restart_action.triggered.connect(self._restart_lva)
        menu.addAction(restart_action)

        menu.addSeparator()

        # Mute toggle
        self._mute_action = QtWidgets.QAction("Mute Microphone", self)
        self._mute_action.setCheckable(True)
        self._mute_action.triggered.connect(self._toggle_mute)
        menu.addAction(self._mute_action)

        menu.addSeparator()

        # Quit
        quit_action = QtWidgets.QAction("Quit Tray Client", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def _start_lva(self):
        _LOGGER.info("Running: systemctl --user start linux-voice-assistant.service")
        subprocess.run(
            ["systemctl", "--user", "start", "linux-voice-assistant.service"],
            check=False,
        )

    def _stop_lva(self):
        _LOGGER.info("Running: systemctl --user stop linux-voice-assistant.service")
        subprocess.run(
            ["systemctl", "--user", "stop", "linux-voice-assistant.service"],
            check=False,
        )

    def _restart_lva(self):
        _LOGGER.info("Running: systemctl --user restart linux-voice-assistant.service")
        subprocess.run(
            ["systemctl", "--user", "restart", "linux-voice-assistant.service"],
            check=False,
        )

    def _toggle_mute(self, checked: bool):
        self._mute_action.setChecked(checked)
        self._muted = checked
        # Publish to LVA
        topic = f"{self._topic_prefix}/mute/set"
        payload = "ON" if checked else "OFF"
        _LOGGER.debug("Publishing mute command: %s -> %s", topic, payload)
        try:
            self._client.publish(topic, payload, retain=False)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to publish mute command")
        # Update local icon
        self._update_tray_icon()

    def _quit(self):
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._app.quit()

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):  # noqa: ARG002
        if rc == 0:
            _LOGGER.info("Connected to MQTT broker (tray)")
            # Subscribe to availability + mute + all LVA topics
            client.subscribe(f"{self._topic_prefix}/availability")
            client.subscribe(f"{self._topic_prefix}/mute/state")
            client.subscribe(f"{self._topic_prefix}/#")
        else:
            _LOGGER.error("Failed to connect to MQTT, return code %d", rc)

    def _on_disconnect(self, client, userdata, rc):  # noqa: ARG002
        _LOGGER.warning("MQTT disconnected (rc=%s)", rc)
        self._available = False
        self._update_tray_icon()

    def _on_message(self, client, userdata, msg):  # noqa: ARG002
        try:
            topic = msg.topic
            payload = msg.payload.decode()
            
            # availability
            if topic == f"{self._topic_prefix}/availability":
                self._handle_availability(payload)
                return

            # mute state
            if topic == f"{self._topic_prefix}/mute/state":
                self._handle_mute_state(payload)
                return

            # light state
            if topic.endswith("_light/state"):
                self._handle_light_state(topic, payload)
                return

        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error in _on_message")

    # ------------------------------------------------------------------
    # MQTT handlers
    # ------------------------------------------------------------------

    def _handle_availability(self, payload: str):
        online = payload.strip().lower() == "online"
        self._available = online
        self._update_tray_icon()

    def _handle_mute_state(self, payload: str):
        new_muted = payload.strip().upper() == "ON"
        self._muted = new_muted
        self._mute_action.setChecked(self._muted)
        self._update_tray_icon()

    def _handle_light_state(self, topic: str, payload: str):
        """
        Handle JSON from .../<state>_light/state
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return

        # Extract state name from topic: lva/<device>/<state>_light/state
        parts = topic.split("/")
        if len(parts) < 4:
            return
        state_part = parts[-2]  # "<state>_light"
        if not state_part.endswith("_light"):
            return
        state_name = state_part[: -len("_light")]
        
        # Verify this is a known state (ignore unknown or 'starting')
        if state_name not in self._default_colors:
            return

        state_flag = data.get("state", "OFF").upper()
        color_dict = data.get("color", {}) or {}
        brightness = int(data.get("brightness", 255))
        brightness = max(0, min(brightness, 255))

        r = int(color_dict.get("r", 0))
        g = int(color_dict.get("g", 0))
        b = int(color_dict.get("b", 0))

        # Apply brightness scaling
        scale = brightness / 255.0 if brightness > 0 else 0.0
        r_scaled = max(0, min(255, int(r * scale)))
        g_scaled = max(0, min(255, int(g * scale)))
        b_scaled = max(0, min(255, int(b * scale)))

        qcolor = QtGui.QColor(r_scaled, g_scaled, b_scaled)
        self._last_color_by_state[state_name] = qcolor

        # State transitions
        if state_name == SatelliteState.IDLE.value:
            # Idle updates just change the idle color, they don't force state transition
            # unless we are already in idle.
            if self._current_state == SatelliteState.IDLE.value:
                self._current_state = SatelliteState.IDLE.value
        else:
            if state_flag == "ON":
                self._current_state = state_name

        self._update_tray_icon()

    # ------------------------------------------------------------------
    # Icon rendering
    # ------------------------------------------------------------------

    def _set_icon_by_key(self, key: str):
        if key == "offline":
            color = QtGui.QColor(128, 128, 128)
            tooltip_state = "offline"
        else:
            color = self._last_color_by_state.get(
                key, self._default_colors.get(key, QtGui.QColor(128, 128, 128))
            )
            tooltip_state = key

        # If muted, tint red
        if self._muted and key != "offline":
            base = color
            color = QtGui.QColor(
                min(255, base.red() + 120),
                max(0, int(base.green() * 0.4)),
                max(0, int(base.blue() * 0.4)),
            )
            tooltip_state += " (muted)"

        icon = self._make_circle_icon(color)
        self.setIcon(icon)

        tip = f"{self._device_name} – {tooltip_state}"
        self.setToolTip(tip)
        if self.contextMenu():
            self._status_action.setText(tip)

    def _make_circle_icon(self, color: QtGui.QColor) -> QtGui.QIcon:
        size = 20
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        pen = QtGui.QPen(QtGui.QColor(0, 0, 0, 160))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(color))

        radius = size // 2 - 2
        painter.drawEllipse(2, 2, radius * 2, radius * 2)
        painter.end()

        return QtGui.QIcon(pixmap)

    def _update_tray_icon(self):
        if not self._available:
            self._set_icon_by_key("offline")
        else:
            self._set_icon_by_key(self._current_state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="LVA Tray Client")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to LVA config.json (defaults to linux_voice_assistant/config.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Resolve config path
    if args.config:
        config_path = args.config
    else:
        # Default: ../../config.json relative to this file
        config_path = Path(__file__).resolve().parent.parent / "config.json"

    _LOGGER.info("Loading config from %s", config_path)
    
    try:
        config = load_config_from_json(config_path)
    except Exception:
        _LOGGER.critical("Failed to load config file.")
        sys.exit(1)

    # Make Qt app
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    tray = LvaTrayClient(app, config)  # noqa: F841

    # Start event loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()