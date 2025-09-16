from .base_detector import BaseDetector
from .microwakeword import MicroWakeWordDetector
from .openwakeword import OpenWakeWordDetector

class DetectorFactory:
    """Factory for creating wake word detectors."""
    
    @staticmethod
    def create_detector(
        detector_type: str,
        wake_model: str,
        stop_model: str,
        **kwargs
    ) -> BaseDetector:
        """Create a detector instance based on type.
        
        Args:
            detector_type: "mww" for MicroWakeWord or "oww" for OpenWakeWord
            wake_model: Wake word model identifier
            stop_model: Stop word model identifier
            **kwargs: Additional detector-specific arguments
            
        Returns:
            BaseDetector: Configured detector instance
        """
        detector_type = detector_type.lower()
        
        if detector_type == "mww":
            return MicroWakeWordDetector(
                wake_model_id=wake_model,
                stop_model_id=stop_model,
                **kwargs
            )
        elif detector_type == "oww":
            return OpenWakeWordDetector(
                wake_model_id=wake_model,
                stop_model_id=stop_model,
                **kwargs
            )
        else:
            raise ValueError(f"Unknown detector type: {detector_type}. Use 'mww' or 'oww'")