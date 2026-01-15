#!/usr/bin/env python3
"""
LVA Tray Client Entry Point
"""

import argparse
import logging
import sys
from pathlib import Path

from PyQt5 import QtWidgets

from ..config import Config, load_config_from_json
from .controller import TrayController
from .ui import TrayUI

_LOGGER = logging.getLogger("lva_tray_client")


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

    # Initialize Qt Application
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Initialize Controller (Business Logic)
    controller = TrayController(config)
    
    # Initialize UI (View)
    tray_ui = TrayUI(app, controller)  # noqa: F841
    
    # Start Logic
    controller.start()

    # Start Event Loop
    exit_code = app.exec_()
    
    # Cleanup
    controller.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()