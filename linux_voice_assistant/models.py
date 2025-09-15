"""Shared models."""

from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .entity import ESPHomeEntity, MediaPlayerEntity
    from .microwakeword import MicroWakeWord
    from .mpv_player import MpvMediaPlayer
    from .satellite import VoiceSatelliteProtocol


@dataclass
class AvailableWakeWord:
    id: str
    wake_word: str
    trained_languages: List[str]
    config_path: Path


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
    media_player_entity: "Optional[MediaPlayerEntity]" = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None
