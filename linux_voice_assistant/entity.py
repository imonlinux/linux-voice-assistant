from abc import abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Callable, List, Optional, Union

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
    ListEntitiesMediaPlayerResponse,
    ListEntitiesRequest,
    ListEntitiesSwitchResponse, # ADDED
    MediaPlayerCommandRequest,
    MediaPlayerStateResponse,
    SubscribeHomeAssistantStatesRequest,
    SwitchCommandRequest, # ADDED
    SwitchStateResponse, # ADDED
)
from aioesphomeapi.model import MediaPlayerCommand, MediaPlayerState
from google.protobuf import message

from .api_server import APIServer
from .mpv_player import MpvMediaPlayer
from .util import call_all

if TYPE_CHECKING:
    from .models import ServerState


class ESPHomeEntity:
    def __init__(self, server: APIServer, state: "ServerState") -> None:
        self.server = server
        self.state = state

    @abstractmethod
    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        pass


# -----------------------------------------------------------------------------
# --- ADDED MIC MUTE SWITCH ENTITY ---
# -----------------------------------------------------------------------------

class MicMuteSwitchEntity(ESPHomeEntity):
    def __init__(
        self,
        server: APIServer,
        state: "ServerState",
        key: int,
        name: str,
        object_id: str,
    ) -> None:
        super().__init__(server, state)
        self.key = key
        self.name = name
        self.object_id = object_id
        self.is_on = False  # Mute is OFF by default

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, ListEntitiesRequest):
            yield ListEntitiesSwitchResponse(
                object_id=self.object_id,
                key=self.key,
                name=self.name,
                icon="mdi:microphone-off", # Show a mic-off icon in HA
            )
        elif isinstance(msg, SwitchCommandRequest) and (msg.key == self.key):
            self.is_on = msg.state
            self.state.event_bus.publish("set_mic_mute", {"state": self.is_on})
            yield self._get_state_message()
        elif isinstance(msg, SubscribeHomeAssistantStatesRequest):
            yield self._get_state_message()

    def _get_state_message(self) -> SwitchStateResponse:
        return SwitchStateResponse(key=self.key, state=self.is_on)


# -----------------------------------------------------------------------------


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
        self.volume = state.preferences.volume_level
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
                self.music_player.pause()
                self.announce_player.play(
                    url,
                    done_callback=lambda: call_all(
                        self.music_player.resume, done_callback
                    ),
                )
            else:
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
            if msg.has_volume:
                self.volume = msg.volume
                volume_int = int(self.volume * 100)
                self.music_player.set_volume(volume_int)
                self.announce_player.set_volume(volume_int)

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
