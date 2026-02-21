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


def _clamp_min(name: str, value: Any, *, minimum: float) -> float:
    """Clamp a numeric value to be >= minimum, logging if clamped.

    Returns the numeric value as float.
    """
    try:
        v = float(value)
    except Exception:
        _LOGGER.warning("%s is not a number (%r); using %s", name, value, minimum)
        return float(minimum)
    if v < float(minimum):
        _LOGGER.warning("%s < %s; clamping to %s (was %s)", name, minimum, minimum, v)
        return float(minimum)
    return v


def _clamp_int_min(name: str, value: Any, *, minimum: int) -> int:
    """Clamp an integer value to be >= minimum, logging if clamped."""
    try:
        v = int(value)
    except Exception:
        _LOGGER.warning("%s is not an int (%r); using %s", name, value, minimum)
        return int(minimum)
    if v < int(minimum):
        _LOGGER.warning("%s < %s; clamping to %s (was %s)", name, minimum, minimum, v)
        return int(minimum)
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

    return cls(**filtered)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Core Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class AppConfig:
    """Top-level app configuration."""
    name: str
    wakeup_sound: str = "sounds/wakeup/wake_word_triggered.flac"
    thinking_sound: str = "sounds/thinking/nothing.flac"
    thinking_sound_loop: bool = False    
    timer_finished_sound: str = "sounds/timer/timer_finished.flac"

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
    """Wake word configuration."""
    directories: List[str] = field(default_factory=list)
    model: str = "okay_nabu"
    stop_model: str = "stop"
    refractory_seconds: float = 2.0
    download_dir: str = "local"
    openwakeword_threshold: float = 0.5


@dataclass
class ESPHomeConfig:
    """Settings for the built-in ESPHome API server."""
    host: str = "0.0.0.0"
    port: int = 6053


@dataclass
class LedConfig:
    """LED controller configuration."""
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
    """MQTT configuration."""
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
# Sendspin Configuration Dataclasses
# -----------------------------------------------------------------------------

@dataclass
class SendspinConnectionConfig:
    """Sendspin connection settings.

    mode:
      - "client_initiated": LVA discovers Sendspin servers via mDNS and connects.
      - "server_initiated": LVA advertises itself and accepts server connections.
    """
    mode: str = "client_initiated"
    mdns: bool = True
    server_host: Optional[str] = None
    server_port: int = 8927
    server_path: str = "/sendspin"

    # Optional tuning
    timeout_seconds: float = 6.0
    hello_timeout_seconds: float = 8.0
    ping_interval_seconds: float = 20.0
    ping_timeout_seconds: float = 20.0
    time_sync_interval_seconds: float = 5.0

    # Clock sync adaptive scheduling (Sendspin client)
    # When time_sync_adaptive is enabled, the client will adjust its polling interval
    # within [time_sync_min_interval_seconds, time_sync_max_interval_seconds].
    time_sync_adaptive: bool = True
    time_sync_min_interval_seconds: float = 0.5
    time_sync_max_interval_seconds: float = 5.0

    # Burst probing parameters (Sendspin client)
    # Sends time_sync_burst_size probes spaced by time_sync_burst_spacing_seconds,
    # then waits time_sync_burst_grace_seconds for late responses.
    time_sync_burst_size: int = 8
    time_sync_burst_spacing_seconds: float = 0.1
    time_sync_burst_grace_seconds: float = 1.5

    # Accepted by config to avoid warnings; only has effect if/when implemented in the client.
    time_sync_burst_on_connect: bool = True
    time_sync_burst_on_stream_start: bool = True


@dataclass
class SendspinRolesConfig:
    """Enable/disable Sendspin roles."""
    player: bool = True
    metadata: bool = True
    controller: bool = True
    artwork: bool = False
    visualizer: bool = False


