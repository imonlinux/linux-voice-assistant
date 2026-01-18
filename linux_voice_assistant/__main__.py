#!/usr/bin/env python3
import argparse
import asyncio
import json
import logging
import os
import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, Tuple, Union

import numpy as np
import soundcard as sc

from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
from pyopen_wakeword import OpenWakeWord

from .audio_engine import AudioEngine
from .button_controller import ButtonController
from .config import Config, load_config_from_json
from .event_bus import EventBus, EventHandler, subscribe
from .led_controller import LedController
from .mqtt_controller import MqttController
from .models import AvailableWakeWord, Preferences, ServerState, WakeWordType
from .mpv_player import MpvMediaPlayer
from .audio_volume import ensure_output_volume
from .satellite import VoiceSatelliteProtocol
from .util import get_mac_address, format_mac
from .zeroconf import HomeAssistantZeroconf
from .xvf3800_button_controller import XVF3800ButtonController  # NEW

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
# Mic Mute / Preferences Handler
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

            # Synchronize the threading event for the audio loop
            if is_muted:
                self.state.mic_muted_event.clear()  # Pauses audio thread
            else:
                self.state.mic_muted_event.set()    # Resumes audio thread

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

    @subscribe
    def set_alarm_duration(self, data: dict):
        """
        Event handler to save alarm_duration_seconds to preferences.

        Expected payload from MQTT controller:
            { "alarm_duration_seconds": <int> }

        Semantics:
            0  -> infinite alarm (only Stop/wake word stops it)
            >0 -> auto-stop alarm after N seconds (plus Stop wake word support)
        """
        duration = data.get("alarm_duration_seconds")
        if duration is None:
            return

        try:
            duration = int(duration)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Invalid alarm_duration_seconds value received: %r", duration
            )
            return

        if duration < 0:
            _LOGGER.warning(
                "Negative alarm_duration_seconds (%d) is not allowed; ignoring",
                duration,
            )
            return

        current = getattr(self.state.preferences, "alarm_duration_seconds", 0)
        if current != duration:
            _LOGGER.debug(
                "Updating alarm_duration_seconds: %s -> %s", current, duration
            )
            self.state.preferences.alarm_duration_seconds = duration
            self.state.save_preferences()

# -----------------------------------------------------------------------------
# Main Application
# -----------------------------------------------------------------------------

