
# Debugging

## Explanation of the audio system:

The Linux audio stack consists of multiple layers working together:

```
┌─────────────────────────────────────────────────────────────┐
│                     HOST SYSTEM                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              User Applications                      │    │
│  │  (Browser, Media Player, Voice Assistant, etc.)     │    │
│  └────────────────┬────────────────────────────────────┘    │
│                   │                                         │
│  ┌────────────────▼────────────────────────────────────┐    │
│  │         PipeWire / PulseAudio                       │    │
│  │         (Sound Server)                              │    │
│  │  - Audio mixing & routing                           │    │
│  │  - Device abstraction                               │    │
│  │  - Network audio support                            │    │
│  │  - Per-application volume control                   │    │
│  └────────────────┬────────────────────────────────────┘    │
│                   │                                         │
│  ┌────────────────▼────────────────────────────────────┐    │
│  │              ALSA                                   │    │
│  │  (Advanced Linux Sound Architecture)                │    │
│  │  - Kernel-level audio driver framework              │    │
│  │  - Direct hardware access                           │    │
│  │  - Controls: /dev/snd/                              │    │
│  └────────────────┬────────────────────────────────────┘    │
│                   │                                         │
│  ┌────────────────▼────────────────────────────────────┐    │
│  │           Hardware (Sound Card)                     │    │
│  │   ┌─────────┐  ┌─────────┐  ┌───────────────────┐   │    │
│  │   │Microphon│  │ Speaker │  │   DSP/Codec       │   │    │
│  │   └─────────┘  └─────────┘  └───────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  LVA_XDG_RUNTIME_DIR=/run/user/1000                             │
│  └── Socket: /run/user/1000/pipewire-0                      │
│      Socket: /run/user/1000/pulse/native                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ Device Passthrough
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   DOCKER CONTAINER                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Container Process                      │    │
│  │  (Linux Voice Assistant)                            │    │
│  │                                                     │    │
│  │  Accesses audio via:                                │    │
│  │  - PipeWire/PulseAudio client library               │    │
│  │  - Uses same LVA_XDG_RUNTIME_DIR socket path            │    │
│  │                                                     │    │
│  │  Environment Variables:                             │    │
│  │  - LVA_XDG_RUNTIME_DIR=/run/user/1000 (host path)       │    │
│  │  - AUDIO_INPUT_DEVICE="default"                     │    │
│  │  - AUDIO_OUTPUT_DEVICE="default"                    │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Device Access:                                             │
│  - Socket: /run/user/1000/pipewire-0 (volume mount)         │
│  - Socket: /run/user/1000/pulse/native (volume mount)       │
└─────────────────────────────────────────────────────────────┘
```

### How it works:

1. **Host Layer**:
   - ALSA provides kernel drivers for audio hardware
   - PipeWire/PulseAudio sits on top of ALSA as a sound server
   - Applications connect to PipeWire/PulseAudio, not directly to ALSA, otherwhise the container would need access to the ALSA devices. If you use ALSA only only one application can connect to the device at a time.

2. **Docker Integration**:
   - The LVA_XDG_RUNTIME_DIR socket must be mounted from host to container
   - The container user must have matching UID/GID with host user or the audio group is added.

3. **Communication Flow**:
   ```
   Container App → PipeWire Client Library → Host PipeWire/PulseAudio
         ↓                                         ↓
   (via mounted socket)                    (via ALSA drivers)
         ↓                                         ↓
   ┌─────────────────────────────────────────────────────┐
   │              Audio Data flows to Hardware           │
   └─────────────────────────────────────────────────────┘
   ```

4. **Key Configuration Points**:
   - `LVA_XDG_RUNTIME_DIR` must point to host's runtime directory
   - Container user UID/GID should match host user

### Docker Compose Example:

