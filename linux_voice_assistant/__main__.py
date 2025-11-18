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

from .config import Config, load_config_from_json  # <-- NEW
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
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        required=True,
        help="Path to configuration.json file",
    )
    args = parser.parse_args()

    # --- Load Configuration ---
    config = load_config_from_json(args.config)

    # --- Setup Logging ---
    logging.basicConfig(level=logging.DEBUG if config.app.debug else logging.INFO)
    _LOGGER.debug("Configuration loaded: %s", config)
    
    loop = asyncio.get_running_loop()
    event_bus = EventBus()

    # --- Load Preferences ---
    preferences_path = _REPO_DIR / config.app.preferences_file
    if preferences_path.exists():
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()
    
    # Set default num_leds from config, allowing preferences to override
    preferences.num_leds = getattr(preferences, 'num_leds', config.led.num_leds)
    
    # --- Load Wake Words ---
    available_wake_words: Dict[str, AvailableWakeWord] = {}
    
    # Add default directories if none are specified
    if not config.wake_word.directories:
        config.wake_word.directories = ["wakewords", "wakewords/openWakeWord"]

    for ww_dir_str in config.wake_word.directories:
        wake_word_dir = _REPO_DIR / ww_dir_str
        for model_config_path in wake_word_dir.glob("*.json"):
            model_id = model_config_path.stem
            if model_id == config.wake_word.stop_model:
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
    
    # --- Load Active Wake Word Models ---
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}
    if preferences.active_wake_words:
        for wake_word_id in preferences.active_wake_words:
            wake_word = available_wake_words.get(wake_word_id)
            if wake_word is None: continue
            wake_models[wake_word_id] = wake_word.load(libtensorflowlite_c_path)
    
    # Load default model if no preferences are set
    if not wake_models:
        wake_word_id = config.wake_word.model
        wake_word = available_wake_words.get(wake_word_id)
        if wake_word:
             wake_models[wake_word_id] = wake_word.load(libtensorflowlite_c_path)
    
    # --- Load Stop Model ---
    stop_model: Optional[MicroWakeWord] = None
    for ww_dir_str in config.wake_word.directories:
        wake_word_dir = _REPO_DIR / ww_dir_str
        stop_config_path = wake_word_dir / f"{config.wake_word.stop_model}.json"
        if not stop_config_path.exists(): continue
        stop_model = MicroWakeWord.from_config(stop_config_path, libtensorflowlite_c_path)
        break
    assert stop_model is not None
    
    # --- Initialize Media Players ---
    music_player = MpvMediaPlayer(
        loop=loop,
        device=config.audio.output_device,
        initial_volume=preferences.volume_level
    )
    tts_player = MpvMediaPlayer(
        loop=loop,
        device=config.audio.output_device,
        initial_volume=preferences.volume_level
    )
    
    # --- Create Global Server State ---
    state = ServerState(
        name=config.app.name,
        mac_address=get_mac(),
        audio_queue=Queue(),
        entities=[],
        available_wake_words=available_wake_words,
        wake_words=wake_models,
        stop_word=stop_model,
        music_player=music_player,
        tts_player=tts_player,
        wakeup_sound=str(_REPO_DIR / config.app.wakeup_sound),
        timer_finished_sound=str(_REPO_DIR / config.app.timer_finished_sound),
        preferences=preferences,
        preferences_path=preferences_path,
        libtensorflowlite_c_path=libtensorflowlite_c_path,
        event_bus=event_bus,
        loop=loop,
        oww_melspectrogram_path=(_REPO_DIR / config.wake_word.oww_melspectrogram_model),
        oww_embedding_path=(_REPO_DIR / config.wake_word.oww_embedding_model),
        refractory_seconds=config.wake_word.refractory_seconds,
    )
    
    # --- Initialize Controllers ---
    led_controller = LedController(
        state,
        led_type=config.led.led_type,
        interface=config.led.interface,
        clock_pin=config.led.clock_pin,
        data_pin=config.led.data_pin,
        num_leds=state.preferences.num_leds,
    )
    
    mqtt_controller: Optional[MqttController] = None
    if config.mqtt.enabled:
        mqtt_controller = MqttController(
            state=state,
            host=config.mqtt.host,
            port=config.mqtt.port,
            username=config.mqtt.username,
            password=config.mqtt.password
        )
        mqtt_controller.start()
    
    mic_mute_handler = MicMuteHandler(state, mqtt_controller)
    
    # --- Start Audio Processing Thread ---
    process_audio_thread = threading.Thread(target=process_audio, args=(state,), daemon=True)
    process_audio_thread.start()

    def sd_callback(indata, _frames, _time, _status): 
        state.audio_queue.put_nowait(bytes(indata))
    
    # --- Start ESPHome Server ---
    server = await loop.create_server(
        lambda: VoiceSatelliteProtocol(state), 
        host=config.esphome.host, 
        port=config.esphome.port
    )
    discovery = HomeAssistantZeroconf(port=config.esphome.port, name=config.app.name)
    await discovery.register_server()

    # --- Run Forever ---
    try:
        input_device = config.audio.input_device if config.audio.input_device else "default"
        with sd.RawInputStream(
            samplerate=16000,
            blocksize=config.audio.input_block_size,
            device=input_device,
            dtype="int16",
            channels=1,
            callback=sd_callback
        ):
            async with server:
                _LOGGER.info("Server started (host=%s, port=%s)", config.esphome.host, config.esphome.port)
                await server.serve_forever()
    except KeyboardInterrupt: 
        pass
    except sd.PortAudioError as e:
        _LOGGER.critical("Failed to open audio input device '%s': %s", input_device, e)
    finally:
        if mqtt_controller: 
            mqtt_controller.stop()
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
