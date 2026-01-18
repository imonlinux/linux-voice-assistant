"""Configuration models for the application."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class AppConfig:
    """General application settings."""
    name: str
    wakeup_sound: str = "sounds/wake_word_triggered.flac"
    timer_finished_sound: str = "sounds/timer_finished.flac"
    preferences_file: str = "preferences.json"
    debug: bool = False


@dataclass
class AudioConfig:
    """Settings for audio input and output."""
    input_device: Optional[str] = None
    input_block_size: int = 1024
    output_device: Optional[str] = None

    # If True, LVA will attempt to set the OS sink volume to match the persisted
    # volume in preferences.json on startup (PipeWire/PulseAudio/ALSA best-effort).
    # Default is False to avoid surprising changes on systems where users manage
    # volume externally.
    volume_sync: bool = False

    # Maximum sink volume to map to LVA's 100% volume.
    #
    # Example: if max_volume_percent=150 then when LVA's media player reports
    # 100% (preferences.volume_level == 1.0), the underlying sink will be set
    # to 150% (or 1.5 for backends that use scalar volume).
    #
    # This is useful for devices that need >100% gain on PipeWire/Pulse/ALSA.
    max_volume_percent: int = 100


@dataclass
class WakeWordConfig:
    """Settings for wake word detection."""
    directories: List[str] = field(default_factory=list)
    model: str = "okay_nabu"
    stop_model: str = "stop"
    refractory_seconds: float = 2.0
    download_dir: str = "local"

    # OpenWakeWord activation threshold.
    # A wake word triggers when model probability exceeds this value.
    # Range: 0.0 - 1.0
    openwakeword_threshold: float = 0.5


@dataclass
class ESPHomeConfig:
    """Settings for the ESPHome API server."""
    host: str = "0.0.0.0"
    port: int = 6053


@dataclass
class LedConfig:
    """Settings for LEDs."""
    enabled: bool = True

    # Supported values include:
    # - "dotstar" / "neopixel" for Pi-attached LED strips
    # - "xvf3800" for the ReSpeaker XVF3800 USB LED ring backend
    led_type: str = "dotstar"

    # For dotstar/neopixel: "spi" or "gpio"
    # For xvf3800: "usb"
    interface: str = "spi"

    # GPIO pin numbers used when interface="gpio"
    clock_pin: int = 13
    data_pin: int = 12

    # Note: overridden by 'preferences.json' if it exists
    num_leds: int = 3


@dataclass
class MqttConfig:
    """Settings for the MQTT client."""
    enabled: bool = False
    host: Optional[str] = None
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class ButtonConfig:
    """Settings for a hardware momentary button."""

    # Overall enable/disable (default: off)
    enabled: bool = False

    # mode:
    #  - "gpio"   -> legacy GPIO button (e.g. ReSpeaker 2-Mic HAT)
    #  - "xvf3800"-> USB-based mute integration for the ReSpeaker XVF3800
    mode: str = "gpio"

    # BCM GPIO pin number for the button input (gpio mode only).
    pin: int = 17

    # Press duration (in seconds) to be considered a "long press" (gpio mode).
    long_press_seconds: float = 1.0

    # Poll interval used by both GPIO and XVF3800 controllers.
    poll_interval_seconds: float = 0.01


@dataclass
class Config:
    """Main configuration object."""
    app: AppConfig
    audio: AudioConfig = field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    esphome: ESPHomeConfig = field(default_factory=ESPHomeConfig)
    led: LedConfig = field(default_factory=LedConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)

# -----------------------------------------------------------------------------
# Helper Function
# -----------------------------------------------------------------------------

def _clamp_0_1(name: str, value: float) -> float:
    """Clamp a float to [0.0, 1.0], logging a warning if clamped."""
    try:
        v = float(value)
    except Exception:
        _LOGGER.warning("%s is not a number (%r); using default 0.5", name, value)
        return 0.5

    if v < 0.0:
        _LOGGER.warning("%s < 0.0; clamping to 0.0 (was %s)", name, v)
        return 0.0
    if v > 1.0:
        _LOGGER.warning("%s > 1.0; clamping to 1.0 (was %s)", name, v)
        return 1.0
    return v


def load_config_from_json(config_path: Path) -> Config:
    """Loads configuration from a JSON file and populates dataclasses."""

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except FileNotFoundError:
        _LOGGER.critical("Configuration file not found at: %s", config_path)
        raise
    except json.JSONDecodeError as e:
        _LOGGER.critical("Error parsing configuration file: %s", e)
        raise

    if "app" not in raw_data:
        raise ValueError("Configuration file must contain an 'app' section with a 'name'.")

    app_config = AppConfig(**raw_data.get("app", {}))
    audio_config = AudioConfig(**raw_data.get("audio", {}))
    wake_word_config = WakeWordConfig(**raw_data.get("wake_word", {}))
    esphome_config = ESPHomeConfig(**raw_data.get("esphome", {}))
    led_config = LedConfig(**raw_data.get("led", {}))
    mqtt_config = MqttConfig(**raw_data.get("mqtt", {}))
    button_config = ButtonConfig(**raw_data.get("button", {}))

    # Normalize / validate wake word threshold
    wake_word_config.openwakeword_threshold = _clamp_0_1(
        "wake_word.openwakeword_threshold",
        getattr(wake_word_config, "openwakeword_threshold", 0.5),
    )

    # Back-compat: allow top-level "volume_sync" (preferred location is audio.volume_sync)
    if "volume_sync" in raw_data and "volume_sync" not in raw_data.get("audio", {}):
        try:
            audio_config.volume_sync = bool(raw_data.get("volume_sync"))
        except Exception:
            # If it's something strange, just leave default.
            pass

    # Set MQTT 'enabled' flag
    if mqtt_config.host:
        mqtt_config.enabled = True

    return Config(
        app=app_config,
        audio=audio_config,
        wake_word=wake_word_config,
        esphome=esphome_config,
        led=led_config,
        mqtt=mqtt_config,
        button=button_config,
    )
