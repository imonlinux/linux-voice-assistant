"""Shared models."""

import asyncio
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union

from .event_bus import EventBus

if TYPE_CHECKING:
    from pymicro_wakeword import MicroWakeWord
    from pyopen_wakeword import OpenWakeWord
    from .entity import ESPHomeEntity
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


def _clamp_0_1(name: str, value: float) -> float:
    """Clamp float to [0.0, 1.0] with warnings."""
    try:
        v = float(value)
    except Exception:
        _LOGGER.warning("%s is not a number (%r); ignoring", name, value)
        return 0.5

    if v < 0.0:
        _LOGGER.warning("%s < 0.0; clamping to 0.0 (was %s)", name, v)
        return 0.0
    if v > 1.0:
        _LOGGER.warning("%s > 1.0; clamping to 1.0 (was %s)", name, v)
        return 1.0
    return v


@dataclass
class AvailableWakeWord:
    id: str
    type: WakeWordType
    wake_word: str
    trained_languages: List[str]
    wake_word_path: Path

    # Optional per-model override for OpenWakeWord activation threshold.
    # If set, AudioEngine will prefer this over the global config threshold.
    oww_threshold: Optional[float] = None

    def load(self) -> "Union[MicroWakeWord, OpenWakeWord]":
        if self.type == WakeWordType.MICRO_WAKE_WORD:
            from pymicro_wakeword import MicroWakeWord
            return MicroWakeWord.from_config(config_path=self.wake_word_path)

        if self.type == WakeWordType.OPEN_WAKE_WORD:
            from pyopen_wakeword import OpenWakeWord

            oww_model = OpenWakeWord.from_model(model_path=self.wake_word_path)
            setattr(oww_model, "wake_word", self.wake_word)

            # Attach per-model threshold if configured
            if self.oww_threshold is not None:
                thr = _clamp_0_1(f"wakeword[{self.id}].threshold", self.oww_threshold)
                setattr(oww_model, "threshold", thr)

            return oww_model

        raise ValueError(f"Unexpected wake word type: {self.type}")


@dataclass
class Preferences:
    active_wake_words: List[str] = field(default_factory=list)
    volume_level: float = 1.0
    num_leds: int = 3
    # New: configurable alarm duration in seconds.
    # 0 = infinite alarm (only Stop/wake word stops it)
    # >0 = auto-stop alarm after this many seconds
    alarm_duration_seconds: int = 0


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
    preferences: Preferences

    # --- Fields WITH default values ---
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    mic_muted: bool = False
    shutdown: bool = False

    # Threading event to pause the audio thread efficiently when muted
    # set() = Mic is ON (Audio processing running)
    # clear() = Mic is OFF (Audio processing paused)
    mic_muted_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self):
        """Ensure the threading event matches the boolean state on init."""
        if not self.mic_muted:
            self.mic_muted_event.set()
        else:
            self.mic_muted_event.clear()

    def save_preferences(self) -> None:
        """Save preferences as JSON."""
        _LOGGER.debug("Saving preferences: %s", self.preferences_path)
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.preferences_path, "w", encoding="utf-8") as preferences_file:
            json.dump(
                asdict(self.preferences),
                preferences_file,
                ensure_ascii=False,
                indent=4,
            )
