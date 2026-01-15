"""
Tray UI
Visual elements for the LVA Tray Client.
Handles the System Tray Icon, Menu, and drawing logic.
"""

import logging
from typing import Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

# Type alias for clarity
RgbTuple = Tuple[int, int, int]

_LOGGER = logging.getLogger(__name__)


class TrayUI(QtWidgets.QSystemTrayIcon):
    """
    System Tray Icon implementation.
    Receives signals from TrayController to update appearance.
    """

    def __init__(self, app: QtWidgets.QApplication, controller):
        super().__init__(parent=None)
        self._app = app
        self._controller = controller
        
        # Build the menu
        self._build_menu()
        
        # Set initial icon (offline/grey)
        self._update_display(False, "offline", (128, 128, 128), False)
        
        # Connect signals
        self._controller.state_updated.connect(self._update_display)
        
        # Show
        self.setVisible(True)

    def _build_menu(self):
        menu = QtWidgets.QMenu()

        # Status Label (Disabled action acting as a header)
        self._status_action = QtWidgets.QAction("LVA: Offline", self)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()

        # Service Controls
        start_action = QtWidgets.QAction("Start LVA", self)
        start_action.triggered.connect(lambda: self._controller.control_service("start"))
        menu.addAction(start_action)

        stop_action = QtWidgets.QAction("Stop LVA", self)
        stop_action.triggered.connect(lambda: self._controller.control_service("stop"))
        menu.addAction(stop_action)

        restart_action = QtWidgets.QAction("Restart LVA", self)
        restart_action.triggered.connect(lambda: self._controller.control_service("restart"))
        menu.addAction(restart_action)

        menu.addSeparator()

        # Mute Toggle
        self._mute_action = QtWidgets.QAction("Mute Microphone", self)
        self._mute_action.setCheckable(True)
        self._mute_action.triggered.connect(self._on_mute_toggled)
        menu.addAction(self._mute_action)

        menu.addSeparator()

        # Quit
        quit_action = QtWidgets.QAction("Quit Tray Client", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def _on_mute_toggled(self, checked: bool):
        self._controller.toggle_mute(checked)

    def _quit(self):
        self._controller.stop()
        self._app.quit()

    def _update_display(self, available: bool, state_name: str, color_rgb: RgbTuple, muted: bool):
        """Slot called when Controller emits a state change."""
        
        # 1. Update Menu State
        self._mute_action.setChecked(muted)
        
        # 2. Determine Display Text
        device_name = self._controller.get_device_name()
        if not available:
            status_text = "Offline"
            final_color = (128, 128, 128)
        else:
            status_text = state_name
            final_color = color_rgb

        if muted and available:
            status_text += " (Muted)"
            # Apply Red Tint
            r, g, b = final_color
            final_color = (
                min(255, r + 120),
                max(0, int(g * 0.4)),
                max(0, int(b * 0.4))
            )

        # 3. Update Tooltip and Menu Label
        full_text = f"{device_name} – {status_text}"
        self.setToolTip(full_text)
        self._status_action.setText(full_text)

        # 4. Draw Icon
        icon = self._draw_circle_icon(final_color)
        self.setIcon(icon)

    def _draw_circle_icon(self, rgb: RgbTuple) -> QtGui.QIcon:
        """Draw a colored circle icon dynamically."""
        size = 20
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # Border
        pen = QtGui.QPen(QtGui.QColor(0, 0, 0, 160))
        pen.setWidth(1)
        painter.setPen(pen)

        # Fill
        r, g, b = rgb
        painter.setBrush(QtGui.QBrush(QtGui.QColor(r, g, b)))

        radius = size // 2 - 2
        painter.drawEllipse(2, 2, radius * 2, radius * 2)
        painter.end()

        return QtGui.QIcon(pixmap)