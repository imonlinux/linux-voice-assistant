#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import List, Literal, Optional

# pylint: disable=no-name-in-module
import sounddevice as sd
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
from .entity import ESPHomeEntity, MediaPlayerEntity
from .mpv_player import MpvMediaPlayer
from .util import call_all, get_mac, is_arm
from .event_bus import EventBus
from .event_led import LedEvent
from .detector_factory import DetectorFactory
from .base_detector import BaseDetector

_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_SOUNDS_DIR = _REPO_DIR / "sounds"
_CONFIG_DIR = _REPO_DIR / "config"

if is_arm():
    _LIB_DIR = _REPO_DIR / "lib" / "linux_arm64"
else:
    _LIB_DIR = _REPO_DIR / "lib" / "linux_amd64"

class WakeWordConfig:
    """Manages persistent storage of the wake word configuration."""
    def __init__(self, detector_type: Literal["mww","oww"], config_dir: Path):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(exist_ok=True)
        self.config_file = self.config_dir / "wake_word_config.json"
        self.detector_type = detector_type
    
    def save_wake_word(self, wake_word_id: str) -> None:
        """Save the current wake word ID to persistent storage."""
        try:
            config = {"detector": self.detector_type, "wake_word_id": wake_word_id}
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f)
            _LOGGER.debug("Saved wake word config: %s", wake_word_id)
        except Exception as e:
            _LOGGER.error("Failed to save wake word config: %s", e)
    
    def load_wake_word(self, fallback: str) -> str:
        """Load the last saved wake word ID from persistent storage."""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                wake_word_id = config.get("wake_word_id")
                detector_type = config.get("detector")

                # if the saved detector type doesn't match the current one, return the default fallback (e.g., from CLI)
                if detector_type != self.detector_type:
                    return fallback

                _LOGGER.debug("Loaded wake word config: %s and detector %s", wake_word_id, detector_type)
                return wake_word_id
        except Exception as e:
            _LOGGER.error("Failed to load wake word config: %s", e)
        return fallback

@dataclass
class ServerState:
    name: str
    mac_address: str
    audio_queue: "Queue[Optional[bytes]]"
    entities: List[ESPHomeEntity]
    detector: BaseDetector
    music_player: MpvMediaPlayer
    tts_player: MpvMediaPlayer
    wakeup_sound: str
    timer_finished_sound: str
    loop: asyncio.AbstractEventLoop
    event_bus: EventBus
    wake_word_config: WakeWordConfig
    media_player_entity: Optional[MediaPlayerEntity] = None
    satellite: "Optional[VoiceSatelliteProtocol]" = None


