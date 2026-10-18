# Wake Word Arbitration

When several voice satellites hear the same wake word, the closest one
should answer — not the fastest one. Wake word arbitration settles this
on the network: every device that detects the wake word broadcasts a
claim carrying how loud the word reached it, waits briefly, and only
answers when no rival heard it louder.

The protocol is shared across satellite kinds: a Linux Voice Assistant
satellite, an Android tablet running a satellite app, or any other
device implementing these facts can arbitrate against each other on the
same network segment.

## Turning it on

- HA device page, **Wake Arbitration** switch (configuration category).
  Toggling starts/stops the UDP listener at runtime; the choice is
  persisted in `preferences.json`.
- **Arbitration Window** number (100–500 ms, default 400): how long a
  device holds the wake to let rivals speak.
- **Arbitration Offset** number (−30..+30 dB, default 0): trims this
  device's energy by a constant, for rooms where one device's microphone
  systematically hears the space differently (a device behind a cabinet,
  a HAT with a hot preamp). Positive values make the device more likely
  to win.

Devices default to arbitration **off**; a fleet where no device enables
it behaves exactly as before.

## What the device measures

Energy is not raw loudness — a device with a hot microphone hears the
room louder too, so raw level would always win. The metric is dB over
the receiver's own noise floor:

- Audio is analyzed in 20 ms frames (320 samples at 16 kHz).
- **Speech level**: average of the loudest 10 of the last 75 frames
  (the wake word, whichever engine fired and however late).
- **Noise floor**: 20th percentile of the last 3 seconds (150 frames),
  clamped so a floor below −75 dBFS counts as −75. The clamp stops a
  noise suppressor from claiming an endless margin.
- **Energy = speech − floor**, rounded to 0.1 dB for the wire.

Levels are taken from the raw primary microphone channel, *before*
software gain, automatic gain control and noise suppression. The
suppressor silences the noise floor the metric is measured against and
would let a suppressed device win from across the room.

## The wire protocol

UDP port **2330**, JSON envelopes:

```json
{"ks": "wake", "v": 1, "id": "a1b2c3d4", "n": 123456789, "p": "okay nabu", "e": 38.4}
```

| Field | Meaning |
| ----- | ------- |
| `ks`  | protocol namespace, always `"wake"` |
| `v`   | protocol version, always `1` |
| `id`  | random per-run device id, lowercase hex, max 64 chars |
| `n`   | random nonce; `id:n` identifies one claim (copies are deduplicated) |
| `p`   | wake word phrase, lowercased, whitespace collapsed |
| `e`   | energy in dB over the sender's own noise floor, rounded to 0.1 |

Envelopes that fail any validation (wrong namespace/version, missing or
oversized ids, non-finite energies, booleans where numbers belong) are
dropped silently.

**Hellos.** Every 5 minutes a device broadcasts `{"ks": "wake", "v": 1,
"hello": true, "id": ...}` so the others know its unicast address. A
device hearing a hello from an unknown peer replies with its own hello,
addressed. Peers not heard from for 30 minutes are forgotten.

**Claims.** Broadcast is never acknowledged, so each claim also goes out
twice more (after 15 and 30 ms) plus addressed copies to every known
peer: Wi-Fi acknowledges unicast and an access point hands it to a
sleeping device sooner. Claim copies are deduplicated by `id:n`; the
first copy's arrival time counts. Claims are kept for 2 seconds.

## The decision

When a device detects the wake word it broadcasts a claim and holds for
the **window** (100–500 ms). Claims for the same normalized phrase that
arrive within ±window of its own detection compete:

- the highest energy wins;
- an exact tie is broken by the lexicographically lower id.

All devices that see all claims reach the same answer without a second
round. The winner answers; losers go back to listening silently.

**Overtaken.** A device that detected the wake word later sends its
claim later, and the trip over Wi-Fi comes on top. So the winner waits
one more window after its own; a louder claim for the same wake arriving
in that second window cancels the winner's turn before the user hears
more than the wake chime.

**Fail-open.** A claim that never arrives changes nothing: both devices
answer, which is the pre-arbitration behavior. The same applies to a
device whose listener failed to open (e.g. port in use) and to a device
without enough recent audio to measure — they simply answer. A muted
device never joins the settle at all: it answers nothing, so it must not
win for the others either.

## Latency cost

Arbitration adds up to one window (default 400 ms) to wake handling,
plus up to one more window for a winner that ends up standing down to a
late, louder claim. The settle blocks the wake path while it runs, which
is also what kiosk-class satellites do; with the default window, command
capture starts about 0.4 s after the wake word — the same order as the
wake chime that plays before listening anyway.

## Implementation notes (fork)

- `linux_voice_assistant/wake_arbitration.py` — metric, transport,
  arbiter, levels ring. Independent implementation of the documented
  protocol facts; no code ported from any other project (the reference
  implementation's license does not permit it).
- `satellite.py` — settles in `wakeup()` before the pipeline starts;
  `WakeLevelRing` is fed by the audio loop from the raw channel;
  stand-down teardown cancels a just-started turn.
- `__main__.py` — ring + arbiter boot; preferences persist via the
  existing `preferences.json` path.
- Tests: `tests/unit/test_wake_arbitration.py`.
