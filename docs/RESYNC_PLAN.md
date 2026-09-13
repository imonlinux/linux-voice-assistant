# LVA Fork Resync Plan

**Date:** 2026-09-12
**Scope:** `imonlinux/linux-voice-assistant` (fork) vs `OHF-Voice/linux-voice-assistant` (upstream)
**Status:** Recommendation — no code changes made yet
**Related:** `docs/LVA_UPSTREAM_DEVIATION_ANALYSIS_v2.md` (April 2026, deleted from tree; recoverable at `git show 46b16fa^:docs/LVA_UPSTREAM_DEVIATION_ANALYSIS_v2.md`) — this plan supersedes it and preserves its architectural boundary.

---

## 1. Executive summary

The fork and upstream have rewritten the same core files in opposite directions. A git merge or
rebase cannot converge them, and the cherry-pick approach now has a backlog of ~295 of 303 upstream
commits, most of which are architectural and cannot be picked — only re-implemented.

**Recommendation: re-found the fork on upstream's current core** (branch from `upstream/main`, port
fork capabilities back on top as add-on modules), preceded by landing `origin/development` as the
source of truth (see §7). The fork's differentiating features live in unusually well-partitioned
files that barely overlap upstream's core, so the port is mostly "re-attach at the seams" rather
than a rewrite. Afterward, `git merge upstream/main` becomes cheap again.

The boundary established by the deviation analysis still holds and is compatible with upstream's
direction:

> **ESPHome owns voice/audio controls; MQTT owns LED hardware (and the tray transport).**

Upstream went further and removed MQTT entirely, exposing everything as ESPHome device-page
entities, and added a WebSocket Peripheral API for out-of-process hardware support. The fork keeps
MQTT for LED controls + tray mirroring and keeps hardware support in-daemon — that is the fork's
differentiator and is preserved.

---

## 2. Current state

| Ref | SHA | Position |
|---|---|---|
| Merge base with upstream | `7a4b3e6` | 2025-09-18, just after upstream v1.0.0 |
| `main` = `origin/main` | `4c67ec4` | 574 commits ahead of merge base |
| `upstream/main` tip | `7c6fbaa` | 2026-09-03, past v1.1.15; 303 commits ahead of merge base |
| `origin/development` | `9e19d46` | 6 ahead of / 3 behind `main` (see §7) |
| `origin/Archive` | `bd62a3b` | Pre-refactor history; not relevant |

244 files differ between the fork and upstream (+19,867 / −22,317). Core-file rewrite depth:

| File | fork lines | upstream lines | diff (+add/−del) |
|---|---|---|---|
| `satellite.py` | 1,033 | 1,165 | +775 / −688 |
| `__main__.py` | 1,106 | 850 | +722 / −925 |
| `entity.py` | 590 | 824 | +491 / −268 |
| `models.py` | 222 | 278 | +171 / −125 |
| `mpv_player.py` | 334 | (split into `player/`) | +95 / −261 |
| `api_server.py` | 165 | 192 | +37 / −11 |
| `util.py` | 134 | 104 | +67 / −97 |

Upstream also deleted `microwakeword.py`/`openwakeword.py` bodies into a new `wake_word.py` and
replaced vendored tflite blobs with pip packages; the fork had already mirrored the pip packages
but kept the modules as stubs.

The `upstream` remote is configured in the working clone:
`git remote add upstream https://github.com/OHF-Voice/linux-voice-assistant.git`

---

## 3. What upstream is now (review summary)

### 3.1 Control plane: ESPHome API only, zero MQTT

`git grep -i mqtt upstream/main` returns nothing. Device-page entities registered in
`VoiceSatelliteProtocol.__init__` (`satellite.py`), classes in `entity.py` (824 lines):

| Entity | Type | object_id / notes |
|---|---|---|
| Media Player | `MediaPlayerEntity` | dual players (music + TTS announce); volume persisted |
| Mute | `MuteSwitchEntity` (switch, CONFIG) | syncs with mic state |
| Thinking Sound | switch (CONFIG) | |
| Mic Auto Gain | number 0–31 (`mic_gain`) | WebRTC AGC, persisted |
| Mic Noise Suppression | select Off…Max (`mic_noise`) | WebRTC NS, persisted |
| Mic Volume | number 1–100 (`mic_volume`) | software gain, persisted |
| Wake Word 1/2 Sensitivity | number 0–1 step 0.001 | dynamic `probability_cutoff`, persisted |
| Stop Word Sensitivity | number 0–1 | restored on startup (`d1f5761`) |
| LED Light | light, RGB+brightness+effects | **opt-in**: created when a peripheral sends `register_light` |
| Button Press Event | event entity (button) | **opt-in** via peripheral `register_button` |

