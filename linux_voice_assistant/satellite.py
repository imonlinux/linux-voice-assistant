"""Voice satellite protocol."""

import logging
import time
from collections.abc import Iterable
from typing import Dict, Optional, Set, Union

from aioesphomeapi.api_pb2 import (
    DeviceInfoRequest,
    DeviceInfoResponse,
    ListEntitiesDoneResponse,
    ListEntitiesRequest,
    MediaPlayerCommandRequest,
    SubscribeHomeAssistantStatesRequest,
    VoiceAssistantAnnounceFinished,
    VoiceAssistantAnnounceRequest,
    VoiceAssistantAudio,
    VoiceAssistantConfigurationRequest,
    VoiceAssistantConfigurationResponse,
    VoiceAssistantEventResponse,
    VoiceAssistantRequest,
    VoiceAssistantSetConfiguration,
    VoiceAssistantTimerEventResponse,
    VoiceAssistantWakeWord,
)
from aioesphomeapi.model import (
    VoiceAssistantEventType,
    VoiceAssistantFeature,
    VoiceAssistantTimerEventType,
)
from google.protobuf import message

from .api_server import APIServer
from .entity import MediaPlayerEntity
from .microwakeword import MicroWakeWord
from .models import ServerState
from .openwakeword import OpenWakeWord
from .util import call_all

_LOGGER = logging.getLogger(__name__)


class VoiceSatelliteProtocol(APIServer):
    def __init__(self, state: ServerState) -> None:
        super().__init__(state.name)

        self.state = state
        self.state.satellite = self

        if self.state.media_player_entity is None:
            self.state.media_player_entity = MediaPlayerEntity(
                server=self,
                key=len(state.entities),
                name="Media Player",
                object_id="linux_voice_assistant_media_player",
                music_player=state.music_player,
                announce_player=state.tts_player,
            )
            self.state.entities.append(self.state.media_player_entity)

        self._is_streaming_audio = False
        self._tts_url: Optional[str] = None
        self._tts_played = False
        self._continue_conversation = False
        self._timer_finished = False

        if hasattr(self.state, "event_bus"):
            self.state.event_bus.publish("ready", {})

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: Dict[str, str]
    ) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)
        # LED notifies only triggered by wake/tts stages

    def handle_timer_event(
        self,
        event_type: VoiceAssistantTimerEventType,
        msg: VoiceAssistantTimerEventResponse,
    ) -> None:
        _LOGGER.debug("Timer event: type=%s", event_type.name)
        if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
            if not self._timer_finished:
                self.state.stop_word.is_active = True
                self._timer_finished = True
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, VoiceAssistantEventResponse):
            data: Dict[str, str] = {arg.name: arg.value for arg in msg.data}
            self.handle_voice_event(VoiceAssistantEventType(msg.event_type), data)
        elif isinstance(msg, VoiceAssistantAnnounceRequest):
            _LOGGER.debug("Announcing: %s", msg.text)
            assert self.state.media_player_entity is not None
            urls = []
            if msg.preannounce_media_id:
                urls.append(msg.preannounce_media_id)
            urls.append(msg.media_id)
            self.state.stop_word.is_active = True
            self._continue_conversation = msg.start_conversation
            self.duck()
            yield from self.state.media_player_entity.play(
                urls, announcement=True, done_callback=self._tts_finished
            )
        elif isinstance(msg, VoiceAssistantTimerEventResponse):
            self.handle_timer_event(VoiceAssistantTimerEventType(msg.event_type), msg)
        elif isinstance(msg, DeviceInfoRequest):
            yield DeviceInfoResponse(
                uses_password=False,
                name=self.state.name,
                mac_address=self.state.mac_address,
                voice_assistant_feature_flags=(
                    VoiceAssistantFeature.VOICE_ASSISTANT
                    | VoiceAssistantFeature.API_AUDIO
                    | VoiceAssistantFeature.ANNOUNCE
                    | VoiceAssistantFeature.START_CONVERSATION
                    | VoiceAssistantFeature.TIMERS
                ),
            )
        elif isinstance(
            msg,
            (
                ListEntitiesRequest,
                SubscribeHomeAssistantStatesRequest,
                MediaPlayerCommandRequest,
            ),
        ):
            for entity in self.state.entities:
                yield from entity.handle_message(msg)
            if isinstance(msg, ListEntitiesRequest):
                yield ListEntitiesDoneResponse()
        elif isinstance(msg, VoiceAssistantConfigurationRequest):
            yield VoiceAssistantConfigurationResponse(
                available_wake_words=[
                    VoiceAssistantWakeWord(
                        id=ww.id,
                        wake_word=ww.wake_word,
                        trained_languages=ww.trained_languages,
                    )
                    for ww in self.state.available_wake_words.values()
                ],
                active_wake_words=[
                    ww.id for ww in self.state.wake_words.values() if ww.is_active
                ],
                max_active_wake_words=2,
            )
            _LOGGER.info("Connected to Home Assistant")
        elif isinstance(msg, VoiceAssistantSetConfiguration):
            active_wake_words: Set[str] = set()
            for wake_word_id in msg.active_wake_words:
                if wake_word_id in self.state.wake_words:
                    active_wake_words.add(wake_word_id)
                    continue
                model_info = self.state.available_wake_words.get(wake_word_id)
                if not model_info:
                    continue
                _LOGGER.debug("Loading wake word: %s", model_info.config_path)
                self.state.wake_words[wake_word_id] = model_info.load(
                    self.state.libtensorflowlite_c_path
                )
                _LOGGER.info("Wake word set: %s", wake_word_id)
                active_wake_words.add(wake_word_id)
                break
            for wake_word in self.state.wake_words.values():
                wake_word.is_active = wake_word.id in active_wake_words
            self.state.preferences.active_wake_words = list(active_wake_words)
            self.state.save_preferences()
            self.state.wake_words_changed = True

    def handle_audio(self, audio_chunk: bytes) -> None:
        if not self._is_streaming_audio:
            return
        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self, wake_word: Union[MicroWakeWord, OpenWakeWord]) -> None:
        if self._timer_finished:
            self._timer_finished = False
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
            return

        wake_word_phrase = wake_word.wake_word
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)

        if hasattr(self.state, "event_bus"):
            self.state.event_bus.publish("voice_wakeword", {"wake_word_phrase": wake_word_phrase})

        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )
        self.duck()
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def stop(self) -> None:
        self.state.stop_word.is_active = False