async def main() -> None:
    # --- 1. Load Basics ---
    config, loop, event_bus, args = _init_basics()

    # --- 2. Load Preferences ---
    preferences = _load_preferences(config)

    # --- 2b. XVF3800 Startup Workarounds (optional) ---
    _xvf3800_startup_preflight(config)

    # --- 3. Find Microphone ---
    mic = _get_microphone(config)

    # --- 4. Load Wake Words ---
    wake_word_data = _load_wake_words(config, preferences)

    # --- 5. Initialize Media Players ---
    media_players = _init_media_players(loop, config, preferences)

    # --- 5b. Sync OS sink volume to persisted volume ---
    # mpv's per-player volume is kept at 100% and ducking is handled within mpv.
    # The user-visible "volume" in HA maps to the OS output volume (PipeWire/Pulse/ALSA).
    if getattr(config.audio, "volume_sync", False):
        try:
            loop.create_task(
                ensure_output_volume(
                    volume=preferences.volume_level,
                    output_device=config.audio.output_device,
                    max_volume_percent=getattr(config.audio, "max_volume_percent", 100),
                    attempts=20,
                    delay_seconds=0.5,
                )
            )
        except Exception:
            _LOGGER.exception("Failed to schedule output volume sync")
    else:
        _LOGGER.debug("Output volume sync disabled (audio.volume_sync=false)")

    # --- 6. Create Server State ---
    state = _create_server_state(
        config, loop, event_bus, preferences,
        wake_word_data, media_players
    )

    # --- 7. Initialize Controllers ---
    _init_controllers(loop, event_bus, state, config, preferences)

    # --- 8. Start Audio Engine ---
    audio_engine = AudioEngine(
        state,
        mic,
        config.audio.input_block_size,
        oww_threshold=getattr(config.wake_word, "openwakeword_threshold", 0.5),
    )
    audio_engine.start()

    # --- 9. Run Server ---
    try:
        await _run_server(state, config)
    finally:
        # --- 10. Cleanup ---
        _LOGGER.debug("Shutting down...")
        audio_engine.stop()

        if hasattr(state, "mqtt_controller") and state.mqtt_controller:
            _LOGGER.debug("Stopping MQTT controller...")
            state.mqtt_controller.stop()

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _init_basics() -> Tuple[Config, asyncio.AbstractEventLoop, EventBus, argparse.Namespace]:
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

    # Optional CLI override to match upstream style
    parser.add_argument(
        "--wake-word-threshold",
        type=float,
        default=None,
        help="OpenWakeWord activation threshold (0.0-1.0). Overrides wake_word.openwakeword_threshold in config.json",
    )

    args = parser.parse_args()

    if args.list_input_devices:
        print("Input devices\n" + "=" * 13)
        try:
            for idx, mic in enumerate(sc.all_microphones(include_loopback=False)):
                print(f"[{idx}]", mic.name)
        except Exception as e:
            _LOGGER.error(
                "Error listing input devices (ensure audio backend is working): %s",
                e,
            )
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
        sys.exit(0)

    config_path = args.config
    if not config_path.is_absolute():
        config_path = _REPO_DIR / config_path
    config = load_config_from_json(config_path)

    if args.debug:
        config.app.debug = True

    # CLI override for OWW threshold (validated/clamped later by AudioEngine too)
    if args.wake_word_threshold is not None:
        try:
            config.wake_word.openwakeword_threshold = float(args.wake_word_threshold)
        except Exception:
            _LOGGER.warning(
                "Invalid --wake-word-threshold value %r; keeping config value %.2f",
                args.wake_word_threshold,
                getattr(config.wake_word, "openwakeword_threshold", 0.5),
            )

    logging.basicConfig(
        level=logging.DEBUG if config.app.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _LOGGER.info("Loading configuration from: %s", config_path)

    loop = asyncio.get_running_loop()
    event_bus = EventBus()

    return config, loop, event_bus, args

def _load_preferences(config: Config) -> Preferences:
    """Loads preferences.json file."""
    preferences_path = _REPO_DIR / config.app.preferences_file
    if preferences_path.exists():
        with open(preferences_path, "r", encoding="utf-8") as f:
            preferences_dict = json.load(f)
            preferences = Preferences(**preferences_dict)
    else:
        preferences = Preferences()

    # Backwards-compatible defaults / migrations
    preferences.num_leds = getattr(preferences, "num_leds", config.led.num_leds)
    # New: default alarm_duration_seconds, 0 = infinite until Stop/wake word
    preferences.alarm_duration_seconds = getattr(
        preferences, "alarm_duration_seconds", 0
    )

    return preferences


def _xvf3800_startup_preflight(config: Config) -> None:
    """
    Best-effort XVF3800 USB preflight.

    Some platforms (notably certain SBC USB controllers) can leave the XVF3800
    capture endpoint "silent" until the device is rebooted.

    If an XVF3800 is configured (LED/button/audio), we optionally:
      1) issue an XVF3800 REBOOT
      2) wait for USB re-enumeration
      3) set AUDIO_MGR_OP_L and AUDIO_MGR_OP_R to (7, 3) to force both channels
         to the ASR3 output (as discovered via xvf_host.py testing)

    This uses PyUSB directly (no dependency on xvf_host.py).
    """
    try:
        # Determine whether XVF3800 is in use at all
        led_cfg = getattr(config, "led", None)
        btn_cfg = getattr(config, "button", None)
        aud_cfg = getattr(config, "audio", None)

        uses_xvf = False
        if led_cfg and getattr(led_cfg, "led_type", "").lower() == "xvf3800":
            uses_xvf = True
        if btn_cfg and getattr(btn_cfg, "enabled", False) and getattr(btn_cfg, "mode", "").lower() == "xvf3800":
            uses_xvf = True
        if aud_cfg and isinstance(getattr(aud_cfg, "input_device", None), str) and "xvf3800" in aud_cfg.input_device.lower():
            uses_xvf = True

        if not uses_xvf:
            return

        # Env toggles (default enabled)
        do_reboot = os.environ.get("LVA_XVF3800_STARTUP_REBOOT", "1").strip().lower() not in ("0", "false", "no", "off")
        do_route = os.environ.get("LVA_XVF3800_STARTUP_SET_ASR3", "1").strip().lower() not in ("0", "false", "no", "off")
        do_save  = os.environ.get("LVA_XVF3800_STARTUP_SAVE_CONFIG", "0").strip().lower() in ("1", "true", "yes", "on")

        if not (do_reboot or do_route):
            return

        from .xvf3800_led_backend import XVF3800USBDevice

        _LOGGER.info("XVF3800 startup preflight: begin (reboot=%s, set_asr3=%s, save=%s)", do_reboot, do_route, do_save)

        if do_reboot:
            try:
                dev = XVF3800USBDevice()
                _LOGGER.info("XVF3800 startup preflight: issuing REBOOT to USB device")
                dev.reboot()
            finally:
                try:
                    dev.close()
                except Exception:
                    pass

            # Wait for USB to cycle
            XVF3800USBDevice.wait_for_reenumeration(timeout_s=12.0, settle_s=1.0)

        if do_route:
            dev2 = XVF3800USBDevice()
            try:
                _LOGGER.info("XVF3800 startup preflight: setting AUDIO_MGR_OP_L and AUDIO_MGR_OP_R to (7, 3)")
                dev2.set_audio_mgr_op_l(7, 3)
                dev2.set_audio_mgr_op_r(7, 3)
                if do_save:
                    _LOGGER.info("XVF3800 startup preflight: saving configuration to flash")
                    dev2.save_configuration()
            finally:
                dev2.close()

        _LOGGER.info("XVF3800 startup preflight: done")

    except Exception as e:
        _LOGGER.warning("XVF3800 startup preflight failed (continuing): %s", e)



def _get_microphone(config: Config):
    """Finds and returns the microphone specified in the config."""
    mic = None
    input_spec = getattr(config.audio, "input_device", None)

    # If the user specified an index, honor it.
    if input_spec is not None:
        try:
            input_device_idx = int(input_spec)
            mic = sc.all_microphones(include_loopback=False)[input_device_idx]
        except (ValueError, IndexError):
            # If the user specified a name, retry a bit (helps after USB re-enumeration / slow PipeWire startup).
            want_name = str(input_spec)
            is_xvf = "xvf3800" in want_name.lower()
            deadline = time.time() + (20.0 if is_xvf else 5.0)

            last_err: Optional[Exception] = None
            while time.time() < deadline:
                try:
                    mic = sc.get_microphone(want_name, include_loopback=False)
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.5)

            if mic is None and last_err is not None:
                _LOGGER.warning("Failed to open configured mic %r after retries: %s", want_name, last_err)
    else:
        mic = sc.default_microphone()

    if mic is None:
        _LOGGER.critical("No microphone found.")
        sys.exit(1)

    _LOGGER.info("Using audio input device: %s", mic.name)
    return mic

