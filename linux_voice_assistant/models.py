#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

import numpy as np
import soundcard as sc
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .config import Config, load_config_from_json
from .event_bus import EventBus, EventHandler, subscribe
from .led_controller import LedController
from .mqtt_controller import MqttController
from .models import AvailableWakeWord, Preferences, ServerState, WakeWordType
from .mpv_player import MpvMediaPlayer
from .satellite import VoiceSatelliteProtocol
from .util import get_mac
from .zeroconf import HomeAssistantZeroconf

_LOGGER = logging.getLogger(__name__)
_MODULE_DIR = Path(__file__).parent
_REPO_DIR = _MODULE_DIR.parent

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
                self.state.event_bus.publish("mic_unmuted")

# -----------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        required=False,
        default=_MODULE_DIR / "config.json",
        help="Path to configuration.json file (default: linux_voice_assistant/config.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (overrides config file)",
    )
    parser.add_argument(
        "--list-input-devices",
        action="store_true",
        help="List audio input devices and exit",
    )
    parser.add_argument(
        "--list-output-devices",
        action="store_true",
        help="List audio output devices and exit",
    )
    args = parser.parse_args()

    # --- Handle List Devices ---
    if args.list_input_devices:
        print("Input devices")
        print("=" * 13)
        for idx, mic in enumerate(sc.all_microphones(include_loopback=False)):
            print(f"[{idx}]", mic.name)
        return

    if args.list_output_devices:
        player = MpvMediaPlayer(loop=None)
        print("Output devices")
        print("=" * 14)
        try:
            for speaker in player.player.audio_device_list:
                print(speaker["name"] + ":", speaker["description"])
        except Exception as e:
            _LOGGER.error("Failed to list output devices: %s", e)
        return

    # --- Load Configuration ---
    config_path = args.config
    
    if not config_path.is_absolute():
         config_path = _REPO_DIR / config_path
         
    config = load_config_from_json(config_path)

    if args.debug:
        config.app.debug = True

    logging.basicConfig(level=logging.DEBUG if config.app.debug else logging.INFO)
    _LOGGER.info("Loading configuration from: %s", config_path)
    _LOGGER.debug("Configuration loaded: %s", config)
    
    loop = asyncio.get_running_loop()
    event_bus = EventBus()

    # --- Create Download Dir ---
    download_dir = _REPO_DIR / config.wake_word.download_dir
    download_dir.mkdir(parents=True, exist_ok=True)
    _LOGGER.debug("Download directory: %s", download_dir)

    # --- Load Preferences ---
    preferences_path = _REPO_DIR / config.app.preferences_file
    if preferences_path.exists():
        with open(preferences_path, "r", encoding="utf-8") as preferences_file:
            preferences_dict = json.load(preferences_file)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()
    
    preferences.num_leds = getattr(preferences, 'num_leds', config.led.num_leds)
    
    # --- Load Wake Words ---
    available_wake_words: Dict[str, AvailableWakeWord] = {}
    
    if not config.wake_word.directories:
        config.wake_word.directories = ["wakewords", "wakewords/openWakeWord"]

    # Add download directory to search path
    wake_word_dirs = [_REPO_DIR / d for d in config.wake_word.directories]
    wake_word_dirs.append(download_dir / "external_wake_words") # <-- ADDED

    for wake_word_dir in wake_word_dirs:
        if not wake_word_dir.exists():
            continue
            
        for model_config_path in wake_word_dir.glob("*.json"):
            model_id = model_config_path.stem
            if model_id == config.wake_word.stop_model:
                continue
            with open(model_config_path, "r", encoding="utf-8") as model_config_file:
                model_config = json.load(model_config_file)
                model_type = WakeWordType(model_config["type"])
                
                if model_type == WakeWordType.OPEN_WAKE_WORD:
                    wake_word_path = model_config_path.parent / model_config["model"]
                else:
                    wake_word_path = model_config_path

                available_wake_words[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=model_type,
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                )

    _LOGGER.debug("Available wake words: %s", list(sorted(available_wake_words.keys())))
    
    # --- Load Active Wake Word Models ---
    active_wake_words: Set[str] = set()
    wake_models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}
    if preferences.active_wake_words:
        for wake_word_id in preferences.active_wake_words:
            wake_word = available_wake_words.get(wake_word_id)
            if wake_word is None: continue
            _LOGGER.debug("Loading wake model: %s", wake_word_id)
            wake_models[wake_word_id] = wake_word.load()
            active_wake_words.add(wake_word_id)
    
    if not wake_models:
        wake_word_id = config.wake_word.model
        wake_word = available_wake_words.get(wake_word_id)
        if wake_word:
            _LOGGER.debug("Loading wake model: %s", wake_word_id)
            wake_models[wake_word_id] = wake_word.load()
            active_wake_words.add(wake_word_id)
    
    # --- Load Stop Model ---
    stop_model: Optional[MicroWakeWord] = None
    for ww_dir_str in config.wake_word.directories:
        wake_word_dir = _REPO_DIR / ww_dir_str
        stop_config_path = wake_word_dir / f"{config.wake_word.stop_model}.json"
        if not stop_config_path.exists(): continue
        _LOGGER.debug("Loading stop model: %s", stop_config_path)
        stop_model = MicroWakeWord.from_config(stop_config_path)
        break
    assert stop_model is not None

    # --- Resolve Microphone ---
    if config.audio.input_device is not None:
        try:
            input_device_idx = int(config.audio.input_device)
            mic = sc.all_microphones(include_loopback=False)[input_device_idx]
        except (ValueError, IndexError):
            mic = sc.get_microphone(config.audio.input_device)
    else:
        mic = sc.default_microphone()
    
    if mic is None:
        _LOGGER.critical("No microphone found.")
        sys.exit(1)
        
    _LOGGER.info("Using audio input device: %s", mic.name)

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
        entities=[],
        available_wake_words=available_wake_words,
        wake_words=wake_models,
        active_wake_words=active_wake_words,
        stop_word=stop_model,
        music_player=music_player,
        tts_player=tts_player,
        wakeup_sound=str(_REPO_DIR / config.app.wakeup_sound),
        timer_finished_sound=str(_REPO_DIR / config.app.timer_finished_sound),
        preferences=preferences,
        preferences_path=preferences_path,
        event_bus=event_bus,
        loop=loop,
        download_dir=download_dir, # <-- ADDED
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
    process_audio_thread = threading.Thread(
        target=process_audio,
        args=(state, mic, config.audio.input_block_size),
        daemon=True
    )
    process_audio_thread.start()
    
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
        async with server:
            _LOGGER.info("Server started (host=%s, port=%s)", config.esphome.host, config.esphome.port)
            await server.serve_forever()
    except KeyboardInterrupt: 
        pass
    finally:
        if mqtt_controller: 
            mqtt_controller.stop()
        
        state.mic_muted = True 
        process_audio_thread.join()

    _LOGGER.debug("Server stopped")

