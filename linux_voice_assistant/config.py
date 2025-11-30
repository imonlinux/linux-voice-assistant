"""Configuration models for the application."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import logging
_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class AudioConfig:
    """Settings for audio input and output."""
    input_device: Optional[str] = None
    input_block_size: int = 1024
    output_device: Optional[str] = None


@dataclass
class WakeWordConfig:
    """Settings for wake word detection."""
    directories: List[str] = field(default_factory=list)
    model: str = "okay_nabu"
    stop_model: str = "stop"
    refractory_seconds: float = 2.0
    download_dir: str = "local"


@dataclass
class ESPHomeConfig:
    """Settings for the ESPHome API server."""
    host: str = "0.0.0.0"
    port: int = 6053


@dataclass
class LedConfig:
    """Settings for the LED strip."""
    led_type: str = "dotstar"
    interface: str = "spi"
    clock_pin: int = 13
    data_pin: int = 12
    num_leds: int = 3  # Note: overridden by 'preferences.json' if it exists


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
    """Settings for a hardware momentary button (e.g. ReSpeaker 2-Mic HAT)."""
    enabled: bool = False
    # BCM GPIO pin number for the button input.
    pin: int = 17
    # Press duration (in seconds) to be considered a "long press".
    long_press_seconds: float = 1.0


@dataclass
class AppConfig:
    """General application settings."""
    name: str
    wakeup_sound: str = "sounds/wake_word_triggered.flac"
    timer_finished_sound: str = "sounds/timer_finished.flac"
    preferences_file: str = "preferences.json"
    debug: bool = False


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

def load_config_from_json(config_path: Path) -> Config:
    """Loads configuration from a JSON file and populates dataclasses."""

    # --- Step 1: Load raw JSON data ---
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except FileNotFoundError:
        _LOGGER.critical(f"Configuration file not found at: {config_path}")
        raise
    except json.JSONDecodeError as e:
        _LOGGER.critical(f"Error parsing configuration file: {e}")
        raise

    # --- Step 2: Create config objects from raw data ---
    if "app" not in raw_data:
        raise ValueError(
            "Configuration file must contain an 'app' section with a 'name'."
        )

    app_config = AppConfig(**raw_data.get("app", {}))
    audio_config = AudioConfig(**raw_data.get("audio", {}))
    wake_word_config = WakeWordConfig(**raw_data.get("wake_word", {}))
    esphome_config = ESPHomeConfig(**raw_data.get("esphome", {}))
    led_config = LedConfig(**raw_data.get("led", {}))
    mqtt_config = MqttConfig(**raw_data.get("mqtt", {}))
    button_config = ButtonConfig(**raw_data.get("button", {}))

    # --- Step 3: Set MQTT 'enabled' flag ---
    if mqtt_config.host:
        mqtt_config.enabled = True

    # --- Step 4: Return the main Config object ---
    return Config(
        app=app_config,
        audio=audio_config,
        wake_word=wake_word_config,
        esphome=esphome_config,
        led=led_config,
        mqtt=mqtt_config,
        button=button_config,
    )