# -----------------------------------------------------------------------------


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

        self.state.event_bus.publish('ready', {})
        _LOGGER.info('System is ready!')

    def handle_voice_event(
        self, event_type: VoiceAssistantEventType, data: dict[str, str]
    ) -> None:
        _LOGGER.debug("Voice event: type=%s, data=%s", event_type.name, data)

        self.state.event_bus.publish(f'voice_{event_type.name}', data)

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
                self._continue_conversation = True
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_TTS_END:
            self._tts_url = data.get("url")
            self.play_tts()
        elif event_type == VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END:
            self._is_streaming_audio = False
            if not self._tts_played:
                self._tts_finished()

            self._tts_played = False

        # TODO: handle error

    def handle_timer_event(
        self,
        event_type: VoiceAssistantTimerEventType,
        msg: VoiceAssistantTimerEventResponse,
    ) -> None:
        _LOGGER.debug("Timer event: type=%s", event_type.name)
        if event_type == VoiceAssistantTimerEventType.VOICE_ASSISTANT_TIMER_FINISHED:
            if not self._timer_finished:
                self.state.detector.stop_active = True
                self._timer_finished = True
                self.duck()
                self._play_timer_finished()

    def handle_message(self, msg: message.Message) -> Iterable[message.Message]:
        _LOGGER.debug(f'message {msg.__name__}')
        if isinstance(msg, VoiceAssistantEventResponse):
            # Pipeline event
            data: dict[str, str] = {}
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

            self.state.detector.stop_active = True
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
        elif isinstance(msg, (
            ListEntitiesRequest,
            SubscribeHomeAssistantStatesRequest,
            MediaPlayerCommandRequest,
        ),):
            for entity in self.state.entities:
                yield from entity.handle_message(msg)

            if isinstance(msg, ListEntitiesRequest):
                yield ListEntitiesDoneResponse()
        elif isinstance(msg, VoiceAssistantConfigurationRequest):
            filtered_wake_words = list(filter(lambda x: x.id != self.state.detector.stop_model_id, self.state.detector.available_wake_words.values()))
            yield VoiceAssistantConfigurationResponse(
                available_wake_words=[
                    VoiceAssistantWakeWord(
                        id=ww.id,
                        wake_word=ww.wake_word,
                        trained_languages=ww.trained_languages,
                    )
                    for ww in filtered_wake_words
                ],
                active_wake_words=self.state.detector.get_active_wake_words(),
                max_active_wake_words=1,
            )
            _LOGGER.info("Connected to Home Assistant")
        elif isinstance(msg, VoiceAssistantSetConfiguration):
            for wake_word_id in msg.active_wake_words:
                if self.state.detector.set_wake_model(wake_word_id):
                    self.state.wake_word_config.save_wake_word(wake_word_id)
                    break

    def handle_audio(self, audio_chunk: bytes) -> None:
        if not self._is_streaming_audio:
            return

        self.send_messages([VoiceAssistantAudio(data=audio_chunk)])

    def wakeup(self, wake_word_phrase: Optional[str] = None) -> None:
        # Why are we stopping the timer? Wouldn't it be better to delay it?
        if self._timer_finished:
            # Stop timer instead
            self._timer_finished = False
            self.state.tts_player.stop()
            _LOGGER.debug("Stopping timer finished sound")
            return
        
        if not wake_word_phrase:
            wake_words = self.state.detector.available_wake_words
            wake_word_id = self.state.detector.wake_model_id
            wake_word_phrase = wake_words[wake_word_id].wake_word if wake_word_id in wake_words else "unknown"
        
        _LOGGER.debug("Detected wake word phrase: %s", wake_word_phrase)
        
        self.send_messages(
            [VoiceAssistantRequest(start=True, wake_word_phrase=wake_word_phrase)]
        )

        self.state.event_bus.publish('voice_wakeword', {'wake_word_phrase': wake_word_phrase})


        self.duck()
        self._is_streaming_audio = True
        self.state.tts_player.play(self.state.wakeup_sound)

    def stop(self) -> None:
        self.state.detector.stop_active = False
        self.state.tts_player.stop()

        if self._timer_finished:
            self._timer_finished = False
            _LOGGER.debug("Stopping timer finished sound")
        else:
            _LOGGER.debug("TTS response stopped manually")
            self._tts_finished()

    def play_tts(self) -> None:
        if (not self._tts_url) or self._tts_played:
            return

        self._tts_played = True
        _LOGGER.debug("Playing TTS response: %s", self._tts_url)

        self.state.event_bus.publish('voice_play_tts', {})

        self.state.detector.stop_active = True
        self.state.tts_player.play(self._tts_url, done_callback=self._tts_finished)

    def duck(self) -> None:
        _LOGGER.debug("Ducking music")
        self.state.music_player.duck()

    def unduck(self) -> None:
        _LOGGER.debug("Unducking music")
        self.state.music_player.unduck()

    def _tts_finished(self) -> None:
        self.state.detector.stop_active = False
        self.send_messages([VoiceAssistantAnnounceFinished()])

        # Actual time the TTS stops speaking
        self.state.event_bus.publish('voice__tts_finished', {})

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
            self.state.timer_finished_sound,
            done_callback=lambda: call_all(
                lambda: time.sleep(1.0), self._play_timer_finished
            ),
        )

    def connection_lost(self, exc):
        super().connection_lost(exc)
        _LOGGER.info("Disconnected from Home Assistant")


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

                # Process audio through detector
                wake_detected, stop_detected = state.detector.process_audio(audio_chunk)

                handle_detection(state, wake_detected, stop_detected)
                    
            except Exception:
                _LOGGER.exception("Unexpected error handling audio")

    except Exception:
        _LOGGER.exception("Unexpected error processing audio")

def handle_detection(state: ServerState, wake_detected: bool, stop_detected: bool):
    if state.satellite is None:
        return

    if wake_detected:
        state.satellite.wakeup()
    if stop_detected:
        state.satellite.stop()

# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--detector-type", 
        choices=["mww", "oww"], 
        default="mww",
        help="Detector type: mww (MicroWakeWord) or oww (OpenWakeWord)"
    )
    parser.add_argument("--wake-uri", help="Wyoming wake server URI (required for oww, e.g., tcp://127.0.0.1:10400)")
    parser.add_argument(
        "--audio-input-device",
        default="default",
        help="sounddevice name for input device",
    )
    parser.add_argument("--audio-input-block-size", type=int, default=1024)
    parser.add_argument("--audio-output-device", help="mpv name for output device")
    parser.add_argument(
        "--wake-word-dir",
        default=_WAKEWORDS_DIR,
        help="Directory with wake word models (.tflite) and configs (.json)",
    )
    parser.add_argument(
        "--wake-model", default="hey_jarvis", help="Id of active wake model"
    )
    parser.add_argument("--stop-model", default="stop", help="Id of stop model")
    parser.add_argument(
        "--wakeup-sound", default=str(_SOUNDS_DIR / "wake_word_triggered.flac")
    )
    parser.add_argument(
        "--timer-finished-sound", default=str(_SOUNDS_DIR / "timer_finished.flac")
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address for ESPHome server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=6053, help="Port for ESPHome server (default: 6053)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to console"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)

    # Validate CLI arguments
    if (args.wake_model == args.stop_model):
        parser.error("Wake model and stop model must be different")
    if args.detector_type == "oww" and not args.wake_uri:
        parser.error("--wake-uri is required when using OpenWakeWord (--detector-type oww)")
    if args.detector_type == "mww" and not args.wake_word_dir:
        parser.error("--wake-word-dor is required when using MicroWakeWord (--detector-type mww)")

    # Prepare detector arguments
    detector_kwargs = {}
    
    if args.detector_type == "mww":
        libtensorflowlite_c_path = _LIB_DIR / "libtensorflowlite_c.so"
        detector_kwargs['libtensorflowlite_c_path'] = libtensorflowlite_c_path
        detector_kwargs['wake_word_dir'] = args.wake_word_dir
    elif args.detector_type == "oww":
        detector_kwargs['wake_uri'] = args.wake_uri

    # Create wake word config manager and try to load saved wake word, fall back to command line argument
    wake_word_config = WakeWordConfig(args.detector_type, _CONFIG_DIR)
    wake_model = wake_word_config.load_wake_word(args.wake_model)
    _LOGGER.debug("Using wake word: %s and stop word: %s", wake_model, args.stop_model)

    # Create detector
    detector = DetectorFactory.create_detector(
        detector_type=args.detector_type,
        wake_model=wake_model,
        stop_model=args.stop_model,
        **detector_kwargs
    )
    
    loop = asyncio.get_running_loop()

    state = ServerState(
        name=args.name,
        mac_address=get_mac(),
        audio_queue=Queue(),
        entities=[],
        detector=detector,
        event_bus=EventBus(),
        wake_word_config=wake_word_config,
        loop=loop,
        music_player=MpvMediaPlayer(device=args.audio_output_device),
        tts_player=MpvMediaPlayer(device=args.audio_output_device),
        wakeup_sound=args.wakeup_sound,
        timer_finished_sound=args.timer_finished_sound,
    )

    LedEvent(state)

    # Connect to remote service if needed (e.g., Wyoming server)
    def _on_detect(name, ts):
        if state.satellite is not None:
            _LOGGER.debug("Detected wake word via callback: %s", name)

            def _handle_detection():
                if (state.satellite is None): return
                handle_detection(state, name == state.detector.wake_model_id, name == state.detector.stop_model_id)

            state.loop.call_soon_threadsafe(_handle_detection)

    detector.connect_if_needed(_on_detect)

    process_audio_thread = threading.Thread(
        target=process_audio, args=(state,), daemon=True
    )
    process_audio_thread.start()

    def sd_callback(indata, _frames, _time, _status):
        state.audio_queue.put_nowait(bytes(indata))

    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), host=args.host, port=args.port
    )

    try:
        _LOGGER.debug("Opening audio input device: %s", args.audio_input_device)
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=args.audio_input_block_size,
            device=args.audio_input_device,
            dtype="int16",
            channels=1,
            callback=sd_callback,
        ):
            async with server:
                _LOGGER.info("Server started (host=%s, port=%s)", args.host, args.port)
                await server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.audio_queue.put_nowait(None)
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())