def _load_wake_words(config: Config, preferences: Preferences) -> WakeWordData:
    """Loads all available and active wake word models."""
    if not config.wake_word.directories:
        config.wake_word.directories = ["wakewords", "wakewords/openWakeWord"]

    download_dir = _REPO_DIR / config.wake_word.download_dir
    download_dir.mkdir(parents=True, exist_ok=True)

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

                # Per-model OpenWakeWord threshold override.
                # Supported keys in wakeword model json:
                # - "threshold" (preferred)
                # - "openwakeword_threshold" (alias)
                oww_threshold = None
                if model_type == WakeWordType.OPEN_WAKE_WORD:
                    if "threshold" in model_config:
                        oww_threshold = model_config.get("threshold")
                    elif "openwakeword_threshold" in model_config:
                        oww_threshold = model_config.get("openwakeword_threshold")

                available[model_id] = AvailableWakeWord(
                    id=model_id,
                    type=model_type,
                    wake_word=model_config["wake_word"],
                    trained_languages=model_config.get("trained_languages", []),
                    wake_word_path=wake_word_path,
                    oww_threshold=oww_threshold,
                )

    active: Set[str] = set()
    models: Dict[str, Union[MicroWakeWord, OpenWakeWord]] = {}

    if preferences.active_wake_words:
        for ww_id in preferences.active_wake_words:
            if ww_id in available:
                models[ww_id] = available[ww_id].load()
                active.add(ww_id)

    if not models:
        ww_id = config.wake_word.model
        if ww_id in available:
            models[ww_id] = available[ww_id].load()
            active.add(ww_id)

    stop_model: Optional[MicroWakeWord] = None
    for ww_dir_str in config.wake_word.directories:
        stop_config_path = _REPO_DIR / ww_dir_str / f"{config.wake_word.stop_model}.json"
        if stop_config_path.exists():
            stop_model = MicroWakeWord.from_config(stop_config_path)
            break
    assert stop_model is not None, "Stop model not found"

    return WakeWordData(available, models, active, stop_model)

