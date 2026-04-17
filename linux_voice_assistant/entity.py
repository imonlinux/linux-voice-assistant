"""ESPHome entities for the Linux Voice Assistant.

This module defines the entity classes that expose LVA controls on the
Home Assistant device page via the ESPHome native API — no MQTT required.

Architecture notes:
  - ``ESPHomeEntity`` is the abstract base class.  It keeps the ``state``
    parameter for backward compatibility with ``MediaPlayerEntity`` which
    accesses ``ServerState`` broadly.
  - New entities follow upstream's *callback* pattern: they accept
    getter/setter callables in their constructor so they don't need a
    direct ``ServerState`` reference.
  - The protobuf imports below cover switch, select, and number entity
    types for current and future entity classes.
"""

from abc import abstractmethod
from collections.abc import Iterable
import logging
from typing import TYPE_CHECKING, Callable, List, Optional, Union

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    # --- Media player ---
    ListEntitiesMediaPlayerResponse,
    ListEntitiesRequest,
    MediaPlayerCommandRequest,
    MediaPlayerStateResponse,
    SubscribeHomeAssistantStatesRequest,
    # --- Switch entities ---
    ListEntitiesSwitchResponse,
    SwitchCommandRequest,
    SwitchStateResponse,
    # --- Select entities ---
    ListEntitiesSelectResponse,
    SelectCommandRequest,
    SelectStateResponse,
    # --- Number entities ---
    ListEntitiesNumberResponse,
    NumberCommandRequest,
    NumberStateResponse,
)
from aioesphomeapi.model import (
    EntityCategory,
    MediaPlayerCommand,
    MediaPlayerState,
)
from google.protobuf import message

from .api_server import APIServer
from .mpv_player import MpvMediaPlayer
from .util import call_all

if TYPE_CHECKING:
    from .models import ServerState

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ESPHomeEntity:
    """Abstract base for all ESPHome entities.

    Subclasses must implement ``handle_message`` which receives every
    routed protobuf message and yields zero or more response messages.
    """

    def __init__(self, server: APIServer, state: "ServerState") -> None:
        self.server = server
        self.state = state

    @abstractmethod
    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        """Process *msg* and yield any response messages."""


# ---------------------------------------------------------------------------
# Media Player entity (existing — unchanged)
# ---------------------------------------------------------------------------

class MediaPlayerEntity(ESPHomeEntity):
    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        music_player: MpvMediaPlayer,
        announce_player: MpvMediaPlayer,
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self.state_enum = MediaPlayerState.IDLE
        self.volume = state.preferences.volume_level  # Initialize with saved volume
        self.muted = False
        self.music_player = music_player
        self.announce_player = announce_player

    def play(
        self,
        url: Union[str, List[str]],
        announcement: bool = False,
        done_callback: Optional[Callable[[], None]] = None,
    ) -> Iterable[message.Message]:
        if announcement:
            if self.music_player.is_playing:
                # Announce, resume music
                self.music_player.pause()
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.music_player.resume, done_callback
                    ),
                )
            else:
                # Announce, idle
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.server.send_messages(
                            [self._update_state(MediaPlayerState.IDLE)]
                        ),
                        done_callback,
                    ),
                )
        else:
            # Music
            self.music_player.play(
                url,
                done_callback=lambda: call_all(
                    self.server.send_messages(
                        [self._update_state(MediaPlayerState.IDLE)]
                    ),
                    done_callback,
                ),
            )

        yield self._update_state(MediaPlayerState.PLAYING)

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, MediaPlayerCommandRequest) and (msg.key == self.key):
            if msg.has_media_url:
                announcement = msg.has_announcement and msg.announcement
                yield from self.play(msg.media_url, announcement=announcement)

            elif msg.has_command:
                if msg.command == MediaPlayerCommand.PAUSE:
                    self.music_player.pause()
                    yield self._update_state(MediaPlayerState.PAUSED)

                elif msg.command == MediaPlayerCommand.PLAY:
                    self.music_player.resume()
                    yield self._update_state(MediaPlayerState.PLAYING)

                elif msg.command == MediaPlayerCommand.STOP:
                    self.music_player.stop()
                    yield self._update_state(MediaPlayerState.IDLE)

            if msg.has_volume:
                # This block is called when the volume slider changes in HA
                self.volume = msg.volume  # HA sends volume as 0.0-1.0
                volume_int = int(self.volume * 100)
                self.music_player.set_volume(volume_int)
                self.announce_player.set_volume(volume_int)

                # Save the new volume level to preferences
                self.state.preferences.volume_level = self.volume
                self.state.save_preferences()

                yield self._update_state(self.state_enum)

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesMediaPlayerResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                supports_pause=True,
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield self._get_state_message()

    def _update_state(self, new_state: MediaPlayerState) -> MediaPlayerStateResponse:
        self.state_enum = new_state
        return self._get_state_message()

    def _get_state_message(self) -> MediaPlayerStateResponse:
        return MediaPlayerStateResponse(
            key=self.key,
            state=self.state_enum,
            volume=self.volume,
            muted=self.muted,
        )


