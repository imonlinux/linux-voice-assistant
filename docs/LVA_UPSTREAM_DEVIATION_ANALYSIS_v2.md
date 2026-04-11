# LVA Fork vs Upstream OHF-Voice/linux-voice-assistant — Deviation Analysis v2

**Date:** April 2, 2026  
**Revision:** v2 — expanded entity migration scope based on PR #261 (timer max ring) and full MQTT entity audit  
**Purpose:** Map all architectural and functional deviations between `imonlinux/linux-voice-assistant` (LVA fork) and `OHF-Voice/linux-voice-assistant` (upstream) to inform a potential realignment effort.  
**Scope:** Core Python module (`linux_voice_assistant/`). Excludes Docker, PiCompose, `.env`, and CI/CD packaging (not relevant to this fork).

---

## Executive Summary

The LVA fork has diverged from upstream in three major categories:

1. **Configuration model** — Fork uses `config.json` + `config.py` dataclasses; upstream uses CLI arguments + environment variables
2. **Control plane for HA entities** — Fork uses MQTT Discovery for most controls; upstream uses ESPHome native entities
3. **Feature additions** — Fork has substantial features upstream lacks (MQTT controller, LED system, XVF3800 hardware, Sendspin client, tray client, sound selection, volume sync)

The deviations are not random — they reflect a deliberate design choice to centralize configuration and leverage MQTT for HA integration. However, upstream is now building the same types of controls (mute switch, thinking sound toggle, wake word sensitivity, timer max ring) as native ESPHome entities or `ServerState` fields. This creates a growing divergence in the entity/control architecture that will make cherry-picking upstream features increasingly painful.

**v2 change:** A full audit of all fork MQTT entities identified 9 controls that should migrate from MQTT to ESPHome entities, with only LED-specific controls remaining as MQTT. This establishes a clean architectural boundary: **ESPHome owns voice/audio behavior; MQTT owns LED hardware.**

---

## 1. Configuration Architecture

### Upstream Approach
- **All configuration via CLI arguments** (`--name`, `--audio-input-device`, `--wake-word-dir`, `--timer-max-ring-seconds`, etc.)
- Environment variables map 1:1 with CLI args (for Docker/systemd)
- `preferences.json` for runtime-persisted state (volume, wake words, thinking sound)
- No `config.json` or `config.py` exists

### Fork Approach
- **Centralized `config.json`** with typed dataclasses in `config.py`
- Sections: `app`, `audio`, `wake_word`, `esphome`, `led`, `mqtt`, `button`, `sendspin`
- Minimal CLI args remain (`--config`, `--debug`)
- `preferences.json` for runtime-persisted state (same concept as upstream)

### Assessment
This is probably the **lowest-risk divergence to maintain**. The fork's approach is architecturally cleaner for a multi-subsystem application. Upstream's CLI-only model works for their Docker/systemd-env-var workflow but becomes unwieldy as features grow. **Recommendation: Keep the fork's config.json approach.** When pulling upstream features, translate new CLI args into `config.json` sections.

---

## 2. Entity System Architecture (BIGGEST DIVERGENCE)

This is the core architectural split and the most important to address.

### Upstream Approach
Upstream is building ESPHome native entities that appear on the HA device page without MQTT:

| Entity | Type | Protobuf Messages | Status |
|--------|------|-------------------|--------|
| Media Player | `MediaPlayerEntity` | `MediaPlayerCommandRequest`, `ListEntitiesMediaPlayerResponse` | In main |
| Mute Switch | `MuteSwitchEntity` | `SwitchCommandRequest`, `ListEntitiesSwitchResponse` | In main |
| Thinking Sound | `ThinkingSoundEntity` | `SwitchCommandRequest`, `ListEntitiesSwitchResponse` | In main |
| Wake Word Sensitivity | `WakeWordSensitivityEntity` | `SelectCommandRequest`, `ListEntitiesSelectResponse` | PR #207 (open) |
| Timer Max Ring Seconds | `ServerState` field only | N/A — CLI arg `--timer-max-ring-seconds`, no HA UI | PR #261 (merged) |

