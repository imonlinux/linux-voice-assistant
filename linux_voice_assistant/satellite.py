"""Voice satellite protocol."""

import asyncio
import functools
import hashlib
import logging
import posixpath
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Dict, Optional, Set, Union, List
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

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
    VoiceAssistantExternalWakeWord,
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
from pymicro_wakeword import MicroWakeWord
from pyopen_wakeword import OpenWakeWord

from .api_server import APIServer
from .entity import MediaPlayerEntity
from .models import AvailableWakeWord, ServerState, SatelliteState, WakeWordType
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
        self._state: SatelliteState = SatelliteState.STARTING
        self._is_streaming_audio: bool = False

        self._tts_url: Optional[str] = None
        self._continue_conversation: bool = False
        self._timer_finished: bool = False

        self._run_end_received: bool = False
        self._tts_end_received: bool = False
        
        self._external_wake_words: Dict[str, VoiceAssistantExternalWakeWord] = {}

    def _set_state(self, new_state: SatelliteState):
        if self._state == new_state:
            return

        _LOGGER.debug(f"State transition: {self._state} -> {new_state}")
        self._state = new_state

        if new_state == SatelliteState.IDLE:
            self.unduck()
            self.state.active_wake_words.discard(self.state.stop_word.id)
            self.state.event_bus.publish("voice_idle")
        elif new_state == SatelliteState.LISTENING:
            self.duck()
            self.state.event_bus.publish("voice_listen")
        elif new_state == SatelliteState.THINKING:
            self.state.event_bus.publish("voice_thinking")
        elif new_state == SatelliteState.RESPONDING:
            self.state.active_wake_words.add(self.state.stop_word.id)
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
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_START:
            self._set_state(SatelliteState.LISTENING)
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_START:
            self.state.event_bus.publish("voice_vad_start", data)
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_END:
            self._is_streaming_audio = False
            self._set_state(SatelliteState.THINKING)
        
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END:
            pass

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END:
            if data.get("continue_conversation") == "1":
                self._continue_conversation = True

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_START:
            self._set_state(SatelliteState.RESPONDING)

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self._tts_end_received = True
            self.play_tts()
            
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._run_end_received = True
            if self._state != SatelliteState.RESPONDING:
                self._determine_final_state()

        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_ERROR:
            self._set_state(SatelliteState.ERROR)
            self.state.loop.call_later(5.0, self._set_state, SatelliteState.IDLE)


    def handle_timer_event(
        self,
        event_type: VoiceAssistantTimerEventType,
        msg: VoiceAssistantTimerEventResponse,
    ) -> None:
        _LOGGER.debug("Timer event: type=%s", event_type.name)
        if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
            if not self._timer_finished:
                self.state.active_wake_words.add(self.state.stop_word.id)
                self._timer_finished = True
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        """
        Handles incoming messages from Home Assistant.
        Note: This method is synchronous. Long-running operations must be offloaded to Tasks.
        """
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
            self.state.active_wake_words.add(self.state.stop_word.id)
            self._continue_conversation = msg.start_conversation
            self.duck()
            self._set_state(SatelliteState.RESPONDING)
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
            available_wake_words = [
                VoiceAssistantWakeWord(
                    id=ww.id,
                    wake_word=ww.wake_word,
                    trained_languages=ww.trained_languages,
                )
                for ww in self.state.available_wake_words.values()
            ]

            self._external_wake_words.clear()
            for eww in msg.external_wake_words:
                if eww.model_type != "micro":
                    _LOGGER.warning("Skipping external wake word %s (type=%s)", eww.id, eww.model_type)
                    continue

                available_wake_words.append(
                    VoiceAssistantWakeWord(
                        id=eww.id,
                        wake_word=eww.wake_word,
                        trained_languages=eww.trained_languages,
                    )
                )
                self._external_wake_words[eww.id] = eww
            
            yield VoiceAssistantConfigurationResponse(
                available_wake_words=available_wake_words,
                active_wake_words=[
                    ww.id for ww in self.state.wake_words.values() 
                    if ww.id in self.state.active_wake_words
                ],
                max_active_wake_words=2,
            )
            
            _LOGGER.info("Connected to Home Assistant")
            self._set_state(SatelliteState.IDLE)
            self.state.event_bus.publish("ha_connected")
        
        elif isinstance(msg, VoiceAssistantSetConfiguration):
            # FIX: Offload to background task to prevent blocking the loop during download
            self.state.loop.create_task(self._handle_set_configuration_task(msg))
            # Yield nothing immediately; response will be sent asynchronously

    async def _handle_set_configuration_task(self, msg: VoiceAssistantSetConfiguration) -> None:
        """Asynchronous handler for SetConfiguration to avoid blocking I/O."""
        active_wake_words: Set[str] = set()
        
        for wake_word_id in msg.active_wake_words:
            if wake_word_id in self.state.wake_words:
                active_wake_words.add(wake_word_id)
                continue
                
            model_info = self.state.available_wake_words.get(wake_word_id)
            
            if not model_info:
                external_wake_word = self._external_wake_words.get(wake_word_id)
                if not external_wake_word:
                    _LOGGER.warning("Unknown wake word: %s", wake_word_id)
                    continue

                # Await the non-blocking download
                model_info = await self._download_external_wake_word(external_wake_word)
                if not model_info:
                    continue

                self.state.available_wake_words[wake_word_id] = model_info

            _LOGGER.debug("Loading wake word: %s", model_info.wake_word_path)
            self.state.wake_words[wake_word_id] = model_info.load()
            
            _LOGGER.info("Wake word set: %s", wake_word_id)
            active_wake_words.add(wake_word_id)
            break
        
        self.state.active_wake_words = active_wake_words
        _LOGGER.debug("Active wake words: %s", active_wake_words)
        
        self.state.preferences.active_wake_words = list(active_wake_words)
        self.state.save_preferences()
        self.state.wake_words_changed = True

    def handle_audio(self, audio_chunk: bytes) -> None:
        if self._is_streaming_audio:
            self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self, wake_word: Union[MicroWakeWord, OpenWakeWord]) -> None:
        if self._state not in (SatelliteState.IDLE, SatelliteState.STARTING):
            return
            
        if self._timer_finished:
            self._timer_finished = False
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
            return
            
        wake_word_phrase = getattr(wake_word, "wake_word", "wake word")
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )
        self._set_state(SatelliteState.LISTENING)
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def stop(self) -> None:
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self.state.tts_player.stop()
        if self._timer_finished:
            self._timer_finished = False
            _LOGGER.debug("Stopping timer finished sound")
        else:
            _LOGGER.debug("TTS response stopped manually")
            self._tts_finished()

    def play_tts(self) -> None:
        if not self._tts_url:
            return

        _LOGGER.debug("Playing TTS response: %s", self._tts_url)
        self.state.active_wake_words.add(self.state.stop_word.id)
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)
        self._tts_url = None

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _determine_final_state(self) -> None:
        if self._continue_conversation:
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            _LOGGER.debug("Continuing conversation")
            self._set_state(SatelliteState.LISTENING)
        else:
            self._set_state(SatelliteState.IDLE)
        
        _LOGGER.debug("Final state determined")

    def _tts_finished(self) -> None:
        self.state.active_wake_words.discard(self.state.stop_word.id)
        self.send_messages([VoiceAssistantAnnounceFinished()])
        _LOGGER.debug("TTS audio playback finished")

        if self._run_end_received:
            self._determine_final_state()
        else:
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

    async def _download_external_wake_word(
        self, external_wake_word: VoiceAssistantExternalWakeWord
    ) -> Optional[AvailableWakeWord]:
        """Wrapper to run the blocking download in a thread executor."""
        return await self.state.loop.run_in_executor(
            None, 
            functools.partial(self._download_external_wake_word_sync, external_wake_word)
        )

    def _download_external_wake_word_sync(
        self, external_wake_word: VoiceAssistantExternalWakeWord
    ) -> Optional[AvailableWakeWord]:
        """Blocking download logic, intended to run in an executor."""
        eww_dir = self.state.download_dir / "external_wake_words"
        eww_dir.mkdir(parents=True, exist_ok=True)

        config_path = eww_dir / f"{external_wake_word.id}.json"
        should_download_config = not config_path.exists()

        model_path = eww_dir / f"{external_wake_word.id}.tflite"
        should_download_model = True
        if model_path.exists():
            model_size = model_path.stat().st_size
            if model_size == external_wake_word.model_size:
                with open(model_path, "rb") as model_file:
                    model_hash = hashlib.sha256(model_file.read()).hexdigest()

                if model_hash == external_wake_word.model_hash:
                    should_download_model = False
                    _LOGGER.debug(
                        "Model size and hash match for %s. Skipping download.",
                        external_wake_word.id,
                    )

        if should_download_config or should_download_model:
            _LOGGER.debug("Downloading %s to %s", external_wake_word.url, config_path)
            try:
                with urlopen(external_wake_word.url, timeout=10) as request:
                    if request.status != 200:
                        _LOGGER.warning(
                            "Failed to download: %s, status=%s",
                            external_wake_word.url,
                            request.status,
                        )
                        return None

                    with open(config_path, "wb") as model_file:
                        shutil.copyfileobj(request, model_file)
            except Exception as e:
                 _LOGGER.error("Exception downloading config: %s", e)
                 return None

        if should_download_model:
            parsed_url = urlparse(external_wake_word.url)
            parsed_url = parsed_url._replace(
                path=posixpath.join(posixpath.dirname(parsed_url.path), model_path.name)
            )
            model_url = urlunparse(parsed_url)

            _LOGGER.debug("Downloading %s to %s", model_url, model_path)
            try:
                with urlopen(model_url, timeout=10) as request:
                    if request.status != 200:
                        _LOGGER.warning(
                            "Failed to download: %s, status=%s", model_url, request.status
                        )
                        return None

                    with open(model_path, "wb") as model_file:
                        shutil.copyfileobj(request, model_file)
            except Exception as e:
                 _LOGGER.error("Exception downloading model: %s", e)
                 return None

        return AvailableWakeWord(
            id=external_wake_word.id,
            type=WakeWordType.MICRO_WAKE_WORD,
            wake_word=external_wake_word.wake_word,
            trained_languages=external_wake_word.trained_languages,
            wake_word_path=config_path,
        )

    def connection_lost(self, exc):
        super().connection_lost(exc)
        self._set_state(SatelliteState.ERROR)
        _LOGGER.info("Disconnected from Home Assistant")
