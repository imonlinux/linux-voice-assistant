#!/usr/bin/env python3

import asyncio
import argparse
import logging
import threading
import time
from collections.abc import Iterable
from enum import Enum, auto
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, List, Union, Dict
from queue import Queue

from google.protobuf import message
import sounddevice as sd
from aioesphomeapi.api_pb2 import (
    DeviceInfoRequest,
    DeviceInfoResponse,
    ListEntitiesRequest,
    ListEntitiesDoneResponse,
    VoiceAssistantConfigurationRequest,
    VoiceAssistantConfigurationResponse,
    VoiceAssistantWakeWord,
    SubscribeVoiceAssistantRequest,
    VoiceAssistantEventResponse,
    VoiceAssistantRequest,
    VoiceAssistantAudio,
    ListEntitiesMediaPlayerResponse,
    MediaPlayerStateResponse,
    MediaPlayerCommandRequest,
    SubscribeHomeAssistantStatesRequest,
    VoiceAssistantAnnounceFinished,
    VoiceAssistantAnnounceRequest,
    VoiceAssistantTimerEventResponse,
)
from aioesphomeapi.model import (
    VoiceAssistantFeature,
    VoiceAssistantEventType,
    VoiceAssistantTimerEventType,
)

from .api_server import APIServer
from .microwakeword import MicroWakeWord
from .entity import ESPHomeEntity, MediaPlayerEntity
from .util import get_mac, call_all
from .mpv_player import MpvMediaPlayer

_LOGGER = logging.getLogger(__name__)


class Sound(Enum):
    WAKEUP = auto()
    TIMER_FINISHED = auto()


_DIR = Path(__file__).parent
_SOUNDS_DIR = _DIR / "sounds"

DEFAULT_SOUNDS: Dict[Sound, Path] = {
    Sound.WAKEUP: _SOUNDS_DIR / "wake_word_triggered.flac",
    Sound.TIMER_FINISHED: _SOUNDS_DIR / "timer_finished.flac",
}


