#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Dict, List, Optional, Union

import numpy as np
import sounddevice as sd

from .event_bus import EventBus, EventHandler, subscribe
from .led_controller import LedController
from .mqtt_controller import MqttController
from .microwakeword import MicroWakeWord, MicroWakeWordFeatures
from .models import AvailableWakeWord, Preferences, ServerState, WakeWordType
from .mpv_player import MpvMediaPlayer
from .openwakeword import OpenWakeWord, OpenWakeWordFeatures
from .satellite import VoiceSatelliteProtocol
from .util import get_mac, is_arm
from .zeroconf import HomeAssistantZeroconf

_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent
_WAKEWORDS_DIR = _REPO_DIR / "wakewords"
_OWW_DIR = _WAKEWORDS_DIR / "openWakeWord"
_SOUNDS_DIR = _REPO_DIR / "sounds"

if is_arm():
    _LIB_DIR = _REPO_DIR / "lib" / "linux_arm64"
else:
    _LIB_DIR = _REPO_DIR / "lib" / "linux_amd64"

# -----------------------------------------------------------------------------

class MicMuteHandler(EventHandler):
    """Event handler for mic mute switch."""
    def __init__(self, state, mqtt_controller: Optional[MqttController]):
        super().__init__(state)
        self.mqtt_controller = mqtt_controller

    @subscribe
    def set_mic_mute(self, data: dict):
        is_muted = data.get("state", False)
        if self.state.mic_muted != is_muted:
            self.state.mic_muted = is_muted
            _LOGGER.debug("Mic muted = %s", is_muted)
            
            if self.mqtt_controller:
                self.mqtt_controller.publish_mute_state(is_muted)
            
            if is_muted:
                self.state.event_bus.publish("mic_muted")
            else:
                _LOGGER.debug("Clearing stale audio queue...")
                while not self.state.audio_queue.empty():
                    try:
                        self.state.audio_queue.get_nowait()
                    except Queue.Empty:
                        break
                self.state.event_bus.publish("mic_unmuted")

# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--audio-input-device",
        default="default",
        help="sounddevice name for input device",
    )
    parser.add_argument("--audio-input-block-size", type=int, default=1024)
    parser.add_argument("--audio-output-device", help="mpv name for output device")
    parser.add_argument(
        "--wake-word-dir",
        default=[_WAKEWORDS_DIR],
        action="append",
        help="Directory with wake word models (.tflite) and configs (.json)",
    )
    parser.add_argument(
        "--wake-model", default="okay_nabu", help="Id of active wake model"
    )
    parser.add_argument("--stop-model", default="stop", help="Id of stop model")
    parser.add_argument(
        "--refractory-seconds",
        default=2.0,
        type=float,
        help="Seconds before wake word can be activated again",
    )
    parser.add_argument(
        "--oww-melspectrogram-model",
        default=_OWW_DIR / "melspectrogram.tflite",
        help="Path to openWakeWord melspectrogram model",
    )
    parser.add_argument(
        "--oww-embedding-model",
        default=_OWW_DIR / "embedding_model.tflite",
        help="Path to openWakeWord embedding model",
    )
    parser.add_argument(
        "--wakeup-sound", default=str(_SOUNDS_DIR / "wake_word_triggered.flac")
    )
    parser.add_argument(
        "--timer-finished-sound", default=str(_SOUNDS_DIR / "timer_finished.flac")
    )
    parser.add_argument("--preferences-file", default=_REPO_DIR / "preferences.json")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address for ESPHome server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=6053, help="Port for ESPHome server (default: 6053)"
    )
    parser.add_argument(
        "--led-type",
        choices=["dotstar", "neopixel"],
        default="dotstar",
        help="Type of LED strip (default: dotstar for APA102)",
    )
    parser.add_argument(
        "--led-interface",
        choices=["spi", "gpio"],
        default="spi",
        help="Interface for LEDs (default: spi)",
    )
    parser.add_argument(
        "--led-clock-pin",
        type=int,
        default=13,
        help="GPIO pin for LED clock (for Grove)",
    )
    parser.add_argument(
        "--led-data-pin",
        type=int,
        default=12,
        help="GPIO pin for LED data (for Grove)",
    )
    parser.add_argument(
        "--num-leds",
        type=int,
        default=3,
        help="Number of LEDs in the strip",
    )
    parser.add_argument("--mqtt-host", help="MQTT broker host")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--mqtt-username", help="MQTT broker username")
    parser.add_argument("--mqtt-password", help="MQTT broker password")
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to console"
    )
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    _LOGGER.debug(args)
    loop = asyncio.get_running_loop()
    event_bus = EventBus()

    preferences_path = Path(args.preferences_file)
    if preferences_path.exists():
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()
    
    preferences.num_leds = getattr(preferences, 'num_leds', args.num_leds)
    
    available_wake_words: Dict[str, AvailableWakeWord] = {}
    for wake_word_dir in [Path(d) for d in args.wake_word_dir]:
        for model_config_path in wake_word_dir.glob("*.json"):
            model_id = model_config_path.stem
            if model_id == args.stop_model:
                continue
            with open(model_config_path, "r", encoding="utf-8") as model_config_file:
                model_config = json.load(model_config_file)
                model_type = model_config["type"]
                available_wake_words[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=WakeWordType(model_type),
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    config_path=model_config_path,
                )

    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))
    
    libtensorflowlite_c_path = _LIB_DIR / "libtensorflowlite_c.so"
    
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}
    if preferences.active_wake_words:
        for wake_word_id in preferences.active_wake_words:
            wake_word = available_wake_words.get(wake_word_id)
            if wake_word is None: continue
            wake_models[wake_word_id] = wake_word.load(libtensorflowlite_c_path)
    if not wake_models:
        wake_word_id = args.wake_model
        wake_word = available_wake_words.get(wake_word_id)
        if wake_word:
             wake_models[wake_word_id] = wake_word.load(libtensorflowlite_c_path)
    
    stop_model: Optional[MicroWakeWord] = None
    for wake_word_dir in [Path(d) for d in args.wake_word_dir]:
        stop_config_path = wake_word_dir / f"{args.stop_model}.json"
        if not stop_config_path.exists(): continue
        stop_model = MicroWakeWord.from_config(stop_config_path, libtensorflowlite_c_path)
        break
    assert stop_model is not None
    
    music_player = MpvMediaPlayer(
        loop=loop,  # <-- MODIFIED
        device=args.audio_output_device,
        initial_volume=preferences.volume_level
    )
    tts_player = MpvMediaPlayer(
        loop=loop,  # <-- MODIFIED
        device=args.audio_output_device,
        initial_volume=preferences.volume_level
    )
    
    state = ServerState(
        name=args.name,
        mac_address=get_mac(),
        audio_queue=Queue(),
        entities=[],
        available_wake_words=available_wake_words,
        wake_words=wake_models,
        stop_word=stop_model,
        music_player=music_player,
        tts_player=tts_player,
        wakeup_sound=args.wakeup_sound,
        timer_finished_sound=args.timer_finished_sound,
        preferences=preferences,
        preferences_path=preferences_path,
        libtensorflowlite_c_path=libtensorflowlite_c_path,
        event_bus=event_bus,
        loop=loop,
        oww_melspectrogram_path=Path(args.oww_melspectrogram_model),
        oww_embedding_path=Path(args.oww_embedding_model),
        refractory_seconds=args.refractory_seconds,
    )
    
    led_controller = LedController(
        state,
        led_type=args.led_type,
        interface=args.led_interface,
        clock_pin=args.led_clock_pin,
        data_pin=args.led_data_pin,
        num_leds=state.preferences.num_leds,
    )
    
    mqtt_controller: Optional[MqttController] = None
    if args.mqtt_host:
        mqtt_controller = MqttController(state=state, host=args.mqtt_host, port=args.mqtt_port, username=args.mqtt_username, password=args.mqtt_password)
        mqtt_controller.start()
    
    mic_mute_handler = MicMuteHandler(state, mqtt_controller)
    process_audio_thread = threading.Thread(target=process_audio, args=(state,), daemon=True)
    process_audio_thread.start()

    def sd_callback(indata, _frames, _time, _status): state.audio_queue.put_nowait(bytes(indata))
    
    server = await loop.create_server(lambda: VoiceSatelliteProtocol(state), host=args.host, port=args.port)
    discovery = HomeAssistantZeroconf(port=args.port, name=args.name)
    await discovery.register_server()

    try:
        with sd.RawInputStream(samplerate=16000, blocksize=args.audio_input_block_size, device=args.audio_input_device, dtype="int16", channels=1, callback=sd_callback):
            async with server:
                _LOGGER.info("Server started (host=%s, port=%s)", args.host, args.port)
                await server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        if mqtt_controller: mqtt_controller.stop()
        state.audio_queue.put_nowait(None)
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")

