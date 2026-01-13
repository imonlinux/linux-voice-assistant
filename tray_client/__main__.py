#!/usr/bin/env python3
"""
LVA Tray Client

- Reads linux_voice_assistant/config.json to discover:
  - app.name  -> used for device_id
  - mqtt.host / port / username / password
- Subscribes to the LVA's MQTT topics to:
  - Track availability (online/offline)
  - Track mute state
  - Track per-state LED color (idle/listening/thinking/responding/error)
- Shows a system tray icon whose color matches the current LVA state.
- Provides a tray menu to start/stop/restart the LVA systemd --user service
  and toggle microphone mute.

Idle state now **also uses the MQTT color**, not a fixed purple.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import paho.mqtt.client as mqtt
from PyQt5 import QtCore, QtGui, QtWidgets

_LOGGER = logging.getLogger("lva_tray_client")

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def slugify_device_id(name: str) -> str:
    """Convert display name to a consistent device_id."""
    return name.strip().lower().replace(" ", "_")


def load_config(config_path: Optional[Path]) -> dict:
    """Load LVA config.json."""
    if config_path is None:
        # Default: same layout you’re using now:
        #   <repo_root>/linux_voice_assistant/config.json
        base = Path(__file__).resolve().parent
        config_path = base / "linux_voice_assistant" / "config.json"

    _LOGGER.info("Using config file: %s", config_path)
    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    app_cfg = cfg.get("app", {})
    mqtt_cfg = cfg.get("mqtt", {})

    name = app_cfg.get("name", "Linux Voice Assistant")
    device_id = slugify_device_id(name)

    host = mqtt_cfg.get("host", "127.0.0.1")
    port = int(mqtt_cfg.get("port", 1883))
    username = mqtt_cfg.get("username") or None
    password = mqtt_cfg.get("password") or None

    cfg["_resolved"] = {
        "name": name,
        "device_id": device_id,
        "mqtt_enabled": True,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
    }

    _LOGGER.info(
        "Loaded config: name=%s device_id=%s mqtt_enabled=%s host=%s port=%s",
        name,
        device_id,
        True,
        host,
        port,
    )
    return cfg


# ---------------------------------------------------------------------------
# Tray client
# ---------------------------------------------------------------------------


class LvaTrayClient(QtWidgets.QSystemTrayIcon):
    """System tray integration for the Linux Voice Assistant."""

    STATES = ["idle", "listening", "thinking", "responding", "error"]

    def __init__(self, app: QtWidgets.QApplication, cfg: dict):
        # QSystemTrayIcon init
        super().__init__(parent=None)
        self._app = app

        resolved = cfg["_resolved"]
        self._device_name = resolved["name"]
        self._device_id = resolved["device_id"]
        self._mqtt_host = resolved["host"]
        self._mqtt_port = resolved["port"]
        self._mqtt_username = resolved["username"]
        self._mqtt_password = resolved["password"]

        self._topic_prefix = f"lva/{self._device_id}"

        # Current state
        self._available: bool = False
        self._muted: bool = False
        self._current_state: str = "idle"

        # Default colors per state (fallbacks)
        self._default_colors: Dict[str, QtGui.QColor] = {
            "idle": QtGui.QColor(128, 0, 255),       # purple
            "listening": QtGui.QColor(0, 0, 255),    # blue
            "thinking": QtGui.QColor(255, 255, 0),   # yellow
            "responding": QtGui.QColor(0, 255, 0),   # green
            "error": QtGui.QColor(255, 165, 0),      # orange
        }
        # Last MQTT-derived color per state (idle included!)
        self._last_color_by_state: Dict[str, QtGui.QColor] = dict(
            self._default_colors
        )

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
            # Subscribe to availability + mute + all LVA topics (for LED states, num_leds, etc.)
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
            _LOGGER.debug(
                "MQTT message: topic=%s payload=%s", topic, payload
            )

            # availability
            if topic == f"{self._topic_prefix}/availability":
                self._handle_availability(payload)
                return

            # mute state
            if topic == f"{self._topic_prefix}/mute/state":
                self._handle_mute_state(payload)
                return

            # effect state (we don't currently use it for color, but log it)
            if topic.endswith("_effect/state"):
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
        _LOGGER.debug("Availability update: %s", payload)
        self._available = online
        self._update_tray_icon()

    def _handle_mute_state(self, payload: str):
        new_muted = payload.strip().upper() == "ON"
        _LOGGER.debug("Mute update: %s", new_muted)
        self._muted = new_muted
        # Reflect in menu
        self._mute_action.setChecked(self._muted)
        self._update_tray_icon()

    def _handle_light_state(self, topic: str, payload: str):
        """
        Handle JSON from .../<state>_light/state
        Example payload:
          {"state": "ON", "brightness": 127,
           "color": {"r": 0, "g": 0, "b": 255}}
        """
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Invalid JSON on light state: %s", payload)
            return

        # Extract state name from topic
        # lva/<device>/<state>_light/state
        parts = topic.split("/")
        if len(parts) < 4:
            return
        state_part = parts[-2]  # "<state>_light"
        if not state_part.endswith("_light"):
            return
        state_name = state_part[: -len("_light")]
        if state_name not in self.STATES:
            return

        _LOGGER.debug(
            "Decoded light state: state_name=%s data=%s", state_name, data
        )

        state_flag = data.get("state", "OFF").upper()
        color_dict = data.get("color", {}) or {}
        brightness = int(data.get("brightness", 255))
        brightness = max(0, min(brightness, 255))

        r = int(color_dict.get("r", 0))
        g = int(color_dict.get("g", 0))
        b = int(color_dict.get("b", 0))

        # Apply brightness scaling to the MQTT color
        scale = brightness / 255.0 if brightness > 0 else 0.0
        r_scaled = max(0, min(255, int(r * scale)))
        g_scaled = max(0, min(255, int(g * scale)))
        b_scaled = max(0, min(255, int(b * scale)))

        qcolor = QtGui.QColor(r_scaled, g_scaled, b_scaled)
        self._last_color_by_state[state_name] = qcolor

        # State transitions:
        # - For non-idle: treat "ON" as "this is the active state"
        # - For idle: **always** treat updates as "idle is the current baseline"
        if state_name == "idle":
            _LOGGER.debug(
                "Tray state_update (idle): data=%s (using MQTT color for idle)",
                data,
            )
            self._current_state = "idle"
        else:
            if state_flag == "ON":
                _LOGGER.debug(
                    "Tray state_update: state_name=%s data=%s",
                    state_name,
                    data,
                )
                if self._current_state != state_name:
                    _LOGGER.debug(
                        "Tray state changing: %s -> %s",
                        self._current_state,
                        state_name,
                    )
                self._current_state = state_name
            else:
                # Ignore OFF for non-idle states
                _LOGGER.debug(
                    "Ignoring OFF for non-idle state %s", state_name
                )

        self._update_tray_icon()

    # ------------------------------------------------------------------
    # Icon rendering
    # ------------------------------------------------------------------

    def _set_icon_by_key(self, key: str):
        """
        Convenience wrapper to set icon for:
          - 'offline'
          - any state name in STATES
        """
        if key == "offline":
            color = QtGui.QColor(128, 128, 128)  # grey
            tooltip_state = "offline"
        else:
            color = self._last_color_by_state.get(
                key, self._default_colors.get(key, QtGui.QColor(128, 128, 128))
            )
            tooltip_state = key

        # If muted, tint red (but keep some info from base color)
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

        # Update tooltip
        tip = f"{self._device_name} – {tooltip_state}"
        self.setToolTip(tip)
        if self.contextMenu():
            self._status_action.setText(tip)

        _LOGGER.debug(
            "Setting tray icon: key=%s available=%s muted=%s", key, self._available, self._muted
        )

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
        type=str,
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

    config_path = Path(args.config) if args.config else None
    cfg = load_config(config_path)

    # Make Qt app
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    tray = LvaTrayClient(app, cfg)

    # Start event loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

