## Unreleased

### Added

- Sendspin: mDNS server auto-discovery restored with pre-2.0 config semantics — `sendspin.connection.mdns` (default `true`) browses `_sendspin-server._tcp.local.` when `sendspin.connection.server_host` is unset; setting `server_host` bypasses discovery. Existing `config.json` files that carried `mdns` need no migration (the migration script no longer strips it). Discovery re-runs on every reconnect, so a moved MA server is picked up without a restart.

### Changed

- `update_lva` now remembers the last-used setup flags (`--sendspin`, `--tray`, `--dev`) in `.lva-setup-flags` and reuses them when an update runs without flags. Previously a bare `update_lva` rebuilt the venv without the extras — silently dropping the Sendspin client (and piper-tts with it) after every update. Explicit flags on any update replace the remembered set.
- Documentation: the README breaking-change warning is gone (mDNS discovery removed the need for it); the Sendspin docs now lead with the recommended setup — `enabled: true` plus `pairing.voice_engine: "piper"` for the spoken PIN announcement.
- `sendspin.connection`: the stale `mode` key is no longer accepted as a setting (server-initiated connections do not exist in the aiosendspin client); unknown keys already log and are ignored.

### Fixed

- `update_lva` no longer crashes with `Syntax error: "(" unexpected` when invoked via `sh` (dash on Debian/RPi OS cannot parse the script's bash arrays). The script now re-execs itself under bash when `BASH_VERSION` is unset, so `sh script/update_lva`, `./script/update_lva`, and `bash script/update_lva` all work.
- ReSpeaker 2-Mic installer: the bring-up-without-reboot path applied the v1 overlay regardless of the detected HAT revision, so a v2 board always ended with "please reboot" even when a live apply would have registered the card. The live apply now uses the detected revision's overlay. Devices that already rebooted are unaffected (the config.txt entry was always correct).

<a id="v2.0.0"></a>
# [v2.0.0](https://github.com/imonlinux/linux-voice-assistant/releases/tag/v2.0.0) - 2026-09-21

## Breaking change (Sendspin)

The Sendspin client was rebuilt on **aiosendspin 9.x**. The deprecated pre-encryption wire protocol and mDNS auto-discovery are gone, and the Music Assistant server address is now **required** in `config.json`:

```json
"sendspin": { "connection": { "server_host": "<MA-SERVER-IP>" } }
```

Existing `config.json` files must add `sendspin.connection.server_host` before the client can connect. Run `script/migrate_config.py` after updating: it warns when the key is missing (it cannot guess your MA IP) and strips the other obsolete `sendspin.connection` keys. Devices without Sendspin enabled are unaffected. The rebuilt client adds Noise-encrypted pairing with a PIN spoken through the speaker (Piper, espeak-ng fallback), persistent player identity, and a sounddevice output stage with server-synchronized buffering. Requires Python >= 3.12 and `libportaudio2`.

## Re-founded on the upstream core

This release lands the re-foundation: upstream modules (`satellite.py`, `entity.py`, `models.py`, `player/`, `wake_word.py`, peripheral API) are used as-is and the fork's differentiating features live as add-on modules. Future upstream releases merge cleanly again. From upstream this brings the full ESPHome device-page entity set (mic auto gain, noise suppression, mic volume, per-slot wake word and stop word sensitivities), dual music/TTS players with ducking and announcements, output-only mode, external wake word downloads, MWW/OWW model switching from the UI, and the WebSocket peripheral API (port 6055).

`main` is now the re-founded stack. Devices still tracking the old `upstream-core` branch keep working: their updater self-migrates to `main` on the first update after this release.

## Kernel-independent ReSpeaker 2-Mic HAT audio (v1 and v2)

Audio runs on mainline kernel drivers (`snd-soc-wm8960` for v1, `snd-soc-tlv320aic3104` for v2) via device-tree overlays compiled at install time:

- No DKMS, no kernel headers, no per-kernel builds, any kernel >= 5.4; kernel upgrades can no longer break audio
- One smart installer auto-detects the HAT revision by I2C address (0x1a = v1, 0x18 = v2) and removes stale installs of the other revision
- Same ALSA card ID (`seeed2micvoicec`) as before, so existing LVA configs keep working; legacy DKMS installs upgrade in place
- v1 gets proper MCLK wiring on the codec node (fixes "No MCLK configured" PCM open failures), which also enables the WM8960 PLL for the 44.1 kHz family

## Self-updating fleet

`script/update_lva` rewritten: in-place `git fetch` + checkout instead of move-aside + fresh clone. Untracked per-device files (preferences, Sendspin credentials, piper voices, per-model threshold files, external wake words, `config.json`) are never touched. The script re-executes the freshly fetched copy, so update logic always runs at the version being deployed. Adds `--branch`, `--force`, and `--rollback`. Now defaults to `main`.

## Fixed

- **Tray client stale state** after MQTT (re)connects: the daemon now publishes a consolidated retained `lva/<device_id>/state` topic on every voice transition, and the tray derives its displayed state from it. State changes while muted are also published now.
- **HA mute switch going stale** on non-HA mute changes (tray, GPIO, XVF3800 hardware buttons): mute state is now published to all connected API clients.

## Upgrade notes

1. Run `script/migrate_config.py` (or `script/update_lva` with your usual flags) after updating.
2. If you use Sendspin, add `sendspin.connection.server_host` to `config.json`.
3. `config.json` is no longer tracked by git; per-device files survive updates.

Full details in [CHANGELOG.md](https://github.com/imonlinux/linux-voice-assistant/blob/main/CHANGELOG.md).

**Testing:** 625 passed / 1 skipped with all install extras (Python 3.12); verified on Raspberry Pi Zero 2 W (2-Mic HAT v1 + v2), Orange Pi Zero 2 W (XVF3800), and desktop tray clients.

[Changes][v2.0.0]


<a id="v1.1.0"></a>
# [v1.1.0](https://github.com/imonlinux/linux-voice-assistant/releases/tag/v1.1.0) - 2026-04-17

## Version 1.1.0 — ESPHome Entity Migration (**Edit - fix update_lva script run details)

This release implements a major architectural realignment migrating voice and audio controls from MQTT to native ESPHome entities. After this upgrade, MQTT is only required for LED hardware controls.

### Breaking Changes

**Home Assistant device cleanup required:**

- Old MQTT entities will appear as "unavailable" after upgrade
- Manual cleanup of LVA and MQTT devices in Home Assistant is required (see Migration Instructions below)
- Service restart is required after update script completes

### Migration Instructions

1. **Stop LVA service:**
  ```
  systemctl --user stop linux-voice-assistant.service
  ```

2. **Delete old devices in Home Assistant:**
  - Go to Settings → Devices & Services
  - Go to MQTT and delete LVA device (this removes all old MQTT entities)
  - Go to ESPHome and delete LVA device if it exists
  - Wait 30 seconds for HA to fully remove devices

3. **Run update script with your setup flags:**
  ```
sh ~/linux-voice-assistant/script/update_lva.sh
  ```
  - Pass --sendspin if you use Sendspin multiroom audio
  - Pass --tray if you use the system tray client
  - Your config.json and preferences.json are preserved automatically

4. **Register LVA in Home Assistant:**
  - Go to Settings → Devices & Services → Add Integration → ESPHome
  - Add LVA that was auto discovered
  - Your satellite should reappear with new native entities
  - Voice/audio controls now appear under Media Player and Switch categories
  - If you included MQTT in your config.json it will be automatically added and visible in the LVA Device but now only controls LED entities

### What's New

**Native ESPHome entities replace MQTT:**
| Entity | ESPHome Type | Replaces MQTT |
|--------|--------------|----------------|
| Mute Microphone | switch | switch.lva_mute |
| Sound Thinking Loop | switch | switch.lva_thinking_sound_loop |
| Event Sounds | switch | switch.lva_event_sounds_enabled |
| Wake Word Sensitivity | select | select.lva_wake_word_sensitivity |
| Sound Wakeup | select | select.lva_sound_wakeup |
| Sound Thinking | select | select.lva_sound_thinking |
| Sound Timer | select | select.lva_sound_timer |
| Alarm Duration | number | number.lva_alarm_duration |

**Architectural improvements:**

- Entity lifecycle now matches upstream pattern with reconnection-safe reuse
- Wake word sensitivity supports three-preset system (Low/Medium/High/Maximum)
- Per-model OWW thresholds from .json files take precedence over global preset
- ESPHome command routing upstreamed from PRs [#271](https://github.com/imonlinux/linux-voice-assistant/issues/271) and [#273](https://github.com/imonlinux/linux-voice-assistant/issues/273)

**MQTT scope reduced:**

- LED hardware controls remain on MQTT (LED count, effects, colors)
- System tray client mute state mirroring continues via MQTT
- Voice and audio behavior no longer requires MQTT broker

### Fixed

- **Wakeup sound volume** ([#76](https://github.com/imonlinux/linux-voice-assistant/issues/76)) — Plays at full volume regardless of OS sink initialization
- **Unknown preference crash** ([#77](https://github.com/imonlinux/linux-voice-assistant/issues/77)) — Gracefully ignores unrecognized keys in preferences.json

### Changed

- **MQTT controller** — Removed all voice/audio entities; manages LED hardware only
- **Satellite protocol** — Entity lifecycle matches upstream pattern
- **Code quality** — Added ruff linting with pre-commit hooks

### Codex-Reviewed Bug Fixes

**Fix: Remove duplicate wakeup sound playback in delayed-listen mode**
- In delayed-listen mode (`listen_during_wake_sound=false`), users hear the wakeup sound twice — first when starting the conversation, then again when the sound finishes. This causes audio to leak into STT processing.
- **Fix: Reapply sensitivity preset after external wake word load**
- External wake words announced by Home Assistant are loaded via async download, but the current sensitivity preset was not being applied to these newly loaded models. Now reapply the preset after each external word is loaded.


[Changes][v1.1.0]


[v2.0.0]: https://github.com/imonlinux/linux-voice-assistant/compare/v1.1.0...v2.0.0
[v1.1.0]: https://github.com/imonlinux/linux-voice-assistant/tree/v1.1.0

<!-- Generated by https://github.com/rhysd/changelog-from-release v3.9.1 -->
