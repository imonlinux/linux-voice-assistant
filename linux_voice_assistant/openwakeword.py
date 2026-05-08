"""Stub module for pyopen_wakeword compatibility.

The actual OpenWakeWord functionality is provided by the external
pyopen_wakeword package. This module exists for test compatibility.
"""

# Re-export from the actual package
try:
    from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures
except ImportError:
    # If package not available, provide stubs for testing
    class OpenWakeWord:
        """Stub for testing when pyopen_wakeword not available."""
        pass

    class OpenWakeWordFeatures:
        """Stub for testing when pyopen_wakeword not available."""
        pass