@dataclass
class SendspinPlayerConfig:
    """Player capability preferences for Sendspin.

    Notes:
    - `supported_codecs` controls what we advertise in `client/hello`.
    - Actual playback is currently PCM-only; if the server starts a stream with
      a non-PCM codec, the client will request a PCM format and log a warning.
    - `mpv_*` and `ffmpeg_*` are pass-through knobs for downstream work.
    """

    preferred_codec: str = "pcm"
    supported_codecs: List[str] = field(default_factory=lambda: ["pcm"])
    sample_rate: int = 48000
    channels: int = 2
    bit_depth: int = 16

    # ---------------------------------------------------------------------
    # Sync / buffering knobs (timestamp-aware jitter buffer)
    # ---------------------------------------------------------------------
    sync_target_latency_ms: int = 250
    sync_late_drop_ms: int = 150
    output_latency_ms: int = 0
    clear_drop_window_ms: int = 2000

    # Approximate stream buffer capacity to advertise (bytes).
    # This is used by the Sendspin server to choose chunk sizing.
    buffer_capacity_bytes: int = 1048576  # 1 MiB

    # Player-side commands the client will accept from the server.
    supported_commands: List[str] = field(default_factory=lambda: ["volume", "mute"])

    # Ducking level applied to mpv volume when voice is active (0-100).
    # NOTE: the Sendspin client currently uses sendspin.coordination.duck_gain.
    # This value is retained for backwards compatibility and for UI/Docs.
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

        # Normalize supported commands list
        cmds = _as_str_list(getattr(self, "supported_commands", None), default=["volume", "mute"])
        cmds_norm: List[str] = []
        for c in cmds:
            c2 = str(c).lower().strip()
            if not c2:
                continue
            if c2 not in cmds_norm:
                cmds_norm.append(c2)
        self.supported_commands = cmds_norm

        # Normalize ms-based knobs to ints (and basic sanity)
        self.sync_target_latency_ms = int(self.sync_target_latency_ms)
        self.sync_late_drop_ms = int(self.sync_late_drop_ms)
        self.output_latency_ms = int(self.output_latency_ms)
        self.clear_drop_window_ms = int(self.clear_drop_window_ms)

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

    # Normalize / validate Sendspin time sync settings
    sendspin_cfg.connection.time_sync_interval_seconds = _clamp_min(
        "sendspin.connection.time_sync_interval_seconds",
        getattr(sendspin_cfg.connection, "time_sync_interval_seconds", 5.0),
        minimum=0.0,
    )
    sendspin_cfg.connection.time_sync_min_interval_seconds = _clamp_min(
        "sendspin.connection.time_sync_min_interval_seconds",
        getattr(sendspin_cfg.connection, "time_sync_min_interval_seconds", 0.5),
        minimum=0.05,
    )
    sendspin_cfg.connection.time_sync_max_interval_seconds = _clamp_min(
        "sendspin.connection.time_sync_max_interval_seconds",
        getattr(sendspin_cfg.connection, "time_sync_max_interval_seconds", 5.0),
        minimum=0.05,
    )
    if sendspin_cfg.connection.time_sync_max_interval_seconds < sendspin_cfg.connection.time_sync_min_interval_seconds:
        _LOGGER.warning(
            "sendspin.connection.time_sync_max_interval_seconds < time_sync_min_interval_seconds; swapping values (%s < %s)",
            sendspin_cfg.connection.time_sync_max_interval_seconds,
            sendspin_cfg.connection.time_sync_min_interval_seconds,
        )
        sendspin_cfg.connection.time_sync_min_interval_seconds, sendspin_cfg.connection.time_sync_max_interval_seconds = (
            sendspin_cfg.connection.time_sync_max_interval_seconds,
            sendspin_cfg.connection.time_sync_min_interval_seconds,
        )

    sendspin_cfg.connection.time_sync_burst_size = _clamp_int_min(
        "sendspin.connection.time_sync_burst_size",
        getattr(sendspin_cfg.connection, "time_sync_burst_size", 8),
        minimum=1,
    )
    sendspin_cfg.connection.time_sync_burst_spacing_seconds = _clamp_min(
        "sendspin.connection.time_sync_burst_spacing_seconds",
        getattr(sendspin_cfg.connection, "time_sync_burst_spacing_seconds", 0.1),
        minimum=0.0,
    )
    sendspin_cfg.connection.time_sync_burst_grace_seconds = _clamp_min(
        "sendspin.connection.time_sync_burst_grace_seconds",
        getattr(sendspin_cfg.connection, "time_sync_burst_grace_seconds", 1.5),
        minimum=0.0,
    )

    # Normalize / validate Sendspin player sync knobs
    sendspin_cfg.player.sync_target_latency_ms = _clamp_int_min(
        "sendspin.player.sync_target_latency_ms",
        getattr(sendspin_cfg.player, "sync_target_latency_ms", 250),
        minimum=0,
    )
    sendspin_cfg.player.sync_late_drop_ms = _clamp_int_min(
        "sendspin.player.sync_late_drop_ms",
        getattr(sendspin_cfg.player, "sync_late_drop_ms", 150),
        minimum=0,
    )
    # output_latency_ms may be negative; don't clamp to 0.
    try:
        sendspin_cfg.player.output_latency_ms = int(getattr(sendspin_cfg.player, "output_latency_ms", 0))
    except Exception:
        _LOGGER.warning(
            "sendspin.player.output_latency_ms is not an int (%r); using 0",
            getattr(sendspin_cfg.player, "output_latency_ms", 0),
        )
        sendspin_cfg.player.output_latency_ms = 0

    sendspin_cfg.player.clear_drop_window_ms = _clamp_int_min(
        "sendspin.player.clear_drop_window_ms",
        getattr(sendspin_cfg.player, "clear_drop_window_ms", 2000),
        minimum=0,
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
