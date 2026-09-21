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
| `libportaudio2` | Debian: `sudo apt install libportaudio2` — Fedora: `sudo dnf install portaudio` — used by the sounddevice output |
| Music Assistant with Sendspin enabled | Built into MA 2.7+ |
| The `sendspin` extra | `script/setup --sendspin` or `pip install -e '.[sendspin]'` |

## Configuration

```json
"sendspin": {
  "enabled": true,
  "pairing": { "pin": null, "voice_engine": "piper" },
  "connection": {
    "mdns": true,
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
| `connection.mdns` | true | Discover the Music Assistant server via mDNS (`_sendspin-server._tcp.local.`). Re-runs on every reconnect, so a moved server is picked up without a restart. |
| `connection.server_host` | *(none)* | Static Music Assistant server address. If you set server_host, discovery is bypassed. |
| `connection.server_port` / `server_path` | 8927 / `/sendspin` | Where to connect (also the discovery defaults). |
| `pairing.pin` | *(none)* | Fixed code to enter in MA when pairing. If unset, a dynamic PIN is written to the daemon log. |
| `pairing.speak_pin` | true | Announce the PIN through the device speaker; falls back to log-only if no TTS engine is available. |
| `pairing.voice_engine` | auto | `auto` (piper if model downloaded, else espeak-ng), `piper` (neural voice; downloads ~60 MB model on first use), or `espeak-ng`. |
| `pairing.piper_model` | en_US-lessac-medium | Piper voice model (HuggingFace name). |
| `pairing.voice` | *(server preference)* | espeak-ng voice override (e.g. `en-us`, `de`). Only applies to the espeak-ng engine. |
| `pairing.voice_speed` | 120 | Speaking rate in wpm. Only applies to the espeak-ng engine. |
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
   (or use your configured static pin) **and speaks the code through its
   speaker**. The Piper neural voice is preferred (`pairing.voice_engine`,
   ~60 MB model download on first use); espeak-ng is the fallback. Enter
   the code in MA.
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
| `sendspin extra is not installed` warning at boot | aiosendspin isn't in the venv: rebuild with `script/setup --sendspin` (updates via `script/update_lva --sendspin` keep it installed; the last-used flags are remembered). On Python 3.11 this is expected — upgrade Python first. |
| `server_host is not configured and mdns is disabled` | Enable `connection.mdns` or set `server_host` to the MA address (see above). |
| `no server advertised via mDNS` | MA is not reachable/announcing: check that Music Assistant runs and both devices are on the same network segment (mDNS does not cross VLANs). Or set `server_host` statically. |
| Connection fails; MA logs a protocol/encryption error | Your MA server requires the current protocol; make sure you run the aiosendspin-based client (this document) and that MA is up to date. |
| `Audio underflow detected` once at stream start | Harmless: the output re-anchors and continues. |
| `Audio underflow detected` repeatedly during playback | Raise `sync_target_latency_ms` (more buffer headroom) — the Pi may not be keeping up at the current rate. |
| MA slider out of sync with actual loudness | Fixed by the client/state echo; if it recurs, report with the log. |
| MA player shows "unavailable" permanently | Known MA issue ([support #5359](https://github.com/music-assistant/support/issues/5359)) — restart MA or remove/re-add the player. |
