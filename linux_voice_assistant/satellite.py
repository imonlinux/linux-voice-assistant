"""Voice satellite protocol."""

import logging
import time
from collections.abc import Iterable
from typing import Dict, Optional, Set, Union

# pylint: disable=no-name-in-module
from aioesphomeapi.api_pb2 import (  # type: ignore[attr-defined]
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
from .models import ServerState, SatelliteState
from .openwakeword import OpenWakeWord
from .util import call_all

_LOGGER = logging.getLogger(__name__)


class VoiceSatelliteProtocol(APIServer):
    def __init__(self, state: ServerState) -> None:
        super().__init__(state.name)

        self.state = state
        self.state.satellite = self

        self.media_player_entity = MediaPlayerEntity(
            server=self,
            state=state,
            key=len(state.entities),
            name="Media Player",
            object_id="linux_voice_assistant_media_player",
            music_player=state.music_player,
            announce_player=state.tts_player,
        )
        self.state.entities.append(self.media_player_entity)

        # State machine
        self._state: SatelliteState = SatelliteState.IDLE
        self._is_streaming_audio: bool = False # Is HA requesting audio?

        # Announce/TTS state
        self._tts_url: Optional[str] = None
        self._continue_conversation: bool = False
        self._timer_finished: bool = False

        # Flags set by events, processed in _determine_final_state
        self._run_end_received: bool = False
        self._tts_end_received: bool = False

    def _set_state(self, new_state: SatelliteState):
        """Manages state transitions and publishes events."""
        if self._state == new_state:
            return

        _LOGGER.debug(f"State transition: {self._state} -> {new_state}")
        self._state = new_state

        # Publish events for LEDs, etc.
        if new_state == SatelliteState.IDLE:
            self.unduck()
            self.state.stop_word.is_active = False
            self.state.event_bus.publish("voice_idle")
        elif new_state == SatelliteState.LISTENING:
            self.duck()
            self.state.event_bus.publish("voice_listen")
        elif new_state == SatelliteState.THINKING:
            self.state.event_bus.publish("voice_thinking")
        elif new_state == SatelliteState.RESPONDING:
            self.state.stop_word.is_active = True
            self.state.event_bus.publish("voice_responding")
        elif new_state == SatelliteState.ERROR:
            self.state.event_bus.publish("voice_error")

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: Dict[str, str]
    ) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)

        if event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START:
            self._run_end_received = False
            self._tts_end_received = False
            self._tts_url = data.get("url")
            self._continue_conversation = False
            # DO NOT transition to THINKING here. Stay in LISTENING.
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_START:
            # This confirms we are in the LISTENING state
            self._set_state(SatelliteState.LISTENING)
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START:
            self.state.event_bus.publish("voice_vad_start", data)
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_END:
            self._is_streaming_audio = False
            # THIS is when we move to THINKING
            self._set_state(SatelliteState.THINKING)
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END:
            # We wait for STT_END before moving to THINKING
            pass

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END:
            if data.get("continue_conversation") == "1":
                self._continue_conversation = True

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START:
            self._set_state(SatelliteState.RESPONDING)

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self._tts_end_received = True
            self.play_tts() # Play the final TTS URL
            
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._run_end_received = True
            # If TTS is already done, or we're not speaking, we can finalize.
            if self._state != SatelliteState.RESPONDING:
                self._determine_final_state()

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_ERROR:
            self._set_state(SatelliteState.ERROR)
            # Use a timer to reset to idle after error
            self.state.loop.call_later(5.0, self._set_state, SatelliteState.IDLE)


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
            data: Dict[str, str] = {}
            for arg in msg.data:
                data[arg.name] = arg.value
            self.handle_voice_event(VoiceAssistantEventType(msg.event_type), data)
        elif isinstance(msg, VoiceAssistantAnnounceRequest):
            _LOGGER.debug("Announcing: %s", msg.text)
            urls = []
            if msg.preannounce_media_id:
                urls.append(msg.preannounce_media_id)
            urls.append(msg.media_id)
            self.state.stop_word.is_active = True
            self._continue_conversation = msg.start_conversation
            self.duck()
            self._set_state(SatelliteState.RESPONDING) # Announce is a response
            yield from self.media_player_entity.play(
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
            self._set_state(SatelliteState.IDLE) # Connected, move to idle
            self.state.event_bus.publish("ha_connected")
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
            _LOGGER.debug("Active wake words: %s", active_wake_words)
            self.state.preferences.active_wake_words = list(active_wake_words)
            self.state.save_preferences()
            self.state.wake_words_changed = True

    def handle_audio(self, audio_chunk: bytes) -> None:
        if not self._is_streaming_audio:
            return
        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self, wake_word: Union[MicroWakeWord, OpenWakeWord]) -> None:
        if self._state != SatelliteState.IDLE:
            return # Already awake
            
        if self._timer_finished:
            self._timer_finished = False
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
            return
            
        wake_word_phrase = wake_word.wake_word
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )
        self._set_state(SatelliteState.LISTENING)
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def stop(self) -> None:
        self.state.tts_player.stop()
        if self._timer_finished:
            self._timer_finished = False
            _LOGGER.debug("Stopping timer finished sound")
        else:
            _LOGGER.debug("TTS response stopped manually")
            self._tts_finished() # Manually trigger TTS finished logic

    def play_tts(self) -> None:
        if not self._tts_url:
            return

        _LOGGER.debug("Playing TTS response: %s", self._tts_url)
        self._set_state(SatelliteState.RESPONDING)
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)
        self._tts_url = None # Clear after starting playback

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _determine_final_state(self) -> None:
        """Called when both TTS and the pipeline run are finished."""
        if self._continue_conversation:
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            _LOGGER.debug("Continuing conversation")
            self._set_state(SatelliteState.LISTENING)
        else:
            self._set_state(SatelliteState.IDLE)
        
        _LOGGER.debug("Final state determined")

    def _tts_finished(self) -> None:
        """Callback when TTS audio finishes playing."""
        self.send_messages([VoiceAssistantAnnounceFinished()])
        _LOGGER.debug("TTS audio playback finished")

        # If the run already ended, we can move to the final state.
        # Otherwise, we wait for the RUN_END event.
        if self._run_end_received:
            self._determine_final_state()
        else:
            # We've finished speaking but the pipeline is still running
            # (e.g., waiting for 'continue_conversation')
            # Move to THINKING state
            if self._state == SatelliteState.RESPONDING:
                self._set_state(SatelliteState.THINKING)


    def _play_timer_finished(self) -> None:
        if not self._timer_finished:
            self.unduck()
            return
        self.state.tts_player.play(
            self.state.timer_finished_sound,
            done_callback=lambda: call_all(
                lambda: time.sleep(1.0), self._play_timer_finished
            ),
        )

    def connection_lost(self, exc):
        super().connection_lost(exc)
        self._set_state(SatelliteState.ERROR)
        _LOGGER.info("Disconnected from Home Assistant")
