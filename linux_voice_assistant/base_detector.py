from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Callable

@dataclass
class AvailableWakeWord:
    id: str
    wake_word: str
    trained_languages: List[str]
    config_path: Path

class BaseDetector(ABC):
    """Abstract base class for wake word detectors."""
    
    def __init__(
        self,
        wake_model_id: str,
        stop_model_id: str,
        **kwargs
    ):
        self.wake_model_id = wake_model_id
        self.stop_model_id = stop_model_id
        
        self.available_wake_words: Dict[str, AvailableWakeWord] = {}

        self._wake_active: bool = True
        self._stop_active: bool = False
        
        # Initialize detector-specific setup
        self._initialize(**kwargs)
    
    @abstractmethod
    def _initialize(self, **kwargs) -> None:
        """Initialize detector-specific setup."""
        pass
    
    @abstractmethod
    def connect_if_needed(self, on_detect: Callable[[str, Optional[int]], None]) -> None:
        """Connect to remote service if needed (e.g., Wyoming server)."""
        pass
    
    @abstractmethod
    def process_audio(self, audio_chunk: bytes) -> tuple[bool, bool]:
        """Process audio chunk.
        
        Returns:
            tuple[bool, bool]: (wake_detected, stop_detected)
        """
        pass
    
    @abstractmethod
    def set_wake_model(self, wake_word_id: str) -> bool:
        """Set the active wake word model.
        
        Returns:
            bool: True if successfully changed
        """
        pass

    @property
    def wake_active(self):
        return self._wake_active

    @wake_active.setter
    def wake_active(self, value: bool):
        self._wake_active = value

    @property
    def stop_active(self):
        return self._stop_active

    @stop_active.setter
    def stop_active(self, value: bool):
        self._stop_active = value if self.stop_model_id else False
    
    def get_active_wake_words(self) -> List[str]:
        """Get list of currently active wake word IDs."""
        return [self.wake_model_id] if self.wake_model_id else []