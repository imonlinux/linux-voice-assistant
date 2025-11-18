#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

import numpy as np
import soundcard as sc
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

from .config import Config, LedConfig, MqttConfig, load_config_from_json
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
# Helper dataclasses
# -----------------------------------------------------------------------------

@dataclass
class WakeWordData:
    """Dataclass to hold all wake word models and settings."""
    available: Dict[str, AvailableWakeWord]
    models: Dict[str, Union[MicroWakeWord, OpenWakeWord]]
    active: Set[str]
    stop_model: MicroWakeWord

@dataclass
class MediaPlayers:
    """Dataclass to hold media player instances."""
    music: MpvMediaPlayer
    tts: MpvMediaPlayer

# -----------------------------------------------------------------------------
# Mic Mute Handler
# -----------------------------------------------------------------------------

class MicMuteHandler(EventHandler):
    """
    Manages the mic_muted state and saves preferences.
    This is the only controller that writes to ServerState.
    """
    def __init__(
        self,
        event_bus: EventBus,
        state: ServerState,
        mqtt_controller: Optional[MqttController],
    ):
        super().__init__(event_bus)
        self.state = state
        self.mqtt_controller = mqtt_controller
        self._subscribe_all_methods()

    @subscribe
    def set_mic_mute(self, data: dict):
        """Event handler for mic mute commands."""
        is_muted = data.get("state", False)
        if self.state.mic_muted != is_muted:
            self.state.mic_muted = is_muted
            _LOGGER.debug("Mic muted = %s", is_muted)
            
            if self.mqtt_controller:
                self.mqtt_controller.publish_mute_state(is_muted)
            
            if is_muted:
                self.event_bus.publish("mic_muted")
            else:
                self.event_bus.publish("mic_unmuted")

    @subscribe
    def set_num_leds(self, data: dict):
        """Event handler to save num_leds to preferences."""
        num_leds = data.get("num_leds")
        if (num_leds is not None) and (self.state.preferences.num_leds != num_leds):
            self.state.preferences.num_leds = num_leds
            self.state.save_preferences()

# -----------------------------------------------------------------------------
# Main Application
# -----------------------------------------------------------------------------

