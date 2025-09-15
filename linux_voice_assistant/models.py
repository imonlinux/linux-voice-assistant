"""Shared models."""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .entity import ESPHomeEntity, MediaPlayerEntity
    from .microwakeword import MicroWakeWord
    from .mpv_player import MpvMediaPlayer
    from .satellite import VoiceSatelliteProtocol

_LOGGER = logging.getLogger(__name__)


@dataclass
class AvailableWakeWord:
    id: str
    wake_word: str
    trained_languages: List[str]
    config_path: Path


@dataclass
class Preferences:
    active_wake_words: List[str] = field(default_factory=list)


@dataclass
class ServerState:
    name: str
    mac_address: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: "List[ESPHomeEntity]"
    available_wake_words: "Dict[str, AvailableWakeWord]"
    wake_word: "MicroWakeWord"
    stop_word: "MicroWakeWord"
    music_player: "MpvMediaPlayer"
    tts_player: "MpvMediaPlayer"
    wakeup_sound: str
    timer_finished_sound: str
    preferences: Preferences
    preferences_path: Path
    media_player_entity: "Optional[MediaPlayerEntity]" = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None

    def save_preferences(self) -> None:
        """Save preferences as JSON."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(
                asdict(self.preferences), preferences_file, ensure_ascii=False, indent=4
            )
