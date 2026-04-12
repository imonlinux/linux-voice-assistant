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



## upstream_refactor branch

This branch incorporates the upstream architectural changes from the OHF-Voice
ESPHome entity pattern, migrating all voice and audio controls from MQTT Discovery
to native ESPHome entities. After this merge, MQTT is LED hardware controls only.

---

### Added

**ESPHome Entity Migration (Phases 1–5)**

This is the core architectural change in this branch. Seven new entity classes
were added to `entity.py`, each following the upstream ESPHome entity pattern
(callback-based state sync, `list_entities` registration, `handle_command` dispatch).

| Key | Entity Class | ESPHome Type | Persisted In |
|-----|-------------|--------------|--------------|
| 0 | `MediaPlayerEntity` | media_player | `preferences.json` (volume) |
| 1 | `MuteSwitchEntity` | switch | `ServerState` (runtime only) |
| 2 | `ThinkingSoundSwitchEntity` | switch | `preferences.json` |
| 3 | `EventSoundsSwitchEntity` | switch | `preferences.json` |
| 4 | `WakeWordSensitivityEntity` | select | `preferences.json` |
| 5 | `SoundSelectEntity` (wakeup) | select | `preferences.json` |
| 6 | `SoundSelectEntity` (thinking) | select | `preferences.json` |
| 7 | `SoundSelectEntity` (timer) | select | `preferences.json` |
| 8 | `AlarmDurationNumberEntity` | number | `preferences.json` |

Entity keys are stable across reconnections. Each entity appears on the HA device
page under its natural category without requiring MQTT.

- **`MuteSwitchEntity`** (key=1) — ports upstream's mute switch directly; syncs
  bidirectionally with hardware mute button and XVF3800 USB button; replaces the
  MQTT mute switch.
- **`ThinkingSoundSwitchEntity`** (key=2) — toggles thinking-sound loop playback;
  replaces the MQTT thinking sound loop switch. Analogous to upstream's
  `ThinkingSoundEntity`.
- **`EventSoundsSwitchEntity`** (key=3) — runtime toggle for the event sounds master
  switch (`event_sounds_enabled`); was previously `config.json`-only with no HA UI.
- **`SoundSelectEntity`** (keys=5–7, generic reusable class) — options scanned from
  `sounds/wakeup/`, `sounds/thinking/`, and `sounds/timer/` subdirectories at
  startup; replaces the three MQTT sound select entities.
- **`AlarmDurationNumberEntity`** (key=8) — number entity (min=0, max=3600, step=5)
  for runtime alarm auto-stop duration; replaces the MQTT alarm duration number
  entity. Upstream tracks this as `timer_max_ring_seconds` on `ServerState` (PR
  #261) with a static CLI arg only; this fork exposes it as a live HA number entity.
- **`WakeWordSensitivityEntity`** (key=4) — select entity with coarse sensitivity
  presets (Low / Medium / High / Maximum); based on upstream PR #207. Precedence:
  ESPHome preset > per-model JSON threshold > global `config.json` threshold.

**Entity lifecycle helper — `_setup_entity()` / `_setup_entity_by_id()`**
- `satellite.py` now uses a helper that checks for an existing entity of the same
  type (or type + instance ID for multi-instance entities) on reconnect, reusing it
  instead of registering a duplicate. This is upstream's entity lifecycle pattern.

**ESPHome command message routing**
- `SwitchCommandRequest`, `SelectCommandRequest`, and `NumberCommandRequest` are now
  imported and dispatched in `satellite.py`'s message handling loop, enabling HA to
  control all new entity types over the ESPHome API.

**`MpvMediaPlayer.play()` volume override**
- New `volume_override: Optional[int]` parameter temporarily sets mpv volume for a
  single playback (range 0–200), restoring the previous level when done. Used
  internally to correct wakeup sound amplitude without permanently changing the user's
  volume setting.

**Ruff linting configuration**
- `pyproject.toml` now includes `[tool.ruff]` targeting Python 3.9, selecting rules
  E9, F4, and F8 (syntax errors, import errors, undefined names). F841 (unused
  variable) is suppressed for EventBus handler assignments that register side-effects.
  A pre-commit hook runs `ruff check` on staged Python files before each commit.
  Run manually with `ruff check .`

---

### Fixed

- **Wakeup sound plays too quietly** (#76) — PipeWire/PulseAudio typically initializes
  the sink at ~70% regardless of the persisted volume level; the wakeup sound was
  therefore consistently under-volume on every fresh start. `satellite.py` now calls
  `tts_player.play(wakeup_sound, volume_override=100)` so the wakeup chime always
  plays at full mpv volume, independent of the OS sink initialization state.

- **Startup crash on unknown preference keys** (#77) — `Preferences(**preferences_dict)`
  would raise `TypeError` if `preferences.json` contained keys added by a newer
  version of LVA (e.g. after a rollback). Loading now filters to known fields only,
  matching the defensive pattern already used for `config.json` sections.

---

### Changed

**MQTT controller slimmed to LED-only**
- All voice and audio control entities removed from `mqtt_controller.py`: mute switch,
  thinking sound loop switch, event sounds switch, three sound select entities, and
  alarm duration number entity. These are now ESPHome entities (see above).
- The MQTT controller now manages only LED hardware: LED count, LED effects (×5), and
  LED colors (×5).
- **Architectural boundary:** ESPHome entities handle all voice and audio behavior
  (mute, sounds, sensitivity, alarm duration). MQTT handles LED hardware exclusively.
  Users without LEDs do not need an MQTT broker at all.

**`self.state.satellite` assignment deferred**
- Moved to the end of `VoiceSatelliteProtocol.__init__` after all entities are set up,
  matching upstream's pattern and preventing callbacks from firing before entity
  initialization is complete.

**Unused imports removed**
- `import numpy as np` and `MicroWakeWordFeatures` from `__main__.py`, `from pathlib
  import Path` from `satellite.py`, and `import math` from `sendspin/player.py`
  were removed; all flagged by ruff F401. Availability probe for the optional
  `opuslib` package in `sendspin/player.py` replaced with `importlib.util.find_spec()`
  (standard library pattern) to avoid the unused-import flag entirely.