@dataclass
class ServerState:
    name: str
    mac_address: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: List[ESPHomeEntity]
    wake_word: MicroWakeWord
    stop_word: MicroWakeWord
    music_player: MpvMediaPlayer
    tts_player: MpvMediaPlayer
    media_player_entity: Optional[MediaPlayerEntity] = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None


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
                object_id="hav_sat_media_player",
                music_player=state.music_player,
                announce_player=state.tts_player,
            )
            self.state.entities.append(self.state.media_player_entity)

        self._is_streaming_audio = False
        self._tts_url: Optional[str] = None
        self._tts_played = False
        self._continue_conversation = False
        self._timer_finished = False

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: Dict[str, str]
    ) -> None:
        _LOGGER.debug("%s: %s", event_type.name, data)

        if event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_START:
            self._tts_url = data.get("url")
            self._tts_played = False
            self._continue_conversation = False
        elif event_type in (
            VoiceAssistantEventType.VOICE_ASSISTANT_STT_VAD_END,
            VoiceAssistantEventType.VOICE_ASSISTANT_STT_END,
        ):
            self._is_streaming_audio = False
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_PROGRESS:
            if data.get("tts_start_streaming") == "1":
                # Start streaming early
                self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_INTENT_END:
            if data.get("continue_conversation") == "1":
                self._continue_conversation
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._is_streaming_audio = False
            if not self._tts_played:
                self._tts_finished()

        # TODO: handle error

    def handle_timer_event(
        self,
        event_type: VoiceAssistantTimerEventType,
        msg: VoiceAssistantTimerEventResponse,
    ) -> None:
        if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
            if not self._timer_finished:
                self.state.stop_word.is_active = True
                self._timer_finished = True
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        if isinstance(msg, VoiceAssistantEventResponse):
            # Pipeline event
            data: Dict[str, str] = {}
            for arg in msg.data:
                data[arg.name] = arg.value

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
                        id=self.state.wake_word.id,
                        wake_word=self.state.wake_word.wake_word,
                        trained_languages=self.state.wake_word.trained_languages,
                    )
                ],
                active_wake_words=[self.state.wake_word.id],
                max_active_wake_words=1,
            )
            _LOGGER.debug("Registered voice assistant")

    def handle_audio(self, audio_chunk: bytes) -> None:

        if not self._is_streaming_audio:
            return

        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self) -> None:
        if self._timer_finished:
            # Stop timer instead
            self._timer_finished = False
            return

        wake_word_phrase = self.state.wake_word.wake_word
        _LOGGER.debug("Detected wake word: %s", wake_word_phrase)
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )
        self.duck()
        self._is_streaming_audio = True
        self.state.tts_player.play(str(DEFAULT_SOUNDS[Sound.WAKEUP]))

    def stop(self) -> None:
        self.state.stop_word.is_active = False

        if self._timer_finished:
            self._timer_finished = False
            self.state.tts_player.stop()
        else:
            _LOGGER.debug("TTS response stopped manually")
            self.state.tts_player.stop()
            self._tts_finished()

    def play_tts(self) -> None:
        if (not self._tts_url) or self._tts_played:
            return

        self._tts_played = True
        _LOGGER.debug("Playing TTS response: %s", self._tts_url)

        self.state.stop_word.is_active = True
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _tts_finished(self) -> None:
        self.state.stop_word.is_active = False
        self.send_messages([VoiceAssistantAnnounceFinished()])

        if self._continue_conversation:
            self.send_messages([VoiceAssistantRequest(start=True)])
            self._is_streaming_audio = True
            _LOGGER.debug("Continuing conversation")
        else:
            self.unduck()

        _LOGGER.debug("TTS response finished")

    def _play_timer_finished(self) -> None:
        if not self._timer_finished:
            self.unduck()
            return

        self.state.tts_player.play(
            str(DEFAULT_SOUNDS[Sound.TIMER_FINISHED]),
            done_callback=lambda: call_all(
                lambda: time.sleep(1.0), self._play_timer_finished
            ),
        )


def process_audio(state: ServerState):

    try:
        while True:
            audio_chunk = state.audio_queue.get()
            if audio_chunk is None:
                break

            if state.satellite is None:
                continue

            try:
                state.satellite.handle_audio(audio_chunk)

                if state.wake_word.is_active and state.wake_word.process_streaming(
                    audio_chunk
                ):
                    state.satellite.wakeup()

                if state.stop_word.is_active and state.stop_word.process_streaming(
                    audio_chunk
                ):
                    state.satellite.stop()
            except:
                _LOGGER.exception("Unexpected error handling audio")

    except:
        _LOGGER.exception("Unexpected error processing audio")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-input-device", default="default")
    parser.add_argument("--audio-output-device")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)

    state = ServerState(
        name="test",
        mac_address=get_mac(),
        audio_queue=Queue(),
        entities=[],
        wake_word=MicroWakeWord.from_config("wakewords/okay_nabu.json"),
        stop_word=MicroWakeWord.from_config("wakewords/stop.json"),
        music_player=MpvMediaPlayer(device=args.audio_output_device),
        tts_player=MpvMediaPlayer(device=args.audio_output_device),
    )

    process_audio_thread = threading.Thread(
        target=process_audio, args=(state,), daemon=True
    )
    process_audio_thread.start()

    def sd_callback(indata, frames, time, status):
        state.audio_queue.put_nowait(bytes(indata))

    loop = asyncio.get_running_loop()
    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), host="0.0.0.0", port=6053
    )

    try:
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=1024,
            device=args.audio_input_device,
            dtype="int16",
            channels=1,
            callback=sd_callback,
        ):
            async with server:
                _LOGGER.info("Server started")
                await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.audio_queue.put_nowait(None)
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())