```yaml
services:
  voice-assistant:
    image: ghcr.io/ohf-voice/linux-voice-assistant:latest
    environment:
      - LVA_XDG_RUNTIME_DIR=/run/user/1000
      - AUDIO_INPUT_DEVICE=default
      - AUDIO_OUTPUT_DEVICE=default
    volumes:
      - /run/user/1000:/run/user/1000:rw
    devices:
      - /dev/snd:/dev/snd
    group_add:
      - audio
```


## List available audio devices:

To list available audio devices, run:

```bash
# List audio devices
docker run --rm -it -e LIST_DEVICES="1" ghcr.io/florian-asche/linux-voice-assistant:develop-final-docker-version
```

Update the `.env` file with your device names:

``` ini
AUDIO_INPUT_DEVICE="default"
AUDIO_OUTPUT_DEVICE="default"
```


## Pipewire runtime directory (Optional):

If you work with the root user, you need to:
``` sh
export LVA_XDG_RUNTIME_DIR=/run/user/${USERLVA_USER_ID
```

💡 **Note:** Replace `$USERLVA_USER_IDth your actual user id that you want to run the voice assistant.


## Troubleshooting:

### Audio Device Not Found:

If the container cannot access audio devices, ensure:

1. The USERLVA_USER_ID`.env` matches your actual users ID (run `id -u $USER` to check)
2. The LVA_XDG_RUNTIME_DIR path exists on the host

### Permission Denied Errors:

tbd

### Low Audio Quality:

If you experience audio quality issues:

1. Check the audio device settings with `alsamixer`
2. Try setting `AUDIO_INPUT_DEVICE` and `AUDIO_OUTPUT_DEVICE` explicitly
3. Adjust the microphone gain in your system settings

### Wake Word Arbitration: Both Devices Answer:

Wake word arbitration settles which satellite answers when several of them hear
the same wake word: the device that heard it loudest responds, the rest stand
down. When two devices both answer the same utterance, the usual cause is a
**wake word name mismatch** — claims match on the configured wake word string,
not on the audio, so `HAL` on one device and `Hal V2` on another arbitrate as
two different words and both answer.

To diagnose, enable the Wake Arbitration switch on each device's HA device
page, say the wake word once, and read each device's log (`script/run --debug`,
or `journalctl --user -u linux-voice-assistant -f`):

```text
# Won: this device answers. An empty "heard" list means no same-name claim arrived.
INFO:linux_voice_assistant.wake_arbitration:Wake arbitration: answering at 13.8 dB

# Lost: this device stayed silent, the named peer answered. Arbitration is working.
INFO:linux_voice_assistant.wake_arbitration:Wake arbitration: 192.168.1.32 answers instead (34.4 dB), this device heard 13.8 dB; heard 192.168.1.32 34.4 dB at -55 ms
```

If every device logs `answering at` with an empty `heard` list while more than
one device woke, the devices are claiming different names. Fix the name on
either side so all devices claim the same string (matching is case-insensitive
with collapsed whitespace, so `HAL` and `hal` are the same; `hal` and
`hal v2` are not):

1. **Model manifest `.json` files** — LVA reads each model's `wake_word` entry
   from its `.json` manifest, and Kiosk Satellite reads the same field for
   MicroWakeWord models. Edit the `wake_word` value so it matches the other
   devices.
2. **`.tflite` file names** — Kiosk Satellite OpenWakeWord models ship no
   manifest, so the kiosk derives the phrase from the file name: `hal_v2.tflite`
   becomes "Hal V2" and claims `hal v2`. Rename the file so its stem matches the
   other devices' phrase (a file named `hal.tflite` claims `hal`), upload it to
   the kiosk, and re-select the model.
3. Repeat the wake test: exactly one device should log `answering at` and every
   other device should log `answers instead` (or stay silent if it never
   detected the wake).

See [docs/wake-word-arbitration.md](wake-word-arbitration.md) for the wire
protocol and the window/offset entities.


## More debugging documentation:

See [PiCompose](https://github.com/florian-asche/PiCompose/blob/main/docs/pipewire_debugging.md) for more debugging information.
