"""
LVA Tray Client

System tray integration for Linux Voice Assistant.
Provides visual feedback and control for LVA through:
- System tray icon with state-based colors
- Context menu for service control
- MQTT integration for mute and LED configuration

Optional dependency: PyQt5
Install with: pip install 'linux-voice-assistant[tray]'
"""

__all__ = ["main"]


def main(argv=None):
    """Entry point for tray client."""
    # Import here to provide better error message if PyQt5 not installed
    try:
        from PyQt5 import QtWidgets  # noqa: F401
    except ImportError as err:
        import sys
        print("Error: PyQt5 is not installed.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Install with:", file=sys.stderr)
        print("  pip install 'linux-voice-assistant[tray]'", file=sys.stderr)
        print("Or:", file=sys.stderr)
        print("  pip install PyQt5>=5.15", file=sys.stderr)
        sys.exit(1)
    
    # Import the actual main function from __main__
    from . import __main__ as main_module
    return main_module.main(argv)