Upstream's `entity.py` has grown to include `ESPHomeEntity` (base), `MediaPlayerEntity`, `MuteSwitchEntity`, `ThinkingSoundEntity`, and (in PR #207) `WakeWordSensitivityEntity`.

Upstream's `satellite.py` routes `SwitchCommandRequest`, `SelectCommandRequest`, `MediaPlayerCommandRequest`, and `SubscribeHomeAssistantStatesRequest` to all entities in `state.entities`.

Upstream manages entity lifecycle across reconnections — checking for existing entities by type, reusing them, and updating their callbacks.

### Fork Approach
The fork has **only `MediaPlayerEntity`** as an ESPHome entity. The `VoiceSatelliteProtocol.__init__` actively prunes the entity list:

```python
# If more entities somehow accumulated, prune them to avoid confusing HA
if len(self.state.entities) > 1:
    _LOGGER.warning(
        "Pruning %d extra ESPHome entities; keeping only the first.",
        len(self.state.entities) - 1,
    )
    del self.state.entities[1:]
```

The fork's `handle_message` only routes `MediaPlayerCommandRequest` and `SubscribeHomeAssistantStatesRequest` — it does NOT route `SwitchCommandRequest` or `SelectCommandRequest`.

All other controls go through `mqtt_controller.py`:
- Mute switch → MQTT switch entity
- LED effects/colors → MQTT select + light entities per voice state
- Sound selection → MQTT select entities
- Thinking sound loop → MQTT switch entity
- Number of LEDs → MQTT number entity
- Alarm duration → MQTT number entity

### Assessment
This is where the fork and upstream are heading in **opposite directions**. Upstream is building everything as ESPHome entities. The fork is building everything as MQTT entities. Both work, but you can't easily have both for the same control (e.g., two mute switches — one ESPHome, one MQTT — would confuse users and create state sync issues).

**Recommendation:** Migrate voice/audio controls to ESPHome entities. Keep MQTT exclusively for LED hardware controls. See Section 11 for the complete entity migration plan.

---

## 3. ESPHome Entity Base Class

### Upstream
```python
class ESPHomeEntity:
    def __init__(self, server: APIServer) -> None:
        self.server = server
```
Base class takes only `server`. No `state` parameter.

### Fork
```python
class ESPHomeEntity:
    def __init__(self, server: APIServer, state: "ServerState") -> None:
        self.server = server
        self.state = state
```
Base class takes both `server` and `state`.

### Assessment
Upstream's entities access state through callbacks (lambdas passed in constructor) rather than holding a direct reference to `ServerState`. This is a cleaner separation of concerns — entities don't need to know about the full state object. The fork's approach is simpler but tighter-coupled. **Recommendation:** Align with upstream's callback pattern when adding new entities. The existing `MediaPlayerEntity` can stay as-is for now since it genuinely needs broad state access for playback.

---

## 4. Audio Processing / Wake Word Detection