def process_audio(state: ServerState, mic, block_size: int):
    """Process audio chunks from the microphone in a separate thread."""
    wake_words: List[Union[MicroWakeWord, OpenWakeWord]] = []
    micro_features: Optional[MicroWakeWordFeatures] = None
    micro_inputs: List[np.ndarray] = []
    oww_features: Optional[OpenWakeWordFeatures] = None
    oww_inputs: List[np.ndarray] = []
    has_oww = False
    last_active: Optional[float] = None
    try:
        _LOGGER.debug("Opening audio input device: %s", mic.name)
        with mic.recorder(samplerate=16000, channels=1, blocksize=block_size) as mic_in:
            while True:
                if state.mic_muted:
                    time.sleep(0.1)
                    continue
                
                audio_chunk_array = mic_in.record(block_size).reshape(-1)
                audio_chunk = (
                    (np.clip(audio_chunk_array, -1.0, 1.0) * 32767.0)
                    .astype("<i2")
                    .tobytes()
                )

                if state.satellite is None:
                    continue
                
                if (not wake_words) or (state.wake_words_changed and state.wake_words):
                    state.wake_words_changed = False
                    wake_words = [
                        ww for ww in state.wake_words.values() 
                        if ww.id in state.active_wake_words
                    ]
                    has_oww = any(isinstance(ww, OpenWakeWord) for ww in wake_words)
                    if micro_features is None:
                        micro_features = MicroWakeWordFeatures()
                    if has_oww and (oww_features is None):
                        oww_features = OpenWakeWordFeatures.from_builtin()
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
                    
                    stopped = False
                    for micro_input in micro_inputs:
                        if state.stop_word.process_streaming(micro_input):
                            stopped = True

                    if stopped and (state.stop_word.id in state.active_wake_words):
                        state.satellite.stop()
                except Exception: _LOGGER.exception("Unexpected error handling audio")
    except Exception as e:
        _LOGGER.critical("Error in audio processing thread: %s", e)
        state.loop.call_soon_threadsafe(state.loop.stop)


if __name__ == "__main__":
    print("--- __main__ block executing ---")
    asyncio.run(main())