def _init_media_players(
    loop: asyncio.AbstractEventLoop,
    config: Config,
    preferences: Preferences,
) -> MediaPlayers:
    """Initializes the music and TTS media players."""
    music_player = MpvMediaPlayer(
        loop=loop,
        device=config.audio.output_device,
        initial_volume=preferences.volume_level,
    )
    tts_player = MpvMediaPlayer(
        loop=loop,
        device=config.audio.output_device,
        initial_volume=preferences.volume_level,
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
    stable_mac = format_mac(get_mac_address())
    return ServerState(
        name=config.app.name,
        mac_address=stable_mac,
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

    # Hardware / XVF3800 mute button controller
    try:
        button_cfg = getattr(config, "button", None)
        if button_cfg is not None and button_cfg.enabled:
            mode = getattr(button_cfg, "mode", "gpio").lower()
            if mode == "xvf3800":
                _LOGGER.info("Initializing XVF3800ButtonController (mode=xvf3800)")
                xvf_btn = XVF3800ButtonController(
                    loop=loop,
                    event_bus=event_bus,
                    state=state,
                    button_config=button_cfg,
                )
                setattr(state, "xvf3800_button_controller", xvf_btn)
            else:
                _LOGGER.info("Initializing GPIO ButtonController (mode=gpio)")
                button_controller = ButtonController(
                    loop=loop,
                    event_bus=event_bus,
                    state=state,
                    config=button_cfg,
                )
                setattr(state, "button_controller", button_controller)
        else:
            _LOGGER.debug("Button controller not enabled in config; skipping")
    except Exception:
        _LOGGER.exception("Failed to initialize button controller(s)")

async def _run_server(state: ServerState, config: Config):
    """Starts the ESPHome server and ZeroConf discovery."""
    server = await state.loop.create_server(
        lambda: VoiceSatelliteProtocol(state),
        host=config.esphome.host,
        port=config.esphome.port,
    )
    discovery = HomeAssistantZeroconf(port=config.esphome.port, name=config.app.name)
    await discovery.register_server()

    async with server:
        _LOGGER.info(
            "Server started (host=%s, port=%s)", config.esphome.host, config.esphome.port
        )
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