Wake-word model switching is not a select entity — it uses the ESPHome voice-assistant
configuration protocol (`VoiceAssistantConfigurationRequest/Response` + `SetConfiguration`),
with runtime download of external wake-word models (`716fe30`, PR #55) and MWW/OWW variant
selection (`4a0695a`, PR #348). `ServerState.broadcast()` (`d811ae7`) fans state changes to all
connected API clients.

### 3.2 Peripheral API: the sanctioned hardware extension point

`peripheral_api.py` (added `2d460b6`, PR #266, July 2026): JSON-over-WebSocket on port 6055,
no auth, `websockets==12`. Documented in `docs/peripheral_api.md`.

- LVA → client events: `wake_word_detected`, `listening`, `stt_text`, `thinking`, `tts_text`,
  `tts_speaking`, `tts_finished`, `pipeline_error`, `idle`, `muted`, `timer_*`,
  `media_player_playing`, `volume_changed`, `volume_muted`, `zeroconf`, `light_command`.
- Client → LVA commands: `start_listening`, `stop_pipeline`, `mute_mic`/`unmute_mic`,
  `volume_up/down/set`, `stop_timer_ringing`, media-player transport, `button_*_press`,
  `register_light`, `register_button`.
- Registrations materialize real ESPHome entities on the device page (`LEDLightEntity`,
  `ButtonEventSensor`); HA changes route back to the peripheral as `light_command`.
- All board support (Satellite1 HAT, ReSpeaker 2-mic/4-mic HAT, ReSpeaker USB Mic Array v2)
  lives in `examples/` Docker containers — nothing hardware-specific is in the daemon.
- `2018961` (PR #357) fixed the ReSpeaker USB Mic Array v2 LED vendor commands **inside the
  example** (command `0x06` custom frames, `0x20` brightness, wIndex `0x1C`). Useful reference
  for the fork's XVF3800 USB backend; not an in-daemon integration.

### 3.3 Core architecture

- `player/` package: `base.py` (`AudioPlayer` ABC), `state.py` (`PlayerState` enum),
  `libmpv.py` (`LibMpvPlayer`: state lock, user-volume + duck factor, `cache=yes` 32 MiB,
  `audio-buffer=0.8`). `mpv_player.py` is now a thin facade. Two instances: `music_player` +
  `tts_player` (announce with ducking/resume).
- `wake_word.py`: discovery, load with type preference + fallback chain; engines are
  `pymicro-wakeword` / `pyopen-wakeword` pip libs; input via `soundcard`.
- `webrtc.py`: lazy `webrtc-noise-gain` wrapper (10 ms framing).
- `satellite.py` (~1,165 lines): big entity-registration `__init__`, idempotent entity reuse
  across reconnects, output-only mode, dual-channel audio (`data2` AEC reference),
  `continue_conversation_delay`, `listen_during_wake_sound`, peripheral event emission.
- Notable user features: `--music-output-device` (`43a183a`, PR #350), `--listen-during-wake-sound`
  (`a66c0e4`, PR #273), output-only mode (`7732be0`, `341e3dc`), `--timer-max-ring-seconds`
  (`4194aa4`, PR #261), mute/unmute + button-press sounds, colored debug logging (`2eb2947`),
  `linux-voice-assistant` console script, version from git tags.
- Infra: `tests/unit/` (pytest + pytest-asyncio, incl. `test_peripheral_api.py` 915 L),
  five lint pipelines, Docker/ghcr release+nightly workflows, changelog automation,
  governance docs (`c37b482`), Kilo agent config (`3b33730`).

### 3.4 What upstream does NOT have (verified by grep against `upstream/main`)

Sendspin, tray client, in-daemon GPIO/lgpio LEDs, XVF3800 (anywhere), ReSpeaker 2-mic HAT button,
config.json, EventBus. Upstream's answer to hardware is the peripheral-API container model; the
fork's answer is native in-daemon support. **This is the philosophical fork in the road and the
fork's differentiator.**

Releases: v1.0.0 (2025-09-08) → v1.1.0 (2026-02-10, Docker) → … → v1.1.13 (2026-07-18,
MWW/OWW switch, peripheral events, tests) → v1.1.14 (2026-07-28) → v1.1.15 (2026-08-02,
`--music-output-device`) → tip 7c6fbaa (2026-09-03).

---

## 4. Fork differentiators to preserve

| Feature | Files | Notes |
|---|---|---|
| Typed config system | `config.py`, `config.json(.example)` | 582 L; sections app/audio/wake_word/esphome/led/mqtt/button/sendspin/tray |
| Event bus | `event_bus.py` | 81 L sync pub/sub decoupling controllers from pipeline |
| Audio engine + volume sync | `audio_engine.py`, `audio_volume.py` | wpctl→pactl→amixer OS sink sync |
| LED controller | `led_controller.py` | 725 L; per-state effect/color/brightness; DotStar/NeoPixel over SPI **and** GPIO; XVF3800 backend auto-select |
| 2-mic HAT button | `button_controller.py` | GPIO pin 17; short wake/stop, long mute |
| XVF3800 | `xvf3800_button_controller.py`, `xvf3800_led_backend.py`, `XVF3800/*.rules` | raw pyusb control transfers; bidirectional HW-mute ↔ HA sync |
| MQTT controller | `mqtt_controller.py` | 389 L; LED-only since upstream_refactor (5 selects + 5 lights + number + tray topics) |
| Sendspin client | `sendspin/` (~3,300 L) | Music Assistant multiroom; Kalman clock sync; PCM/FLAC/Opus; ducking |
| Tray client | `tray_client/` | PyQt5; mirrors state via MQTT; systemd service control |
| ESPHome entity set | `entity.py` | 9 entities: media player, mute, thinking loop, event sounds, 3 sound selects, alarm duration, wake-word sensitivity |
| Per-model OWW thresholds | model `.json` + loader | precedence per-model JSON > global config |
| Stable identity | `util.py`, `__main__.py` | persisted MAC in preferences.json |
| Sounds & wake words | `sounds/{wakeup,thinking,timer}/`, 7 extra OWW models | reorganized dirs |
| Deployment | `service/*.service`, `script/tray`, `script/update_lva`, `respeaker2mic/` | systemd user units, tray bound to graphical-session.target |
| Tests | `tests/` (~7,500 L, 286 tests) | mostly fork-module-specific; CI matrix 3.11–3.13 |

---

## 5. Options evaluated

### A. `git merge upstream/main` into the fork — rejected
Both sides rewrote the entire collision zone (`satellite.py`, `__main__.py`, `entity.py`,
`models.py`, `mpv_player.py`, wake-word modules). The merge would be hand-resolved file by file —
the same work as re-foundation but from a worse starting position, with a Frankenstein
architecture (fork's EventBus/config.json shell around upstream internals that assume CLI args
and no EventBus), and future merges would never get easier.

### B. Rebase the fork onto upstream — rejected
574 commits over fully rewritten files = per-commit conflict explosion. Not feasible.

### C. Continue batched cherry-picking (current approach) — fallback only
Works for small bug fixes (Phase 0/1 on main proved it) but cannot carry architectural changes
(`player/`, `wake_word.py`, full entity set, peripheral API). Backlog ~295 commits and compounding.
This is the treadmill the deviation analysis predicted. Viable only if the decision is "do not
resync, treat upstream as an idea source."

### D. Re-foundation (recommended)
Branch from `upstream/main`; port fork capabilities on top as add-on modules in dependency order.
Flips the direction of the port: instead of dragging upstream's rewrites through 574 fork commits,
re-attach ~10 well-bounded fork modules (~10k lines, mostly porting as-is) to upstream's core.
Matches upstream's control-plane direction, ends the treadmill, and restores cheap merges.

---

## 6. Recommended path: re-foundation in phases

### Phase R0 — Reconcile and document (small, do first)
1. Land `origin/development` as the source of truth (procedure and rationale in §7).
2. Restore `docs/LVA_UPSTREAM_DEVIATION_ANALYSIS_v2.md` content into a living
   `docs/RESYNC_PLAN.md` (or keep this document alongside the repo) and record decisions.
3. Tag the pre-resync fork baseline (e.g., `fork-last-divergent`) for reference.

### Phase R1 — Re-foundation (the big one)
Create branch `upstream-core` from `upstream/main`. Port in dependency order:

1. **Config shim (highest-leverage convergence move).** Keep `config.json`, but implement it as a
   front-end to upstream's argparse (feed `parser.set_defaults` / translate sections → args)
   rather than a parallel config path. The fork then tracks upstream's `__main__.py` instead of
   maintaining a permanent 1,100-line rewrite of it. New upstream CLI args map to new config
   sections incrementally.
2. **EventBus + seams.** Re-attach `event_bus.py`; feed it from the same emission points upstream
   added for the peripheral API (`_emit` in `satellite.py`). Controllers consume the bus exactly
   as today. This shared seam keeps a future peripheral-API migration open without committing to it.
3. **Entity set merge.** Adopt upstream `entity.py` (gains mic gain/noise/volume + three numeric
   sensitivities + broadcast + reconnect-reuse for free). Append fork-only entities (event sounds
   switch, 3 sound selects, alarm duration number) with keys **after** upstream's — do not reuse
   upstream key numbering. Drop fork's duplicate mute/thinking/sensitivity entities in favor of
   upstream's versions. Reconciliation: upstream replaced PR #207's preset select with numeric
   sensitivities — adopt the numbers; keep per-model JSON threshold as the finer precedence tier.
4. **Wake words.** Adopt `wake_word.py`; port per-model threshold loading and the 7 extra models.
   Keep `microwakeword.py`/`openwakeword.py` as shims only if tests need them.
5. **Player.** Adopt `player/`; re-add fork behaviors on top (timer alarm auto-stop/loop
   semantics via config `alarm_duration_seconds`, `volume_override` for sound leveling).
   Upstream's dual-player design supersedes the mpv fix cluster that Phases 0/1 re-picked.

### Phase R2 — Hardware re-attach
Port `led_controller.py`, `button_controller.py`, `xvf3800_*`, `audio_volume.py` essentially
as-is (self-contained, EventBus-fed). Keep `mqtt_controller.py` LED-only + tray transport.

**Deliberate choice: stay in-daemon.** Do not convert to peripheral-API clients. Reasons: native
(non-Docker) installs are the fork's audience; XVF3800 mute sync is deeply bidirectional;
config-gated optional imports are already the pattern. Revisit only if upstream's peripheral
ecosystem makes containerization attractive later; consider upstreaming the XVF3800 backend as an
`examples/` peripheral container (it would be a welcome contribution — upstream has ReSpeaker
USB Array v2 but not XVF3800).

### Phase R3 — Subsystems
`sendspin/` and `tray_client/` port nearly unchanged; the real work is rewiring sendspin's
ducking/volume to upstream's `player/` package and PlayerState. Keep tray-on-MQTT. Carry over
services, scripts, install docs (Pi OS/Fedora/Nobara native paths).

### Phase R4 — Tests and CI
Fork's ~286 tests mostly target fork modules and travel with them. Adopt upstream's
`tests/unit/` for the core; port `test_entity.py`/`test_satellite.py` expectations to the merged
entity set. Close deferred items F3 (MQTT tests vs slimmed controller) and F4 (JSONC unification).
Reconcile CI (fork matrix 3.11–3.13 vs upstream lint workflow).

### Ongoing after re-foundation
`git merge upstream/main` per upstream release; new upstream features arrive as merges.
Cherry-pick only into ported-module seams. Watch: new entity types, player state changes,
peripheral event vocabulary.

---

## 7. Landing `origin/development` as the source of truth (elaboration)

### 7.1 Branch topology

Both branches descend from `961858d` ("Fix systemd unit: bind tray to graphical session") and
implemented the Phase 0/1 upstream picks **independently**:

- `main` (3 commits): `e580a97` Phase 0 → `87e6203` Phase 1 → `4c67ec4` docs GPIO
- `development` (6 commits): `247e421` Phase 0+1 combined → `888193c` Phase 2 →
  `0cf9b40` F1 → `f4e5e89` F2+F5 → `cbe1848` D1+D2 → `9e19d46` docs GPIO

Net delta `main → development`: 8 files, +299/−133. Development is a **strict functional
superset** of main; main's two phase commits are superseded implementations, and the docs commit
(`9e19d46`) is content-identical to main's `4c67ec4`. All of development's core modules
compile cleanly post-`cbe1848` (verified via `py_compile` on 2026-09-12).

### 7.2 What development contains, commit by commit

**`247e421` — Phase 0 + Phase 1: eight re-implementations of upstream fixes** (these are manual
ports, not git cherry-picks; no trailers):

| Pick | Upstream origin | What it does |
|---|---|---|
| 0.1 | #159 (`614bce4`, timer-loop suppress) | Timer alarm repeat via `loop.call_later` instead of `time.sleep(1.0)` — stops blocking the event loop during alarm repeats |
| 0.2 | fork-local defect | Removes stray `self._pipeline_active = True` fragment in `_start_conversation` (half-ported line causing state inconsistency) |
| 0.3 | fork-local defect (MQTT thread safety) | Marshals post-connect bootstrap + discovery publishing onto the asyncio loop via `call_soon_threadsafe` from the paho callback thread |
| 0.4 | fork-local feature | JSONC support (`//`, `/* */`) so `config.json` can carry comments — `_load_json_with_comments()` in `config.py`, used by `load_config_from_json()` and the Sendspin section loader in `__main__.py` |
| 1.5 (C) | upstream mpv cluster (`2ccb1fb`/`f5b76b8` era) | `_stop_for_replacement()` in `mpv_player.py`: stops current media **without firing** its done_callback, so replacing playback can't trigger a stale callback |
| 1.6 (B) | upstream mpv cluster | `player.pause = False` in `play()` — a paused player given a new URL must actually play |
| 1.7 (A) | upstream mpv cluster | Removes double `_tts_finished()` in `satellite.stop()` — `tts_player.stop()` already fires the callback internally |
| 1.8 (D) | upstream mpv cluster | `audio_buffer="0.8"` + `audio_stream_silence=True` — fixes short sounds starting mid-way / first samples dropped; commit message carries an explicit verification note about the idle-active observer interaction |

**`888193c` — Phase 2: three behavior picks** (Pick M, mute/unmute confirmation sounds,
explicitly skipped/deferred):

| Pick | Upstream origin | What it does |
|---|---|---|
| F | #275 (`88fb2c5`) | A wake word detected while the timer alarm is ringing continues straight into listening; `_play_timer_finished()` only unducks from IDLE — no unwanted unduck mid-conversation |
| G | #342 (`1d84fa0`) | `continue_conversation_delay` (default 0.5 s) with delayed continue via `loop.call_later` + `_start_continued_conversation()` (thread-safe alternative to upstream's `threading.Timer`), checking `mic_muted` and connection state |
| E | upstream entity lifecycle pattern | `update_get`/`update_set` methods on all six fork entities + `_setup_entity`/`_setup_entity_by_id` recreation on reconnect — fixes stale lambda closures over a dead protocol instance |

**Review-fix commits (F/D findings from reviewing the picks):**

| Commit | Finding | What it fixes |
|---|---|---|
| `0cf9b40` | **F1 (HIGH)** | Phase 2.2 referenced nonexistent `state.config.app.…` — adds `continue_conversation_delay: float = 0.5` to the `ServerState` dataclass, populates it in `_create_server_state`, reads it in `_determine_final_state` |
| `f4e5e89` | **F2 + F5** | Callbacks passed separately to `_setup_entity*` and rebound via `update_*` on reconnect (F2); scheduled-handle cleanup — `_timer_repeat_handle` cancelled in `_stop_timer_alarm`/alarm-clear, `_continue_conversation_handle` cancelled in `connection_lost` and on reschedule (F5) |
| `cbe1848` | **D1 (FATAL) + D2** | Removes the stray `)` after `AlarmDurationNumberEntity` that made `entity.py` un-importable (888193c shipped broken); restores **end-relative** timer repeat via `_schedule_timer_repeat()` — repeat fires 1 s *after* the sound finishes, fixing stutter on alarm sounds ≥ 1 s |
| `9e19d46` | docs | GPIO system dependencies for lgpio (content-identical to main's `4c67ec4`) |

### 7.3 Why development, not main

1. **Strict superset.** Development contains every upstream fix main contains (in reviewed,
   fixed-up form) **plus** Phase 2 behavior fixes (F, G, E) and the F1/F2/F5/D1/D2 corrections.
   Choosing main would mean re-doing Phase 2 and the fixes; choosing development loses nothing.
2. **It is the internally consistent branch.** Development went through a review pass; its known
   defects were found and fixed (F1, D1). Main's parallel Phase 0/1 was never given the same
   review, and the two implementations cannot be merged — resolving them is a choice, not a merge.
3. **Its implementation deltas are equal or better on every point** (detail in §7.4). None of
   main's variant choices is worth preserving at the cost of a third hybrid implementation.

### 7.4 The four implementation deltas and the verdict on each

| Topic | main | development | Verdict |
|---|---|---|---|
| JSONC support | `load_jsonc()` in `util.py` (+67, generic helper) | `_load_json_with_comments()` in `config.py` (+51, scoped to config consumers) | **Keep development's.** One implementation, placed next to its only consumers. If the preferences loader later needs JSONC, promote to `util.py` then — that is exactly deferred item F4. |
| mpv `audio_buffer` | `0.5` (numeric) + `stream_silence=True` | `"0.8"` (string) + `audio_stream_silence=True` + verification note | **Keep development's.** Matches upstream's settled value (`audio-buffer=0.8` in `player/libmpv.py`), documented rationale (underrun fix for short sounds). Open item: verify the idle-active end-of-playback observer still transitions correctly with stream silence (noted in the commit). |
| Pause reset placement | End of `play()` (after `playlist_pos = 0`) | Beginning of `play()` (right after `_stop_for_replacement()`) | **Keep development's.** Clearing pause before starting new media matches the intent (new media must play, not inherit pause) and was the version that went through review. Becomes moot at R1 when upstream's `player/` package replaces this code. |
| MQTT `_on_connect` marshaling | Subscriptions stay in the paho thread; only bootstrap timer + discovery are marshaled to the loop | Everything (`_on_connect_impl`: bootstrap reset, timer, subscriptions, discovery publishing) marshaled to the loop in one call | **Keep development's.** Single entry point on the loop thread; uniform and more conservative. paho's `subscribe()` is thread-safe, but there is no benefit to splitting the work across threads. |

### 7.5 Known loose ends inherited from development (carry into R0/R4)

- **F3 (deferred):** `test_mqtt_controller.py` and `test_end_to_end_workflows.py` predate the
  rebind pattern — expect failures against `update_*`/`_setup_entity` changes until updated.
- **F4 (deferred):** JSONC implementation is now singular (development's), but revisit placement
  (`config.py` vs `util.py`) when the preferences loader needs it.
- **Pick M (deferred):** mute/unmute confirmation sounds — optional; upstream has them
  (`1352cb3`); port at R1 with the upstream sound plumbing if wanted.
- **audio_buffer acceptance test:** idle-active observer vs `audio_stream_silence` (1.8's note).
- Shared by both branches (not development's fault): CI workflow still triggers on the stale
  `upstream_refactor` branch name.

### 7.6 Landing procedure (concrete)

```bash
git checkout main
git merge origin/development
# Conflicts expected in: satellite.py, config.py, util.py, mpv_player.py,
# mqtt_controller.py, __main__.py, models.py, entity.py
# Resolution policy per §7.4: take development's side everywhere;
# drop main's util.py load_jsonc and main's Phase 0/1 variants.
```

Then: run the test suite, fix the F3-affected MQTT tests, syntax/import check, tag as
`pre-refoundation`. From that point R1 proceeds from `upstream/main` — main and development
never need to be reconciled again.

---

## 8. Upstream commit disposition appendix

Tracking table for the significant upstream work. "Picked" = already re-implemented on
development; "R1–R4" = port during that phase; "Skip" = not applicable to the fork; "Ref" = use
as reference only.

| Upstream PR / commit | Theme | Disposition |
|---|---|---|
| #159 `614bce4` timer-loop suppress | timers | **Picked** (Phase 0.1) |
| #261 `4194aa4` timer max ring | timers | Fork equivalent exists (`AlarmDurationNumberEntity`, runtime-adjustable); align naming/defaults at R1 |
| #275 `88fb2c5` wake during ring → listening | timers | **Picked** (Phase 2.1 / Pick F) |
| #342 `1d84fa0` continue-conversation delay | pipeline | **Picked** (Phase 2.2 + F1) |
| #207 `3ea3289` sensitivity | wake words | Fork entity exists; **supersede with upstream numeric entities at R1**; keep per-model JSON precedence |
| `d1f5761` stop-word sensitivity restore | wake words | R1 (comes with upstream entity adoption) |
| `4a0695a` MWW/OWW switch from UI (#348) | wake words | R1 (adopt upstream protocol-based switching) |
| `716fe30` external wake-word download (#55) | wake words | R1 |
| `72c8f02`/`0f73f2e` pip engines + soundcard | wake words | Already mirrored by fork; adopt `wake_word.py` at R1 |
| mpv cluster (`2ccb1fb`, `f5b76b8`, `4c9d92f`, `9dcc454`, `282aa7e`) | audio | Partially picked (Phase 1); **superseded by `player/` at R1** — re-add alarm auto-stop + `volume_override` on top |
| `472fd42` mute switch, `f81a119` thinking sound | entities | Fork has equivalents; adopt upstream versions at R1 |
| `c284806`/`4ee7bb0` mic gain/NS/volume entities | entities | R1 (new capability for fork) |
| `d811ae7` broadcast to all clients | entities | R1 |
| `2d460b6` peripheral API (#266) + subsequent fixes (`ba7d834`, `8303a96`, `adcef57`, `99ee08f`) | hardware | R1 (adopt server + event seams; feeds EventBus). Example scripts = Ref; Satellite1 hold-repeat not applicable unless porting examples |
| `2018961` ReSpeaker USB Array v2 LED fix (#357) | hardware | Ref for XVF3800 USB command patterns |
| `43a183a` `--music-output-device` (#350) | audio | R1 |
| `a66c0e4` `--listen-during-wake-sound` (#273) | pipeline | R1 |
| `7732be0`/`341e3dc` output-only mode | pipeline | R1 (optional, config-gated) |
| `eb29a00` dual-channel AEC input (#334) + AEC docs | audio | R1/R3 (fork already has PipeWire AEC docs; reconcile) |
| `1352cb3` mute/unmute sounds; button-press sounds | sounds | R1 (also covers deferred Pick M) |
| `2eb2947` colored debug logging | logging | R1 (low effort) |
| `9b2a8b4` tests (#312) + lint pipelines | infra | R4 |
| Docker/ghcr workflows, governance, Kilo config, dependabot | infra | Skip (fork keeps its own CI; native-install focus) |
| `0081f2e` version from git tags, `9067648` console script | packaging | R3 (nice-to-have) |

---

## 9. Decision log

| # | Decision | Rationale |
|---|---|---|
| D1 | Re-foundation on upstream core (Option D) | Merge/rebase infeasible; picking cannot carry architecture; restores cheap future merges |
| D2 | Land `origin/development` as source of truth | Strict reviewed superset; consistent post-fixes; §7.4 deltas all favor it |
| D3 | Keep `config.json` as argparse front-end | Preserves fork UX while letting fork track upstream `__main__.py` |
| D4 | Keep hardware in-daemon (not peripheral-API clients) | Native-install audience; deep XVF3800 mute sync; peripheral seam kept open via shared event emission points |
| D5 | Keep MQTT for LED controls + tray | Established boundary; self-contained; no upstream MQTT to conflict with |
| D6 | Adopt upstream numeric sensitivities; per-model JSON as finer tier | Upstream superseded PR #207 select; fork's granularity remains as override |
| D7 | Adopt upstream `player/` package | Ends mpv fix churn; fork behaviors re-added on top |

## 10. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Config shim complexity (upstream keeps adding CLI args) | Medium | Shim is additive per-arg; default to upstream CLI behavior when a config key is absent |
| Entity-key collisions after merge (HA caches per key) | Medium | Upstream keys first, fork entities appended after; document mapping in RESYNC_PLAN |
| Sendspin ducking vs new `player/` states | Medium | R3 has explicit rewiring step + `test_sendspin_client.py` travels with module |
| XVF3800 regressions invisible to CI | High | Hardware-in-the-loop pass on real kit at end of R2 (`tests/xvf3800_probe.py` exists) |
| F3 MQTT test debt | Low | Scheduled in R0/R4 |
| Upstream breaks seams (entity registration, `_emit` points) | Medium | After re-foundation these arrive as ordinary merges; keep seam adapters thin |