### Upstream (current main + PR #207)
- Audio processing lives in `__main__.py` `process_audio()` function
- OWW threshold was hardcoded at `0.5`, now being changed to use `state.oww_probability_cutoff` (from PR #207)
- MWW uses `process_streaming()` which returns bool (threshold is internal to the model)
- PR #207 adds sensitivity presets that override MWW's internal `probability_cutoff`

### Fork
- Audio processing also in `__main__.py` (likely similar structure since forked from v1.0.0)
- OWW threshold is configurable three ways:
  - Global: `config.json` → `wake_word.openwakeword_threshold` (default 0.5)
  - Per-model: model's `.json` file → `"threshold": 0.62`
  - CLI: `--wake-word-threshold`
- Per-model overrides are resolved at model load time

### Assessment
The fork's per-model threshold system is **more granular** than upstream's preset approach. However, they're solving different problems:
- Fork: "I need this specific model at 0.62 and that one at 0.45"
- Upstream: "Give users a simple UI knob in HA"

**Recommendation:** Both can coexist. Add the ESPHome sensitivity entity (preset-based) as a global multiplier/override, while keeping per-model thresholds as the fine-grained config. Precedence: ESPHome entity selection > per-model JSON > global config.json > default.

---

## 5. Satellite Protocol / State Machine

### Upstream
- `VoiceSatelliteProtocol.__init__` manages multiple entities, checks for existing entities by type on reconnection
- Routes `SwitchCommandRequest` and `SelectCommandRequest` alongside media player commands
- `_set_thinking_sound_enabled()` method on the protocol for the entity callback
- Moves `self.state.satellite = self` to the **end** of `__init__` (prevents race with audio thread)

### Fork
- Manages only `MediaPlayerEntity`, prunes others
- Routes only `MediaPlayerCommandRequest`
- No thinking sound entity integration in satellite
- Sets `self.state.satellite = self` **early** in `__init__`

### Assessment
The upstream pattern of deferred `self.state.satellite = self` assignment is a good defensive practice. The entity management pattern (check-existing-by-type, reuse-or-create) is necessary for multi-entity support. **Recommendation:** Adopt upstream's entity management pattern and deferred satellite assignment.

---

## 6. Models / Data Classes

### Upstream `ServerState` (from PR #207 + PR #261 merged)
```python
@dataclass
class ServerState:
    satellite: "Optional[VoiceSatelliteProtocol]" = None
    mute_switch_entity: "Optional[MuteSwitchEntity]" = None
    thinking_sound_entity: "Optional[ThinkingSoundEntity]" = None
    sensitivity_entity: "Optional[WakeWordSensitivityEntity]" = None
    wake_words_changed: bool = False
    refractory_seconds: float = 2.0
    thinking_sound_enabled: bool = False
    output_only: bool = False
    muted: bool = False
    connected: bool = False
    volume: float = 1.0
    wake_word_sensitivity: str = "Slightly sensitive"
    oww_probability_cutoff: float = 0.7
    timer_max_ring_seconds: float = 900.0
```

### Fork `ServerState`
Has many more fields reflecting fork features:
- `mic_muted` (not `muted`)
- `shutdown: bool`
- `event_sounds_enabled: bool`
- `thinking_sound_loop: bool`
- `mic_muted_event: threading.Event` (efficient audio thread pausing)
- No entity reference fields (no `mute_switch_entity`, etc.)
- No `wake_word_sensitivity` or `oww_probability_cutoff`

### Fork `Preferences`
Has additional fields:
- `selected_wakeup_sound`, `selected_thinking_sound`, `selected_timer_sound` (MQTT sound selections)
- `selected_thinking_sound_loop` (MQTT override)
- `volume_level` (instead of `volume`)
- `sendspin_volume`
- `alarm_duration_seconds`

### Assessment
The fork's `ServerState` is richer because it manages more subsystems. The naming differences (`muted` vs `mic_muted`, `volume` vs separate tracking) will cause merge conflicts but aren't architectural issues. **Recommendation:** When adding upstream entity fields to `ServerState`, use upstream's naming where possible. The fork-specific fields stay.

---

## 7. Fork-Only Features (Upstream Doesn't Have These)

These are features the fork has that upstream does not. They should be maintained as fork-specific additions:

| Feature | Files | Notes |
|---------|-------|-------|
| `config.json` + `config.py` | `config.py`, `config.json`, `config.json.example` | Centralized config system |
| MQTT Controller | `mqtt_controller.py` | Full MQTT Discovery device with switches, selects, lights, numbers |
| LED Controller | `led_controller.py` | Backend-agnostic LED effects with per-state mapping |
| XVF3800 USB LED Backend | `xvf3800_led_backend.py` | Direct USB control of ReSpeaker XVF3800 LEDs |
| XVF3800 Button Controller | `xvf3800_button_controller.py` | Hardware mute button + LED sync |
| GPIO Button Controller | `button_controller.py` | Short press wake/stop, long press mute |
| Audio Volume Sync | `audio_volume.py` | OS sink volume alignment on startup |
| Sound Selection | In `mqtt_controller.py` + `__main__.py` | Scan directories, select entities (migrating to ESPHome) |
| Sendspin Client | `sendspin/` subpackage | WebSocket-based multiroom audio client |
| Tray Client | `tray_client/` subpackage | Desktop system tray with state mirror |
| EventBus | `event_bus.py` | Pub/sub for cross-subsystem communication |
| Per-model OWW thresholds | In `__main__.py` model loading | Granular per-model sensitivity |
| Preferences field filtering | In `__main__.py` | Defensive loading of unknown keys |
| Runtime alarm duration control | MQTT number entity | Upstream's PR #261 is static CLI-only; fork allows runtime adjustment |
| Event sounds master toggle | `config.json` only | Not runtime-adjustable yet; candidate for ESPHome switch |

---

## 8. Upstream Features the Fork Is Missing

| Feature | Notes |
|---------|-------|
| `MuteSwitchEntity` (ESPHome) | Fork uses MQTT switch instead |
| `ThinkingSoundEntity` (ESPHome) | Fork uses MQTT switch + sound select instead |
| `WakeWordSensitivityEntity` (ESPHome) | PR #207, not yet merged upstream |
| `timer_max_ring_seconds` on `ServerState` | PR #261 (merged). Fork has equivalent via MQTT + preferences, but not on `ServerState` directly |
| `--output-only` mode | May exist in fork but not confirmed |
| Docker/env-var configuration | Not needed for fork's use case |
| `--processing-sound` / `--mute-sound` / `--unmute-sound` CLI args | Fork handles sounds differently via config.json + MQTT |

---

## 9. Complete MQTT Entity Audit — ESPHome Migration Candidates

This section inventories every fork MQTT entity and determines whether it should migrate to ESPHome.

### Migrate to ESPHome (voice/audio behavior controls)

| # | Current MQTT Entity | Type | ESPHome Protobuf | Upstream Precedent | Notes |
|---|-------------------|------|------------------|--------------------|-------|
| 1 | Mute Microphone | switch | `SwitchCommandRequest` / `ListEntitiesSwitchResponse` | `MuteSwitchEntity` in upstream main | Direct port from upstream |
| 2 | Sound Thinking Loop | switch | `SwitchCommandRequest` / `ListEntitiesSwitchResponse` | `ThinkingSoundEntity` in upstream main | Direct port from upstream |
| 3 | Sound Wakeup | select | `SelectCommandRequest` / `ListEntitiesSelectResponse` | Same pattern as `WakeWordSensitivityEntity` (PR #207) | Fork-specific; options populated from `sounds/wakeup/` scan |
| 4 | Sound Thinking | select | `SelectCommandRequest` / `ListEntitiesSelectResponse` | Same pattern | Fork-specific; options from `sounds/thinking/` scan |
| 5 | Sound Timer | select | `SelectCommandRequest` / `ListEntitiesSelectResponse` | Same pattern | Fork-specific; options from `sounds/timer/` scan |
| 6 | Alarm Duration | number | `NumberCommandRequest` / `ListEntitiesNumberResponse` | Upstream has `timer_max_ring_seconds` on `ServerState` (PR #261) but no HA UI | Fork-specific ESPHome number entity; upstream only has static CLI arg |
| 7 | *(new)* Wake Word Sensitivity | select | `SelectCommandRequest` / `ListEntitiesSelectResponse` | `WakeWordSensitivityEntity` (PR #207, open) | Port from upstream PR |
| 8 | *(new)* Event Sounds Enabled | switch | `SwitchCommandRequest` / `ListEntitiesSwitchResponse` | None — fork-specific | Currently `config.json` only; making it an ESPHome switch enables runtime toggle from HA |

### Keep as MQTT (LED hardware controls)

| # | MQTT Entity | Type | Why Keep MQTT |
|---|------------|------|---------------|
| 9 | LED Count | number | Requires LVA restart to take effect; not a good runtime entity |
| 10 | LED \<State\> Effect (×5) | select | Fork-specific LED hardware; no ESPHome equivalent upstream |
| 11 | LED \<State\> Color (×5) | light | MQTT JSON schema for RGB color has no clean ESPHome protobuf equivalent for config-style color pickers |

### Architectural Boundary

After migration, the split becomes:

- **ESPHome entities (on HA device page, no MQTT required):** Media player, mute, thinking sound, event sounds, wake word sensitivity, sound selection (×3), alarm duration — all voice/audio behavior
- **MQTT entities (require MQTT broker):** LED count, LED effects (×5), LED colors (×5) — all LED hardware

This is a clean, intuitive separation. Users without LEDs don't need MQTT at all. Users with LEDs get LED controls as a bonus when MQTT is configured.

---

## 10. Target ESPHome Entity List

After full migration, the fork will expose these ESPHome entities on the HA device page:

| Key | Entity Class | ESPHome Type | Protobuf | Persisted In |
|-----|-------------|--------------|----------|--------------|
| 0 | `MediaPlayerEntity` | media_player | `MediaPlayerCommandRequest` | `preferences.json` (volume) |
| 1 | `MuteSwitchEntity` | switch | `SwitchCommandRequest` | `ServerState` (runtime only) |
| 2 | `ThinkingSoundSwitchEntity` | switch | `SwitchCommandRequest` | `preferences.json` |
| 3 | `EventSoundsSwitchEntity` | switch | `SwitchCommandRequest` | `preferences.json` |
| 4 | `WakeWordSensitivityEntity` | select | `SelectCommandRequest` | `preferences.json` |
| 5 | `SoundSelectEntity` (wakeup) | select | `SelectCommandRequest` | `preferences.json` |
| 6 | `SoundSelectEntity` (thinking) | select | `SelectCommandRequest` | `preferences.json` |
| 7 | `SoundSelectEntity` (timer) | select | `SelectCommandRequest` | `preferences.json` |
| 8 | `AlarmDurationNumberEntity` | number | `NumberCommandRequest` | `preferences.json` |

Entity keys 0–8 are stable across reconnections. The satellite manages entity lifecycle by checking for existing entities by type and reusing them (upstream's pattern).

---

## 11. Recommended Realignment Plan (Revised)

### Phase 1: Entity System Foundation (High Priority)
**Goal:** Enable the fork to support upstream's ESPHome entity pattern alongside existing MQTT controls.

1. Update `ESPHomeEntity` base class OR add upstream's simpler base alongside
2. Remove entity list pruning in `VoiceSatelliteProtocol.__init__`
3. Add `SwitchCommandRequest`, `SelectCommandRequest`, `NumberCommandRequest` imports and message routing in `satellite.py`
4. Add all required protobuf imports to `entity.py`: `ListEntitiesSwitchResponse`, `ListEntitiesSelectResponse`, `ListEntitiesNumberResponse`, `SwitchStateResponse`, `SelectStateResponse`, `NumberStateResponse`, `SwitchCommandRequest`, `SelectCommandRequest`, `NumberCommandRequest`, `EntityCategory`
5. Move `self.state.satellite = self` to end of `__init__`
6. Implement upstream's entity lifecycle pattern (check-existing-by-type, reuse-or-create)

### Phase 2: Port Upstream Entities (High Priority)
**Goal:** Add upstream's ESPHome entities, replacing MQTT equivalents where overlapping.

1. Add `MuteSwitchEntity` to `entity.py` — wire to `ServerState.mic_muted` via callbacks
2. Add `ThinkingSoundSwitchEntity` to `entity.py` — wire to thinking sound loop toggle
3. Remove MQTT mute switch and MQTT thinking sound loop switch from `mqtt_controller.py`
4. Update `MicMuteHandler` to sync ESPHome entity state when mute changes from other sources (button, XVF3800)
5. Wire entity setup in `VoiceSatelliteProtocol.__init__`

### Phase 3: Fork-Specific ESPHome Entities (Medium Priority)
**Goal:** Migrate remaining voice/audio MQTT controls to ESPHome entities.

1. Add `EventSoundsSwitchEntity` — toggle `event_sounds_enabled` at runtime
2. Add `SoundSelectEntity` (generic, reusable for all 3 categories) — options populated from sound directory scan
3. Add `AlarmDurationNumberEntity` — number entity with min=0, max=3600, step=5
4. Remove corresponding MQTT entities from `mqtt_controller.py`
5. Add `event_sounds_enabled` and `alarm_duration_seconds` to `Preferences` persistence

### Phase 4: Wake Word Sensitivity (Medium Priority)
**Goal:** Implement PR #207's sensitivity control, integrated with fork's per-model thresholds.

1. Add `SENSITIVITY_PRESETS` dict to `satellite.py`
2. Add `WakeWordSensitivityEntity` to `entity.py`
3. Add `wake_word_sensitivity` to `Preferences` and `ServerState`
4. Add `oww_probability_cutoff` to `ServerState`
5. Add `_set_sensitivity()` / `_apply_sensitivity()` to satellite
6. Modify `process_audio()` in `__main__.py` to use `state.oww_probability_cutoff`
7. Define precedence: ESPHome preset > per-model JSON threshold > global config threshold

### Phase 5: MQTT Controller Cleanup (Low Priority)
**Goal:** Slim the MQTT controller to LED-only controls.

1. Remove all migrated entities from `mqtt_controller.py` (mute, sounds, thinking loop, alarm duration)
2. Verify MQTT device info still works with reduced entity set
3. Update documentation to reflect that MQTT is optional and only needed for LED controls
4. Update `README.md` entity tables

### Phase 6: Ongoing Sync Process
**Goal:** Establish a pattern for pulling upstream changes.

- Watch upstream releases and PRs
- For each upstream change, categorize:
  - **Direct port:** New ESPHome features, bug fixes, protocol changes
  - **Adapt:** Changes to files the fork has modified (satellite.py, entity.py, models.py, __main__.py)
  - **Skip:** Docker, PiCompose, env-var mapping, packaging

---

## 12. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Upstream refactors satellite.py significantly | Medium | High | Keep fork's satellite.py as close to upstream structure as possible |
| Upstream adds MQTT support natively | Low | Medium | If they do, it would likely align with fork's approach; LED controls would merge cleanly |
| Entity key conflicts (fork vs upstream numbering) | Medium | Low | Use stable key assignment (0=media player, 1+=config entities) |
| MQTT + ESPHome dual control for same function during migration | Medium | Medium | Migrate one entity at a time; test each before removing MQTT equivalent |
| Upstream changes Preferences schema | Medium | Low | Fork's field filtering already handles unknown keys |
| ESPHome number entity (`NumberCommandRequest`) not proven in upstream | Low | Low | The protobuf support exists in aioesphomeapi; upstream just hasn't used it yet. Same risk existed for select entities before PR #207. |
| HA device page shows too many entities | Low | Low | Use `EntityCategory.CONFIG` for all non-media-player entities so they appear under Configuration, not main controls |

---

## Appendix: Upstream PRs Tracked

| PR | Title | Status | Fork Impact |
|----|-------|--------|-------------|
| #207 | Wake word sensitivity | Open | ESPHome select entity + sensitivity presets; adopt in Phase 4 |
| #261 | Add maximum seconds for ringing timer | **Merged** | Adds `timer_max_ring_seconds` to `ServerState` + CLI arg; fork has this as runtime-adjustable MQTT entity, will become ESPHome number entity in Phase 3 |
| #277 | docs: document timer max ring seconds configuration | **Merged** | Documentation only |
