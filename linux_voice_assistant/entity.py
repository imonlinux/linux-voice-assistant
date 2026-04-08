"""ESPHome entities for the Linux Voice Assistant.

This module defines the entity classes that expose LVA controls on the
Home Assistant device page via the ESPHome native API — no MQTT required.

Architecture notes (Phase 1 — entity system foundation):
  - ``ESPHomeEntity`` is the abstract base class.  It keeps the ``state``
    parameter for backward compatibility with ``MediaPlayerEntity`` which
    accesses ``ServerState`` broadly.
  - New entities added in later phases (MuteSwitchEntity, SoundSelectEntity,
    etc.) should follow upstream's *callback* pattern instead: accept
    getter/setter callables in their constructor so they don't need a
    direct ``ServerState`` reference.
  - The protobuf imports below cover switch, select, and number entity
    types.  They are unused in Phase 1 but are required infrastructure
    for Phase 2+ entity classes.
"""

from abc import abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Callable, List, Optional, Union

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    # --- Media player (existing) ---
    ListEntitiesMediaPlayerResponse,
    ListEntitiesRequest,
    MediaPlayerCommandRequest,
    MediaPlayerStateResponse,
    SubscribeHomeAssistantStatesRequest,
    # --- Switch entities (Phase 2: mute, thinking sound, event sounds) ---
    ListEntitiesSwitchResponse,
    SwitchCommandRequest,
    SwitchStateResponse,
    # --- Select entities (Phase 3/4: sound selection, wake word sensitivity) ---
    ListEntitiesSelectResponse,
    SelectCommandRequest,
    SelectStateResponse,
    # --- Number entities (Phase 3: alarm duration) ---
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
