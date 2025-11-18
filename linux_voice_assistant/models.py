"""Shared models."""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union

from .event_bus import EventBus

if TYPE_CHECKING:
    from pymicro_wakeword import MicroWakeWord
    from pyopen_wakeword import OpenWakeWord
    from .entity import ESPHomeEntity, MediaPlayerEntity
    from .mpv_player import MpvMediaPlayer
    from .satellite import VoiceSatelliteProtocol

_LOGGER = logging.getLogger(__name__)


class SatelliteState(str, Enum):
    """Voice satellite state."""
    STARTING = "starting"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    RESPONDING = "responding"
    ERROR = "error"


class WakeWordType(str, Enum):
    MICRO_WAKE_WORD = "micro"
    OPEN_WAKE_WORD = "openWakeWord"


@dataclass
class AvailableWakeWord:
    id: str
    type: WakeWordType
    wake_word: str
    trained_languages: List[str]
    wake_word_path: Path

    def load(self) -> "Union[MicroWakeWord, OpenWakeWord]":
        if self.type == WakeWordType.MICRO_WAKE_WORD:
            from pymicro_wakeword import MicroWakeWord
            return MicroWakeWord.from_config(config_path=self.wake_word_path)

        if self.type == WakeWordType.OPEN_WAKE_WORD:
            from pyopen_wakeword import OpenWakeWord
            
            oww_model = OpenWakeWord.from_model(model_path=self.wake_word_path)
            setattr(oww_model, "wake_word", self.wake_word)
            return oww_model

        raise ValueError(f"Unexpected wake word type: {self.type}")


@dataclass
class Preferences:
    active_wake_words: List[str] = field(default_factory=list)
    volume_level: float = 1.0
    num_leds: int = 3


@dataclass
class ServerState:
    """A simple dataclass to hold core application state."""
    # --- Fields WITHOUT default values ---
    name: str
    mac_address: str
    event_bus: EventBus
    loop: asyncio.AbstractEventLoop
    entities: "List[ESPHomeEntity]"
    music_player: "MpvMediaPlayer"
    tts_player: "MpvMediaPlayer"
    available_wake_words: "Dict[str, AvailableWakeWord]"
    wake_words: "Dict[str, Union[MicroWakeWord, OpenWakeWord]]"
    active_wake_words: Set[str]
    stop_word: "MicroWakeWord"
    wakeup_sound: str
    timer_finished_sound: str
    preferences_path: Path
    download_dir: Path
    preferences: Preferences  # <-- MOVED UP
    
    # --- Fields WITH default values ---
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    mic_muted: bool = False

    def save_preferences(self) -> None:
        """Save preferences as JSON."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(
                asdict(self.preferences), preferences_file, ensure_ascii=False, indent=4
            )
