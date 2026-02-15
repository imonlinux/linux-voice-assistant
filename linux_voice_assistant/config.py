"""Configuration models for the application.

This module is intentionally defensive:
- Unknown keys in JSON config blocks are ignored (with a warning) rather than
  crashing the app.
- Certain fields are normalized to the expected types.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

_LOGGER = logging.getLogger(__name__)

T = TypeVar("T")


# -----------------------------------------------------------------------------
# Helpers
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


def _as_str_list(value: Any, *, default: Optional[List[str]] = None) -> List[str]:
    """Normalize a config value into List[str]."""
    if value is None:
        return list(default) if default is not None else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            out.append(str(item))
        return out
    # Accept a single string as a 1-item list.
    return [str(value)]


def _dataclass_from_dict(cls: Type[T], raw: Any, *, context: str) -> T:
    """Create a dataclass instance from a dict, ignoring unknown keys.

    This prevents config.json typos or future/extra keys from crashing startup.
    """
    if not isinstance(raw, dict):
        raw = {}

    allowed = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in raw.items() if k in allowed}
    unknown = sorted({k for k in raw.keys() if k not in allowed})
    if unknown:
        _LOGGER.warning("%s: ignoring unknown keys: %s", context, unknown)

    try:
        return cls(**filtered)  # type: ignore[arg-type]
    except Exception as e:
        _LOGGER.warning("%s: failed to parse (%s); using defaults. raw=%r", context, e, raw)
        return cls()  # type: ignore[call-arg]


# -----------------------------------------------------------------------------
# Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class AppConfig:
    """General application settings."""
    name: str
    wakeup_sound: str = "sounds/wake_word_triggered.flac"
    thinking_sound: str = "sounds/nothing.flac"
    timer_finished_sound: str = "sounds/timer_finished.flac"

    # Master toggle for event sounds (wakeup + thinking).
    # The timer alarm is NOT gated by this flag — it is a functional alert
    # and will always play regardless of this setting.
    event_sounds_enabled: bool = True

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
    long_press_seconds: float = 2.0


@dataclass
class TrayConfig:
    """Settings for the tray client."""
    systemd_service_name: str = "linux-voice-assistant.service"


# -----------------------------------------------------------------------------
# Sendspin sub-configs
# -----------------------------------------------------------------------------

@dataclass
class SendspinConnectionConfig:
    """How to find / connect to a Sendspin server."""
    host: Optional[str] = None
    port: int = 8888
    path: str = "/sendspin"
    use_mdns: bool = True
    reconnect_delay: float = 5.0
    connect_timeout: float = 10.0


@dataclass
class SendspinRolesConfig:
    """Which Sendspin protocol roles to activate."""
    player: bool = True
    controller: bool = False


@dataclass
class SendspinPlayerConfig:
    """Player-role settings for codec negotiation, buffering, and mpv.

    - `preferred_codec` and `supported_codecs` are advertised during handshake.
    - `mpv_*` and `ffmpeg_*` are pass-through knobs for downstream work.
    """

    preferred_codec: str = "pcm"
    supported_codecs: List[str] = field(default_factory=lambda: ["pcm"])
    sample_rate: int = 48000
    channels: int = 2
    bit_depth: int = 16

    # Approximate stream buffer capacity to advertise (bytes).
    # This is used by the Sendspin server to choose chunk sizing.
    buffer_capacity_bytes: int = 1048576  # 1 MiB

    # Ducking level applied to mpv volume when voice is active (0-100).
    duck_volume_percent: int = 20

    # Local playback process configuration
    mpv_path: str = "mpv"
    mpv_ao: Optional[str] = None
    mpv_audio_device: Optional[str] = None
    mpv_extra_args: List[str] = field(default_factory=list)

    # Decoder configuration (Milestone 4: wired-through, used in later milestones)
    decoder_backend: str = "auto"  # auto|ffmpeg|none
    ffmpeg_path: str = "ffmpeg"
    ffmpeg_extra_args: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.preferred_codec = str(self.preferred_codec or "pcm").lower().strip()

        # Normalize codecs lists
        codecs = _as_str_list(self.supported_codecs, default=["pcm"])
        codecs_norm: List[str] = []
        for c in codecs:
            c2 = str(c).lower().strip()
            if not c2:
                continue
            if c2 not in codecs_norm:
                codecs_norm.append(c2)
        if "pcm" not in codecs_norm:
            codecs_norm.append("pcm")
        self.supported_codecs = codecs_norm

        if self.preferred_codec not in self.supported_codecs:
            _LOGGER.warning(
                "sendspin.player.preferred_codec=%r not in supported_codecs=%r; falling back to 'pcm'",
                self.preferred_codec,
                self.supported_codecs,
            )
            self.preferred_codec = "pcm"

        self.mpv_extra_args = _as_str_list(self.mpv_extra_args, default=[])
        self.ffmpeg_extra_args = _as_str_list(self.ffmpeg_extra_args, default=[])

        self.decoder_backend = str(self.decoder_backend or "auto").lower().strip()
        if self.decoder_backend not in ("auto", "ffmpeg", "none"):
            _LOGGER.warning("sendspin.player.decoder_backend=%r invalid; using 'auto'", self.decoder_backend)
            self.decoder_backend = "auto"


@dataclass
class SendspinAudioOutputConfig:
    """Local audio output settings for Sendspin playback."""
    backend: str = "soundcard"  # Phase 1: soundcard experiment
    device: Optional[str] = None  # None -> default output device
    block_ms: int = 20
    prebuffer_ms: int = 300


@dataclass
class SendspinCoordinationConfig:
    """Coordination between voice interaction and Sendspin playback."""
    duck_during_voice: bool = True
    duck_gain: float = 0.3  # 0.0-1.0 multiplier applied to PCM samples
    on_error: str = "mute"  # "mute" | "stop"


@dataclass
class SendspinConfig:
    """Top-level Sendspin config block."""
    enabled: bool = False
    connection: SendspinConnectionConfig = field(default_factory=SendspinConnectionConfig)
    roles: SendspinRolesConfig = field(default_factory=SendspinRolesConfig)
    player: SendspinPlayerConfig = field(default_factory=SendspinPlayerConfig)
    audio_output: SendspinAudioOutputConfig = field(default_factory=SendspinAudioOutputConfig)
    coordination: SendspinCoordinationConfig = field(default_factory=SendspinCoordinationConfig)


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
    sendspin: SendspinConfig = field(default_factory=SendspinConfig)


# -----------------------------------------------------------------------------
# Loader
# -----------------------------------------------------------------------------

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

    app_config = _dataclass_from_dict(AppConfig, raw_data.get("app", {}), context="app")
    audio_config = _dataclass_from_dict(AudioConfig, raw_data.get("audio", {}), context="audio")
    wake_word_config = _dataclass_from_dict(WakeWordConfig, raw_data.get("wake_word", {}), context="wake_word")
    esphome_config = _dataclass_from_dict(ESPHomeConfig, raw_data.get("esphome", {}), context="esphome")
    led_config = _dataclass_from_dict(LedConfig, raw_data.get("led", {}), context="led")
    mqtt_config = _dataclass_from_dict(MqttConfig, raw_data.get("mqtt", {}), context="mqtt")
    button_config = _dataclass_from_dict(ButtonConfig, raw_data.get("button", {}), context="button")

    # --- Sendspin (nested dataclasses; keep robust to partial configs) ---
    sendspin_raw = raw_data.get("sendspin", {})
    if not isinstance(sendspin_raw, dict):
        sendspin_raw = {}

    sendspin_cfg = SendspinConfig(
        enabled=bool(sendspin_raw.get("enabled", False)),
        connection=_dataclass_from_dict(
            SendspinConnectionConfig, (sendspin_raw.get("connection", {}) or {}), context="sendspin.connection"
        ),
        roles=_dataclass_from_dict(
            SendspinRolesConfig, (sendspin_raw.get("roles", {}) or {}), context="sendspin.roles"
        ),
        player=_dataclass_from_dict(
            SendspinPlayerConfig, (sendspin_raw.get("player", {}) or {}), context="sendspin.player"
        ),
        audio_output=_dataclass_from_dict(
            SendspinAudioOutputConfig, (sendspin_raw.get("audio_output", {}) or {}), context="sendspin.audio_output"
        ),
        coordination=_dataclass_from_dict(
            SendspinCoordinationConfig, (sendspin_raw.get("coordination", {}) or {}), context="sendspin.coordination"
        ),
    )

    # Normalize / validate wake word threshold
    wake_word_config.openwakeword_threshold = _clamp_0_1(
        "wake_word.openwakeword_threshold",
        getattr(wake_word_config, "openwakeword_threshold", 0.5),
    )

    # Normalize / validate Sendspin duck_gain
    sendspin_cfg.coordination.duck_gain = _clamp_0_1(
        "sendspin.coordination.duck_gain",
        getattr(sendspin_cfg.coordination, "duck_gain", 0.3),
    )

    # Back-compat: allow top-level "volume_sync" (preferred location is audio.volume_sync)
    if "volume_sync" in raw_data and "volume_sync" not in raw_data.get("audio", {}):
        try:
            audio_config.volume_sync = bool(raw_data.get("volume_sync"))
        except Exception:
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
        sendspin=sendspin_cfg,
    )
