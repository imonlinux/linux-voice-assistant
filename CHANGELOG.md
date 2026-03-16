# Changelog

## 1.0.0

- Initial release (https://github.com/OHF-Voice/linux-voice-assistant)

## Unreleased Fork 
(https://github.com/imonlinux/linux-voice-assistant)

### Added

**Full MQTT Home Assistant Integration**
- MQTT Discovery publishes a complete HA device with entities for all
  controllable aspects of the satellite — mute, LED effects, LED colors,
  alarm duration, and sound selection
- LED effects and colors are configurable per voice state (Idle, Listening,
  Thinking, Responding, Error) directly from the Home Assistant UI
- All MQTT-controlled settings are retained by the broker and re-applied
  on reconnect or restart without user intervention
- Upstream provides no MQTT integration; this is a foundational addition
  to this fork

**EventBus Architecture**
- Introduced a synchronous publish/subscribe EventBus to decouple
  hardware components (LED controller, button controller, MQTT controller)
  from the core voice pipeline
- All controllers subscribe to named events (`voice_idle`, `voice_listen`,
  `mic_muted`, etc.) rather than being called directly, enabling independent
  addition or removal of components

**Sendspin Multiroom Audio Client** (optional — install with `script/setup --sendspin`)
- Integrates LVA with Music Assistant via the Sendspin protocol over WebSocket
- Auto-discovers the Sendspin server via mDNS (`_sendspin-server._tcp.local.`) or accepts a static host in `config.json`
- Supports PCM, FLAC, and Opus audio codecs; codec preference and availability configurable
- Real-time volume and mute control via mpv IPC
- Automatically ducks Sendspin playback volume during voice listen, thinking, and responding states; unducks on idle or error
- Persists Music Assistant player volume across sessions via `preferences.json`
- Publishes connection, playback, metadata, and audio state events to the LVA EventBus (`sendspin_connection_state`, `sendspin_playback_state`, `sendspin_metadata`, `sendspin_audio_state`)
- Spec-compliant handshake: `client/hello` → `server/hello` → heartbeat/time-sync loop
- Graceful disconnect with `client/goodbye` per protocol spec
- Automatic reconnect with exponential backoff

**Event Sounds & Thinking Sound**
- New `app.thinking_sound` config option — plays a sound during the THINKING state (after speech-to-text, before TTS response)
- New `app.thinking_sound_loop` config option — when `true`, the thinking sound loops until the state changes; when `false`, it plays once
- New `app.event_sounds_enabled` master toggle — when `false`, suppresses wakeup and thinking sounds; timer alarm is always played regardless as it is a functional alert
- Multiple thinking sound files bundled: `nothing.flac`, `processing.flac`, `thinking_modem.flac`, `thinking_music.flac`, `thinking_music_2.flac`, `thinking_music_3.flac`

**MQTT Sound Selection**
- Three new MQTT select entities: **Sound Wakeup**, **Sound Thinking**, **Sound Timer**
- New MQTT switch entity: **Sound Thinking Loop**
- Sound files scanned from `sounds/wakeup/`, `sounds/thinking/`, and `sounds/timer/` subdirectories at startup; subdirectories are auto-created if missing
- Drop `.flac`, `.wav`, or `.mp3` files into any subdirectory and restart LVA to make them available in Home Assistant
- Wakeup and Thinking selects include a "None" option to disable the sound entirely; *not applied to Timer*
- Selection persisted to `preferences.json` and applied at runtime without restart
- Precedence: MQTT selection > `config.json` > `config.py` defaults
- Sound files reorganized into category subdirectories (`sounds/wakeup/`, `sounds/thinking/`, `sounds/timer/`)

**Stable Device Identity**
- LVA MAC address is now persisted to `preferences.json` on first boot
- Subsequent boots use the persisted MAC regardless of hardware NIC changes, VM re-provisioning, or OS MAC randomization
- Prevents device re-registration in Home Assistant after network or OS changes
- To reset device identity, remove `mac_address` from `preferences.json`

**ReSpeaker XVF3800 USB 4-Mic Array**
- Hardware mute button with bidirectional sync between the physical device, LVA state, and Home Assistant
- Advanced LED effects driven over USB
- New `button.mode: "xvf3800"` config option; GPIO-specific fields are ignored in this mode
- Optional `poll_interval_seconds` to tune USB polling frequency

**GPIO Button Controller**
- Short press: wake the assistant or stop active TTS/timer alarm playback
- Long press: toggle microphone mute
- Configurable `pin` and `long_press_seconds` in `config.json`

**MQTT Alarm Duration Control**
- New **Alarm Duration** number entity in Home Assistant
- Set timer alarm auto-stop duration in seconds; `0` = infinite (stop only via Stop wake word or wakeup word)
- Persisted to `preferences.json`

**Volume Sync at Startup**
- New `audio.volume_sync` config option (default: `false`) — when enabled, LVA sets the OS output sink volume to match the persisted `volume_level` from `preferences.json` on startup
- New `audio.max_volume_percent` config option — allows mapping LVA's 100% to greater than 100% on the OS sink (useful for devices that need boosted output)
- Sync attempts via `wpctl` (PipeWire) → `pactl` (PulseAudio) → `amixer` (ALSA) in order

**Per-Model OpenWakeWord Threshold Tuning**
- Per-model sensitivity threshold configurable in individual model `.json` files
- Global override available via CLI flag `--wake-word-threshold`
- Overrides the global `wake_word.openwakeword_threshold` in `config.json`

**Additional OpenWakeWord Models**
- Added bundled models: `computer_v2`, `hey_Marvin`, `hey_nabu_v2`, `jarvis_v2`, `ok_jarvis`
- Updated existing model JSON configs to prevent OWW models from masking MWW models of the same name

**update_lva Script**
- New `script/update_lva` automates pulling the latest LVA release while preserving `config.json` and `preferences.json`
- Accepts setup flags (e.g. `--sendspin`, `--tray`) to reinstall optional components

**System Tray Client** (optional — install with `script/setup --tray`)
- Desktop system tray icon that mirrors LVA state and LED colors
- Mute toggle, and start/stop/restart of the LVA systemd user service

**ReSpeaker 2Mic v2 Hardware Support**
- Installation documentation and configuration examples for the ReSpeaker 2-Mic HAT v2
- PipeWire and PulseAudio audio backend documentation updated for this device

---

### Fixed

- **Volume not restored after reboot** — `initial_volume` was passed to `MpvMediaPlayer` but never applied; mpv always started at 100% regardless of the persisted value. Fixed `set_volume()` to apply the persisted volume on construction. When `audio.volume_sync` is enabled the OS sink handles volume restoration, so mpv correctly stays at 100% to avoid double-attenuation.
- **MQTT reconnect reliability** — Added `reconnect_delay_set(min=1, max=60)` to prevent paho from giving up during extended network outages. Bootstrap state sync now resets correctly on every reconnect to ensure retained messages are re-applied. Stale bootstrap-end timers are cancelled on rapid disconnect/reconnect cycles, and on clean shutdown.
- **Timer alarm not stopping; duration setting not honored** — `_timer_auto_stop_handle` was referenced in `handle_timer_event` and `_clear_timer_auto_stop` before being initialized, causing an `AttributeError` that silently prevented auto-stop from being scheduled and blocked the Stop/wakeup word from cancelling the alarm.
- **TTS 20-second timeout on HA proxy URLs** — Disabled the mpv `ytdl` hook (`ytdl=False`) which was intercepting Home Assistant TTS proxy URLs and timing out after 20 seconds attempting a youtube-dl lookup.
- **Sendspin causing `ModuleNotFoundError` when not installed** — `SendspinClient` was unconditionally imported at module load, causing a crash when the `websockets` package was absent. Changed to a conditional `try/except` import with a `None` fallback and a runtime warning.
- **MQTT and ESPHome devices not linked in Home Assistant** — MQTT Discovery device info now uses MAC address as the identifier and includes it in the `connections` attribute, allowing HA to automatically associate the MQTT device with the ESPHome integration device.
- **Preferences loading crash on unknown keys** — `Preferences(**preferences_dict)` would raise `TypeError` if `preferences.json` contained unrecognized keys from a newer or older version. Loading now filters to known fields only, matching the defensive pattern already used for `config.json` sections.

---

### Changed

- Centralized all configuration into `config.json` replacing 20+ CLI
  arguments; application is now launched with a single `--config` flag.
  `config.py` provides typed dataclasses with defaults and validation for
  all sections
- `__main__.py` refactored into discrete helper functions
  (`_init_basics`, `_load_preferences`, `_init_media_players`,
  `_create_server_state`, `_init_controllers`) to improve readability
  and testability as the codebase grew
- Replaced `sounddevice` audio library with `soundcard` to align with
  upstream and improve PipeWire/PulseAudio compatibility
- Local wake word model code removed; replaced with `pymicro-wakeword`
  and `pyopen-wakeword` pip packages

- MQTT entity names prefixed with **LED**, **Sound**, and **Alarm** for logical visual grouping in the Home Assistant device configuration page
- `pymicro_wakeword` log level raised to `INFO` when running in debug mode to suppress high-frequency probability log spam introduced in `pymicro-wakeword==2.2.0`

---