def process_audio(state: ServerState):
    wake_words: List[Union[MicroWakeWord, OpenWakeWord]] = []
    micro_features: Optional[MicroWakeWordFeatures] = None
    micro_inputs: List[np.ndarray] = []
    oww_features: Optional[OpenWakeWordFeatures] = None
    oww_inputs: List[np.ndarray] = []
    has_oww = False
    last_active: Optional[float] = None
    try:
        while True:
            if state.mic_muted:
                time.sleep(0.1)
                continue
            audio_chunk = state.audio_queue.get()
            if audio_chunk is None: break
            if state.satellite is None: continue
            if (not wake_words) or (state.wake_words_changed and state.wake_words):
                state.wake_words_changed = False
                wake_words = [ww for ww in state.wake_words.values() if ww.is_active]
                has_oww = any(isinstance(ww, OpenWakeWord) for ww in wake_words)
                if micro_features is None:
                    micro_features = MicroWakeWordFeatures(libtensorflowlite_c_path=state.libtensorflowlite_c_path)
                if has_oww and (oww_features is None):
                    oww_features = OpenWakeWordFeatures(melspectrogram_model=state.oww_melspectrogram_path, embedding_model=state.oww_embedding_path, libtensorflowlite_c_path=state.libtensorflowlite_c_path)
            try:
                state.satellite.handle_audio(audio_chunk)
                assert micro_features is not None
                micro_inputs.clear(); micro_inputs.extend(micro_features.process_streaming(audio_chunk))
                if has_oww:
                    assert oww_features is not None
                    oww_inputs.clear(); oww_inputs.extend(oww_features.process_streaming(audio_chunk))
                for wake_word in wake_words:
                    activated = False
                    if isinstance(wake_word, MicroWakeWord):
                        if any(wake_word.process_streaming(mi) for mi in micro_inputs): activated = True
                    elif isinstance(wake_word, OpenWakeWord):
                        if any(p > 0.5 for oi in oww_inputs for p in wake_word.process_streaming(oi)): activated = True
                    if activated:
                        now = time.monotonic()
                        if (last_active is None) or ((now - last_active) > state.refractory_seconds):
                            state.satellite.wakeup(wake_word)
                            last_active = now
                if any(state.stop_word.process_streaming(mi) for mi in micro_inputs) and state.stop_word.is_active:
                    state.satellite.stop()
            except Exception: _LOGGER.exception("Unexpected error handling audio")
    except Exception: _LOGGER.exception("Unexpected error processing audio")

if __name__ == "__main__":
    print("--- __main__ block executing ---")
    asyncio.run(main())