# ---------------------------------------------------------------------------
# Mute Switch entity (Phase 2 — ported from upstream)
# ---------------------------------------------------------------------------

class MuteSwitchEntity(ESPHomeEntity):
    """ESPHome switch entity for microphone mute.

    Uses the callback pattern: getter/setter callables are provided by
    the satellite at construction time so this entity has no direct
    dependency on ``ServerState`` fields.

    When HA toggles the switch, the setter callback fires the EventBus
    ``set_mic_mute`` event so ``MicMuteHandler`` remains the single
    writer to ``ServerState.mic_muted``.
    """

    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        get_muted: Callable[[], bool],
        set_muted: Callable[[bool], None],
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self._get_muted = get_muted
        self._set_muted = set_muted

    @property
    def _switch_state(self) -> bool:
        return self._get_muted()

    def sync_state_to_ha(self) -> None:
        """Push the current mute state to HA.

        Called by ``MicMuteHandler`` after mute changes from non-ESPHome
        sources (hardware button, XVF3800, MQTT) so HA stays in sync.
        """
        self.server.send_messages(
            [SwitchStateResponse(key=self.key, state=self._switch_state)]
        )

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SwitchCommandRequest) and (msg.key == self.key):
            self._set_muted(msg.state)
            yield SwitchStateResponse(key=self.key, state=self._switch_state)

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSwitchResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                icon="mdi:microphone-off",
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield SwitchStateResponse(key=self.key, state=self._switch_state)


# ---------------------------------------------------------------------------
# Thinking Sound Loop switch entity (Phase 2 — adapted from upstream)
# ---------------------------------------------------------------------------

class ThinkingSoundSwitchEntity(ESPHomeEntity):
    """ESPHome switch entity for the thinking sound loop toggle.

    Upstream's ``ThinkingSoundEntity`` toggles ``thinking_sound_enabled``
    (a simple on/off for the thinking sound).  This fork's equivalent
    controls ``thinking_sound_loop`` — whether the thinking sound repeats
    during the THINKING state.  The semantics are slightly different but
    the ESPHome entity pattern is identical.

    Uses the callback pattern for state access.
    """

    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        get_enabled: Callable[[], bool],
        set_enabled: Callable[[bool], None],
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self._get_enabled = get_enabled
        self._set_enabled = set_enabled

    @property
    def _switch_state(self) -> bool:
        return self._get_enabled()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SwitchCommandRequest) and (msg.key == self.key):
            self._set_enabled(msg.state)
            yield SwitchStateResponse(key=self.key, state=self._switch_state)

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSwitchResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                icon="mdi:thought-bubble-outline",
                entity_category=EntityCategory.CONFIG,
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield SwitchStateResponse(key=self.key, state=self._switch_state)


# ---------------------------------------------------------------------------
# Event Sounds switch entity (Phase 3 — fork-specific)
# ---------------------------------------------------------------------------

class EventSoundsSwitchEntity(ESPHomeEntity):
    """ESPHome switch entity for the event sounds master toggle.

    Controls ``ServerState.event_sounds_enabled`` — when disabled, wakeup
    and thinking sounds are suppressed.  The timer alarm is NOT affected
    (it always plays as a functional alert).

    Uses the callback pattern for state access.
    """

    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        get_enabled: Callable[[], bool],
        set_enabled: Callable[[bool], None],
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self._get_enabled = get_enabled
        self._set_enabled = set_enabled

    @property
    def _switch_state(self) -> bool:
        return self._get_enabled()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SwitchCommandRequest) and (msg.key == self.key):
            self._set_enabled(msg.state)
            yield SwitchStateResponse(key=self.key, state=self._switch_state)

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSwitchResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                icon="mdi:volume-off",
                entity_category=EntityCategory.CONFIG,
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield SwitchStateResponse(key=self.key, state=self._switch_state)


# ---------------------------------------------------------------------------
# Sound Select entity (Phase 3 — fork-specific, reusable)
# ---------------------------------------------------------------------------

