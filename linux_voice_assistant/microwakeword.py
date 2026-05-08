"""Stub module for pymicro_wakeword compatibility.

The actual MicroWakeWord functionality is provided by the external
pymicro_wakeword package. This module exists for test compatibility.
"""

# Re-export from the actual package
try:
    from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
except ImportError:
    # If package not available, provide stubs for testing
    class MicroWakeWord:
        """Stub for testing when pymicro_wakeword not available."""
        pass

    class MicroWakeWordFeatures:
        """Stub for testing when pymicro_wakeword not available."""
        pass