# Sendspin Client (Music Assistant Multiroom)

LVA includes a **Sendspin** client: Music Assistant streams synchronized
audio to the LVA over the network, and the LVA plays it in sync with other
Sendspin players. The client is built on [aiosendspin](https://github.com/Sendspin/aiosendspin)
9.x (Noise-encrypted protocol, pairing, time synchronization) with LVA's own
synchronized output stage.

## Requirements

| Requirement | Notes |
|---|---|
| Python **3.12+** | aiosendspin 9.x requirement; on 3.11 the subsystem disables itself with a log warning |
| `libportaudio2` | `sudo apt install libportaudio2` — used by the sounddevice output |
| Music Assistant with Sendspin enabled | Built into MA 2.7+ |
| The `sendspin` extra | `script/setup --sendspin` or `pip install -e '.[sendspin]'` |

## Configuration

```json
"sendspin": {
  "enabled": true,
  "pairing": { "pin": null },
  "connection": {
    "server_host": "192.168.0.100",
    "server_port": 8927,
    "server_path": "/sendspin"
  },
  "player": {
    "sync_target_latency_ms": 250,
    "output_latency_ms": 0,
    "output_device": null
  },
  "coordination": {
    "duck_during_voice": true,
    "duck_gain": 0.3
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `connection.server_host` | *(required)* | Music Assistant server address. There is no discovery — this must be set. |
| `connection.server_port` / `server_path` | 8927 / `/sendspin` | Where to connect. |
| `pairing.pin` | *(none)* | Fixed code to enter in MA when pairing. If unset, a dynamic PIN is written to the daemon log. |
| `player.sync_target_latency_ms` | 250 | Audio the server keeps buffered at this player. Also the playback start gate. Higher = more jitter headroom, more startup latency. |
| `player.output_latency_ms` | 0 | Static delay compensation, clamped to 0–5000 ms. Raise only if this device consistently plays early relative to others in the group. |
| `player.output_device` | *(system default)* | sounddevice output device name. |
| `coordination.duck_during_voice` | true | Duck the music while the voice assistant listens/thinks/responds. |
| `coordination.duck_gain` | 0.3 | Duck multiplier (0 = silence, 1 = no ducking). |

## Pairing (one-time per MA server)

1. Start LVA with the block above. When connected and unpaired, a pairing
   window opens automatically for 10 minutes and the log shows
   `Sendspin: server not yet paired — pairing window open`.
2. In Music Assistant, select the player, press **Setup**, and start pairing.
3. LVA logs `Sendspin: PAIRING PIN — enter this in Music Assistant: <code>`
   (or use your configured static pin). Enter it in MA.
4. Pairing credentials persist in `sendspin_pairing.json` next to
   `preferences.json`, together with the player identity in
   `sendspin_identity.json`. Reboots reconnect without re-pairing.
   **Keep these files when migrating** — a fresh install pairs as a new
   device.

Volume and mute commands from Music Assistant are applied to the output
stage and echoed back via `client/state`, so MA's slider always reflects the
device.

## Voice coordination (ducking)

While music plays, voice events from the assistant duck the music:
`voice_listen` / `voice_thinking` / `voice_responding` lower it by
`duck_gain`, `voice_idle` / `voice_error` restore it. Transitions are
logged at INFO:

```
Sendspin: music ducked (gain 0.30)
Sendspin: music restored (gain 1.00)
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `sendspin extra is not installed` warning at boot | aiosendspin isn't in the venv: rebuild with `script/setup --sendspin`. On Python 3.11 this is expected — upgrade Python first. |
| `sendspin.connection.server_host is not configured` | Add `server_host` to config.json (see above). |
| Connection fails; MA logs a protocol/encryption error | Your MA server requires the current protocol; make sure you run the aiosendspin-based client (this document) and that MA is up to date. |
| `Audio underflow detected` once at stream start | Harmless: the output re-anchors and continues. |
| `Audio underflow detected` repeatedly during playback | Raise `sync_target_latency_ms` (more buffer headroom) — the Pi may not be keeping up at the current rate. |
| MA slider out of sync with actual loudness | Fixed by the client/state echo; if it recurs, report with the log. |
| MA player shows "unavailable" permanently | Known MA issue ([support #5359](https://github.com/music-assistant/support/issues/5359)) — restart MA or remove/re-add the player. |