async def main() -> None:
    # --- 1. Load Basics ---
    config, loop, event_bus = _init_basics()
    
    # --- 2. Load Preferences ---
    preferences = _load_preferences(config)

    # --- 3. Find Microphone ---
    mic = _get_microphone(config)
    
    # --- 4. Load Wake Words ---
    wake_word_data = _load_wake_words(config, preferences)

    # --- 5. Initialize Media Players ---
    media_players = _init_media_players(loop, config, preferences)

    # --- 6. Create Server State ---
    state = _create_server_state(
        config, loop, event_bus, preferences, 
        wake_word_data, media_players
    )

    # --- 7. Initialize Controllers ---
    _init_controllers(loop, event_bus, state, config, preferences)
    
    # --- 8. Start Audio Thread ---
    audio_thread = threading.Thread(
        target=_process_audio,
        args=(state, mic, config.audio.input_block_size),
        daemon=True
    )
    audio_thread.start()
    
    # --- 9. Run Server ---
    try:
        await _run_server(state, config)
    finally:
        # --- 10. Cleanup ---
        _LOGGER.debug("Shutting down...")
        state.shutdown = True # Signal audio thread to stop
        audio_thread.join()
        
        if hasattr(state, "mqtt_controller") and state.mqtt_controller:
            _LOGGER.debug("Stopping MQTT controller...")
            state.mqtt_controller.stop()

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _init_basics() -> Tuple[Config, asyncio.AbstractEventLoop, EventBus]:
    """Loads config, sets up logging, and creates loop/event bus."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--config", type=Path, required=False,
        default=_MODULE_DIR / "config.json",
        help="Path to configuration.json file"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--list-input-devices", action="store_true", help="List audio input devices")
    parser.add_argument("--list-output-devices", action="store_true", help="List audio output devices")
    args = parser.parse_args()

    # --- List devices and exit ---
    if args.list_input_devices:
        print("Input devices\n" + "=" * 13)
        for idx, mic in enumerate(sc.all_microphones(include_loopback=False)):
            print(f"[{idx}]", mic.name)
        sys.exit(0)

    if args.list_output_devices:
        print("Output devices\n" + "=" * 14)
        try:
            player = MpvMediaPlayer(loop=None)
            for speaker in player.player.audio_device_list:
                print(speaker["name"] + ":", speaker["description"])
        except Exception as e:
            _LOGGER.error("Failed to list output devices: %s", e)
        sys.exit(1)

    # --- Load config ---
    config_path = args.config
    if not config_path.is_absolute():
         config_path = _REPO_DIR / config_path
    config = load_config_from_json(config_path)

    if args.debug:
        config.app.debug = True

    # --- Setup logging ---
    logging.basicConfig(level=logging.DEBUG if config.app.debug else logging.INFO)
    _LOGGER.info("Loading configuration from: %s", config_path)
    _LOGGER.debug("Configuration loaded: %s", config)

    loop = asyncio.get_running_loop()
    event_bus = EventBus()
    
    return config, loop, event_bus

def _load_preferences(config: Config) -> Preferences:
    """Loads preferences.json file."""
    preferences_path = _REPO_DIR / config.app.preferences_file
    if preferences_path.exists():
        with open(preferences_path, "r", encoding="utf-8") as f:
            preferences_dict = json.load(f)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()
    
    preferences.num_leds = getattr(preferences, 'num_leds', config.led.num_leds)
    return preferences

def _get_microphone(config: Config):
    """Finds and returns the microphone specified in the config."""
    mic = None
    if config.audio.input_device is not None:
        try:
            input_device_idx = int(config.audio.input_device)
            mic = sc.all_microphones(include_loopback=False)[input_device_idx]
        except (ValueError, IndexError):
            mic = sc.get_microphone(config.audio.input_device, include_loopback=False)
    else:
        mic = sc.default_microphone()
    
    if mic is None:
        _LOGGER.critical("No microphone found. Use --list-input-devices to see options.")
        sys.exit(1)
        
    _LOGGER.info("Using audio input device: %s", mic.name)
    return mic

def _load_wake_words(config: Config, preferences: Preferences) -> WakeWordData:
    """Loads all available and active wake word models."""
    
    if not config.wake_word.directories:
        config.wake_word.directories = ["wakewords", "wakewords/openWakeWord"]
        
    download_dir = _REPO_DIR / config.wake_word.download_dir
    download_dir.mkdir(parents=True, exist_ok=True)
    _LOGGER.debug("Download directory: %s", download_dir)

    wake_word_dirs = [_REPO_DIR / d for d in config.wake_word.directories]
    wake_word_dirs.append(download_dir / "external_wake_words")

    available: Dict[str, AvailableWakeWord] = {}
    for wake_word_dir in wake_word_dirs:
        if not wake_word_dir.exists():
            continue
        for config_path in wake_word_dir.glob("*.json"):
            model_id = config_path.stem
            if model_id == config.wake_word.stop_model:
                continue
            with open(config_path, "r", encoding="utf-8") as f:
                model_config = json.load(f)
                model_type = WakeWordType(model_config["type"])
                
                wake_word_path = (
                    config_path.parent / model_config["model"]
                    if model_type == WakeWordType.OPEN_WAKE_WORD
                    else config_path
                )

                available[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=model_type,
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                )
    _LOGGER.debug("Available wake words: %s", list(sorted(available.keys())))

    active: Set[str] = set()
    models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}
    
    if preferences.active_wake_words:
        for ww_id in preferences.active_wake_words:
            if ww_id in available:
                _LOGGER.debug("Loading wake model (from preferences): %s", ww_id)
                models[ww_id] = available[ww_id].load()
                active.add(ww_id)
    
    if not models:
        ww_id = config.wake_word.model
        if ww_id in available:
            _LOGGER.debug("Loading wake model (from config): %s", ww_id)
            models[ww_id] = available[ww_id].load()
            active.add(ww_id)

    stop_model: Optional[MicroWakeWord] = None
    for ww_dir_str in config.wake_word.directories:
        stop_config_path = _REPO_DIR / ww_dir_str / f"{config.wake_word.stop_model}.json"
        if stop_config_path.exists():
            _LOGGER.debug("Loading stop model: %s", stop_config_path)
            stop_model = MicroWakeWord.from_config(stop_config_path)
            break
    assert stop_model is not None, "Stop model not found"

    return WakeWordData(available, models, active, stop_model)

def _init_media_players(loop: asyncio.AbstractEventLoop, config: Config, preferences: Preferences) -> MediaPlayers:
    """Initializes the music and TTS media players."""
    _LOGGER.debug("Initializing media players")
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
    return MediaPlayers(music=music_player, tts=tts_player)

def _create_server_state(
    config: Config,
    loop: asyncio.AbstractEventLoop,
    event_bus: EventBus,
    preferences: Preferences,
    wake_word_data: WakeWordData,
    media_players: MediaPlayers,
) -> ServerState:
    """Creates the global ServerState object."""
    _LOGGER.debug("Creating server state")
    return ServerState(
        name=config.app.name,
        mac_address=get_mac(),
        event_bus=event_bus,
        loop=loop,
        entities=[],
        music_player=media_players.music,
        tts_player=media_players.tts,
        available_wake_words=wake_word_data.available,
        wake_words=wake_word_data.models,
        active_wake_words=wake_word_data.active,
        stop_word=wake_word_data.stop_model,
        wakeup_sound=str(_REPO_DIR / config.app.wakeup_sound),
        timer_finished_sound=str(_REPO_DIR / config.app.timer_finished_sound),
        preferences=preferences,
        preferences_path=_REPO_DIR / config.app.preferences_file,
        download_dir=_REPO_DIR / config.wake_word.download_dir,
        refractory_seconds=config.wake_word.refractory_seconds,
    )

def _init_controllers(
    loop: asyncio.AbstractEventLoop,
    event_bus: EventBus,
    state: ServerState,
    config: Config,
    preferences: Preferences,
):
    """Initializes all decoupled controllers."""
    _LOGGER.debug("Initializing controllers")
    
    led_controller = LedController(
        loop=loop,
        event_bus=event_bus,
        config=config.led,
        preferences=preferences,
    )

    mqtt_controller: Optional[MqttController] = None
    if config.mqtt.enabled:
        mqtt_controller = MqttController(
            loop=loop,
            event_bus=event_bus,
            config=config.mqtt,
            app_name=config.app.name,
            mac_address=state.mac_address,
            preferences=preferences,
        )
        setattr(state, "mqtt_controller", mqtt_controller)
        mqtt_controller.start()

    mic_mute_handler = MicMuteHandler(
        event_bus=event_bus,
        state=state,
        mqtt_controller=mqtt_controller,
    )

async def _run_server(state: ServerState, config: Config):
    """Starts the ESPHome server and ZeroConf discovery."""
    _LOGGER.debug("Starting ESPHome server")
    server = await state.loop.create_server(
        lambda: VoiceSatelliteProtocol(state), 
        host=config.esphome.host, 
        port=config.esphome.port
    )
    discovery = HomeAssistantZeroconf(port=config.esphome.port, name=config.app.name)
    await discovery.register_server()

    async with server:
        _LOGGER.info("Server started (host=%s, port=%s)", config.esphome.host, config.esphome.port)
        await server.serve_forever()


def _process_audio(state: ServerState, mic, block_size: int):
    """Main audio processing loop. Runs in a separate thread."""
    
    micro_features: MicroWakeWordFeatures = MicroWakeWordFeatures()
    oww_features: Optional[OpenWakeWordFeatures] = None
    
    wake_words: List[Union[MicroWakeWord, OpenWakeWord]] = []
    micro_inputs: List[np.ndarray] = []
    oww_inputs: List[np.ndarray] = []
    has_oww = False
    last_active: Optional[float] = None
    
    try:
        while True:
            # --- THIS IS THE FIX ---
            # Wait while muted. This loop will spin until unmuted.
            if state.mic_muted:
                _LOGGER.debug("Audio thread muted.")
                while state.mic_muted:
                    if state.shutdown:
                        _LOGGER.debug("Shutdown signal received, stopping audio thread.")
                        return # Exit thread
                    time.sleep(0.1)

                # --- Just unmuted ---
                _LOGGER.debug("Audio thread unmuted, clearing buffers.")
                micro_features.reset()
                if oww_features is not None:
                    oww_features.reset()
                last_active = time.monotonic() # Start refractory period
                
                # Re-open microphone to flush OS buffer
                _LOGGER.debug("Re-opening microphone to flush OS buffers...")
                with mic.recorder(samplerate=16000, channels=1, blocksize=block_size) as mic_in:
                    mic_in.flush() # Discard any stale data
                    _LOGGER.debug("Microphone flushed.")
                    # Fall through to the main recording loop
            # --- END FIX ---
            
            _LOGGER.debug("Opening audio input device: %s", mic.name)
            with mic.recorder(samplerate=16000, channels=1, blocksize=block_size) as mic_in:
                # --- This is now the main recording loop ---
                while not state.mic_muted: 
                    if state.shutdown:
                        _LOGGER.debug("Shutdown signal received, stopping audio thread.")
                        return # Exit thread
                    
                    audio_chunk_array = mic_in.record(block_size).reshape(-1)
                    audio_chunk = (
                        (np.clip(audio_chunk_array, -1.0, 1.0) * 32767.0)
                        .astype("<i2")
                        .tobytes()
                    )

                    if state.satellite is None:
                        time.sleep(0.01)
                        continue
                    
                    if (not wake_words) or (state.wake_words_changed):
                        state.wake_words_changed = False
                        wake_words = [
                            ww for ww in state.wake_words.values() 
                            if ww.id in state.active_wake_words
                        ]
                        has_oww = any(isinstance(ww, OpenWakeWord) for ww in wake_words)
                        
                        if has_oww and (oww_features is None):
                            _LOGGER.debug("Initializing OpenWakeWord features...")
                            oww_features = OpenWakeWordFeatures.from_builtin()
                    
                    try:
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
                                    state.loop.call_soon_threadsafe(state.satellite.wakeup, wake_word)
                                    last_active = now
                        
                        stopped = False
                        for micro_input in micro_inputs:
                            if state.stop_word.process_streaming(micro_input):
                                stopped = True

                        if stopped and (state.stop_word.id in state.active_wake_words):
                            state.loop.call_soon_threadsafe(state.satellite.stop)
                            
                        state.loop.call_soon_threadsafe(state.satellite.handle_audio, audio_chunk)

                    except Exception: 
                        _LOGGER.exception("Unexpected error handling audio")
    
    except Exception as e:
        _LOGGER.critical("A soundcard error occurred: %s", e)
        state.loop.call_soon_threadsafe(state.loop.stop)


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print("--- __main__ block executing ---")
    asyncio.run(main())