class SoundSelectEntity(ESPHomeEntity):
    """ESPHome select entity for choosing a sound file.

    A single reusable class for wakeup, thinking, and timer sound
    selection.  Each instance is constructed with its own key, name,
    options list, and getter/setter callbacks.

    The ``instance_id`` field distinguishes multiple SoundSelectEntity
    instances during entity lifecycle lookups (since ``_setup_entity``
    finds by type).
    """

    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        icon: str,
        instance_id: str,
        options: List[str],
        get_selection: Callable[[], str],
        set_selection: Callable[[str], None],
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self.icon = icon
        self.instance_id = instance_id
        self.options = options
        self._get_selection = get_selection
        self._set_selection = set_selection

    @property
    def _current_state(self) -> str:
        return self._get_selection()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SelectCommandRequest) and (msg.key == self.key):
            if msg.state in self.options:
                self._set_selection(msg.state)
            else:
                _LOGGER.warning(
                    "SoundSelectEntity '%s': unknown option '%s'",
                    self.instance_id, msg.state,
                )
            yield SelectStateResponse(
                key=self.key, state=self._current_state, missing_state=False,
            )

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSelectResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                options=self.options,
                entity_category=EntityCategory.CONFIG,
                icon=self.icon,
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield SelectStateResponse(
                key=self.key, state=self._current_state, missing_state=False,
            )


# ---------------------------------------------------------------------------
# Alarm Duration number entity (Phase 3 — fork-specific)
# ---------------------------------------------------------------------------

class AlarmDurationNumberEntity(ESPHomeEntity):
    """ESPHome number entity for timer alarm auto-stop duration.

    Exposes ``alarm_duration_seconds`` as a number entity on the HA
    device page.

    Semantics:
        0  = infinite alarm (only Stop/wake word stops it)
        >0 = auto-stop alarm after this many seconds

    Uses the callback pattern for state access.
    """

    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        get_value: Callable[[], float],
        set_value: Callable[[float], None],
        min_value: float = 0.0,
        max_value: float = 3600.0,
        step: float = 1.0,
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self._get_value = get_value
        self._set_value = set_value
        self.min_value = min_value
        self.max_value = max_value
        self.step = step

    @property
    def _current_value(self) -> float:
        return self._get_value()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, NumberCommandRequest) and (msg.key == self.key):
            value = max(self.min_value, min(self.max_value, msg.state))
            self._set_value(value)
            yield NumberStateResponse(
                key=self.key, state=self._current_value, missing_state=False,
            )

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesNumberResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                icon="mdi:timer",
                entity_category=EntityCategory.CONFIG,
                min_value=self.min_value,
                max_value=self.max_value,
                step=self.step,
                unit_of_measurement="s",
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield NumberStateResponse(
                key=self.key, state=self._current_value, missing_state=False,
            )
            
# ---------------------------------------------------------------------------
# Wake Word Sensitivity select entity (Phase 4 — ported from upstream PR #207)
# ---------------------------------------------------------------------------

class WakeWordSensitivityEntity(ESPHomeEntity):
    """ESPHome select entity for wake word sensitivity preset.

    Exposes a dropdown on the HA device page with three sensitivity
    levels.  When changed, the satellite adjusts MWW probability_cutoff
    and OWW global threshold accordingly.

    Per-model OWW thresholds (from the model JSON) are unaffected —
    they take precedence over the global threshold via the existing
    ``getattr(wake_word, "threshold", self.oww_threshold)`` fallback
    in AudioEngine.

    Uses the callback pattern for state access.
    """

    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
        options: List[str],
        get_sensitivity: Callable[[], str],
        set_sensitivity: Callable[[str], None],
    ) -> None:
        super().__init__(server, state)

        self.key = key
        self.name = name
        self.object_id = object_id
        self.options = options
        self._get_sensitivity = get_sensitivity
        self._set_sensitivity = set_sensitivity

    @property
    def _current_state(self) -> str:
        return self._get_sensitivity()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, SelectCommandRequest) and (msg.key == self.key):
            if msg.state in self.options:
                self._set_sensitivity(msg.state)
            else:
                _LOGGER.warning(
                    "WakeWordSensitivityEntity: unknown option '%s'", msg.state,
                )
            yield SelectStateResponse(
                key=self.key, state=self._current_state, missing_state=False,
            )

        elif isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSelectResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                options=self.options,
                entity_category=EntityCategory.CONFIG,
                icon="mdi:microphone-settings",
            )

        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield SelectStateResponse(
                key=self.key, state=self._current_state, missing_state=False,
            )
