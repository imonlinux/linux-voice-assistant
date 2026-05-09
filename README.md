# Linux Voice Assistant

> Forked from [OHF-Voice/linux-voice-assistant][ohf-voice] Release v1.0.0.
> 
> Upstream concepts incorporated since the fork point:
> 
>     - `soundcard` audio library replacing `sounddevice` (upstream alignment)
> 
>     - `pymicro-wakeword` and `pyopen-wakeword` pip packages replacing local wake word code
> 
>     - Timer alarm auto-stop ([upstream PR #261](https://github.com/OHF-Voice/linux-voice-assistant/pull/261)) — extended with runtime HA control
> 
>     - Wake word sensitivity presets ([upstream PR #207](https://github.com/OHF-Voice/linux-voice-assistant/pull/207)) — integrated with fork's per-model threshold system
> 
>     - Mute switch and thinking sound toggle as ESPHome entities (upstream pattern)

A Linux-based voice satellite for [Home Assistant][homeassistant] that speaks the [ESPHome][esphome] protocol via [aioesphomeapi][aioesphomeapi]. It turns any Linux device — from a Raspberry Pi Zero 2 W to a full desktop — into a capable voice assistant with wake word detection, speech-to-text, TTS playback, timers, LED feedback, and optional multiroom audio via Sendspin.

Runs on `aarch64` and `x86_64`

Tested with Python 3.11, 3.13, and 3.14 on Raspberry Pi OS (Trixie), Fedora, Arch, and Nobara.

See [the tutorial](docs/linux-voice-assistant-install.md) for complete instructions to install LVA.

---

## Features

### Voice Assistant Core

- **Dual wake word engines** — MicroWakeWord and OpenWakeWord models can run simultaneously. Wake words are selectable from the Home Assistant UI and persisted across reboots.
- **Wake word sensitivity** — Adjustable detection sensitivity (Slightly/Moderately/Very sensitive) controllable from the Home Assistant device page. Per-model OpenWakeWord thresholds from `.json` files take precedence over the global preset. (More details in the Wake Word Models section below)
- **Conversational flow** — Supports announcements, start/continue conversation, and timers with configurable alarm duration and auto-stop.
- **Configurable event sounds** — Wakeup, thinking, and timer sounds selectable from the Home Assistant device page, with a master toggle (`Event Sounds`). Thinking sound supports optional looping. Timer alarm is a functional alert and always plays regardless of the toggle.
- **Acoustic Echo Cancellation** — WebRTC-based AEC via PipeWire filter chains for clean wake word detection during TTS playback.
- **Stop word** — A dedicated MicroWakeWord model can interrupt TTS playback or silence a ringing timer alarm.
- **Alarm Duration** — Set the time in seconds for the alarm to play (0 = play until interrupted by the Stop wake word). Configurable from the Home Assistant device page.

### MQTT Device Controls

When MQTT is enabled, *(See Section 5 of [the tutorial](docs/linux-voice-assistant-install.md))* LVA publishes a full device via MQTT Discovery with the following entities:

| Entity | Type | Description |
| --- | --- | --- |
| LED Count | `number` | Set the number of addressable LEDs |
| LED \<State\> Effect | `select` | Choose an LED animation per voice state |
| LED \<State\> Color | `light` | Set color and brightness per voice state |

*LED states: Idle, Listening, Thinking, Responding, Error. Available effects: Off, Solid, Slow/Medium/Fast Pulse, Slow/Medium/Fast Blink, Spin*

> **Note:** Mute, sound selection, thinking sound loop, alarm duration, event sounds, and wake word sensitivity are now controlled via the ESPHome device page in Home Assistant — no MQTT required. MQTT is only needed for LED controls. The tray client continues to use MQTT for mute state mirroring but the entity is not published in HA.

<img width="515" height="1033" alt="image" src="https://github.com/user-attachments/assets/cfc9e462-b301-4323-a3d8-5bab0322a548" />


### Hardware Integrations *(See Section 5 of [the tutorial](docs/linux-voice-assistant-install.md))*

- **ReSpeaker 2-Mic Pi HAT v1 or v2** — GPIO button (mute toggle, short/long press) and SPI LEDs
- **ReSpeaker XVF3800 4-Mic USB Array** — Hardware mute button, red mute LED sync, USB LED ring, and 4-mic input with AEC support. No vendor binaries required — LVA communicates directly via USB control transfers.

### LED Support

- **DotStar (APA102)** — SPI or GPIO interface
- **NeoPixel (WS2812B)** — SPI or GPIO interface *(Experimental)*
- **ReSpeaker XVF3800** — USB LED ring with 12 addressable LEDs
- Per-state effect, color, and brightness control from Home Assistant

### Sendspin Client (Music Assistant) *(See Section 5 of [the tutorial](docs/linux-voice-assistant-install.md))*

The optional Sendspin client turns LVA into a multiroom audio player for [Music Assistant][music-assistant]. The LVA automatically appears as a player in Music Assistant using the device name.

- **Codec support** — PCM, FLAC (via ffmpeg), and Opus (via opuslib or ffmpeg)
- **Clock-synchronized playback** — Kalman filter clock sync with configurable target latency and late-drop policy for tight multiroom alignment
- **Transport controls** — Play, pause, stop, volume, and mute from Music Assistant
- **Voice coordination** — Automatic audio ducking during voice interactions
- **Tunable timing** — `output_latency_ms`, `sync_target_latency_ms`, and `sync_late_drop_ms` for per-device calibration

#### *Requires Python 3.12+ and the `--sendspin` install extra.*

### Desktop Tray Client *(See [this tutorial](docs/lva-desktop.md))*

An optional PyQt5 system tray application for Linux desktops that mirrors the LVA's state via MQTT:

- Visual state indicator with LED color mirroring
- Mute toggle from the tray menu
- Start, stop, and restart the LVA systemd service

*Requires the `--tray` install extra.*

### Stable Device Identity

LVA persists its MAC address to `preferences.json` on first boot. This ensures the device identity in Home Assistant survives NIC changes, VM re-provisioning, or NetworkManager MAC randomization. To reset identity, remove the `mac_address` field from `preferences.json`.

### Persistent Settings

Volume, wake word selection, LED count, alarm duration, sound selections, and Sendspin volume are all persisted to `preferences.json` and restored on startup.

---

## Quick Start (Minimal System)

### System Dependencies

```bash
sudo apt-get install libportaudio2 build-essential libmpv-dev mpv
```

### Install

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
script/setup
```

Optional extras (additive):

```bash
script/setup --tray        # Desktop tray client (PyQt5)
script/setup --sendspin    # Sendspin / Music Assistant support
script/setup --dev         # Development tools
```

### Configure

Copy and edit the example configuration:

```bash
nano ~/linux_voice_assistant/config.json
```

*At minimum, set the `app.name` field. See [`config.json.example`](linux_voice_assistant/config.json.example) for all available options with inline documentation.*

### Run

```bash
script/run
```

Or directly:

```bash
python3 -m linux_voice_assistant
```

### Connect to Home Assistant

LVA advertises itself via mDNS/Zeroconf and should be auto-discovered. If not:

1. Go to **Settings → Devices & Services** in Home Assistant
2. Click **Add Integration** → **ESPHome** → **Set up another instance**
3. Enter the IP address of your LVA device with port `6053`
4. During registration, use the wake word shown on the registration page (default: "OK Nabu")

### Run as a Service

```bash
# Copy and edit the service file (adjust paths/username as needed)
mkdir -p ~/.config/systemd/user/
cp service/linux-voice-assistant.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant.service
```

Verify:

```bash
journalctl --user -u linux-voice-assistant.service -f
```

---

## Tutorials

| Guide | Description |
| --- | --- |
| [Full Install Guide](docs/linux-voice-assistant-install.md) | Complete setup including AEC, MQTT, LEDs, Sendspin, and XVF3800 |
| [2-Mic HAT Quick Start](docs/linux-voice-assistant-2mic-install.md) | Raspberry Pi + ReSpeaker 2-Mic HAT focused guide |
| [XVF3800 Setup](docs/linux-voice-assistant-xvf3800.md) | ReSpeaker XVF3800 4-Mic USB Array configuration |
| [Desktop Client](docs/lva-desktop.md) | Running LVA on a Linux desktop with the tray client |
| [PipeWire Install](docs/install_pipewire.md) | PipeWire setup notes |
| [PulseAudio Install](docs/install_pulseaudio.md) | PulseAudio setup notes |

---

## Configuration Reference

LVA is configured via `config.json`. The file is organized into sections:

| Section | Purpose |
| --- | --- |
| `app` | Device name, sound file paths, event sounds toggle, preferences file |
| `audio` | Input/output device selection, volume sync, max volume percent |
| `wake_word` | Model directories, default model, stop model, detection threshold |
| `esphome` | API server host and port |
| `led` | LED type (dotstar/neopixel/xvf3800), interface, GPIO pins, count |
| `mqtt` | Broker connection (host, port, credentials) |
| `button` | Hardware button mode (gpio/xvf3800), pin, press timing |
| `sendspin` | Sendspin client connection, player tuning, codec preferences |

*See [`config.json.example`](linux_voice_assistant/config.json.example) for the complete reference with inline documentation.*

---

## Wake Word Models

Built-in models (in `wakewords/`):

Community openWakeword models from [home-assistant-wakewords-collection][wakewords-collection] can be added by placing the `.tflite` and corresponding `.json` file in `wakewords/openWakeWord/`.

> **Wake word detection threshold is configurable via the Home Assistant ESPHome entity (MWW and OWW), globally via `config.json` `wake_word.openwakeword_threshold` (OWW only), or per-model via the model's `.json` file (OWW only). The ESPHome entity applies sensitivity presets that adjust all models simultaneously. Per-model OWW thresholds from `.json` files take precedence over both the ESPHome preset and the global `config.json` value.**

Example file:
`wakewords/openWakeWord/ok_nabu_v0.1.json`

```bash
{
  "type": "openWakeWord",
  "wake_word": "Okay Nabu",
  "model": "ok_nabu_v0.1.tflite",
  "threshold": 0.62
}
```

---

## Project Structure

```
linux-voice-assistant/
├── docs                                        # Installation and setup guides
│   ├── install_pipewire.md                        # PipeWire setup notes
│   ├── install_pulseaudio.md                    # PulseAudio setup notes
│   ├── linux-voice-assistant-2mic-install.md    # Raspberry Pi + ReSpeaker 2-Mic HAT focused guide
│   ├── linux-voice-assistant-install.md        # Complete setup including AEC, MQTT, LEDs, Sendspin, and XVF3800
│   ├── linux-voice-assistant-xvf3800.md        # ReSpeaker XVF3800 4-Mic USB Array configuration
│   ├── linux-voice-assistant-xvf3800-mute.md    # Hardware mute button and LED sync details
│   ├── lva-desktop.md                            # Running LVA on a Linux desktop with the tray client
│   └── xvf3800_legacy_led_effects_mapping.md    # LED functions when running firmware older than 2.0.7
├── linux_voice_assistant
│   ├── api_server.py                            # ESPHome API server
│   ├── audio_engine.py                            # Mic capture and wake word detection
│   ├── audio_volume.py                            # OS volume control (wpctl/pactl/amixer)
│   ├── button_controller.py                    # GPIO button handler
│   ├── config.json                                # LVA configuration file
│   ├── config.json.example                        # Annotated configuration reference
│   ├── config.py                                # Configuration dataclasses
│   ├── entity.py                                # ESPHome entity classes (media player, mute, sounds, sensitivity, alarm duration)
│   ├── event_bus.py                            # Publish/subscribe event system
│   ├── __init__.py
│   ├── led_controller.py                        # LED effects and state mapping
│   ├── __main__.py                                # Application entry point
│   ├── models.py                                # Shared state and data models
│   ├── mpv_player.py                            # Media playback via mpv
│   ├── mqtt_controller.py                        # MQTT discovery and entity management
│   ├── satellite.py                            # ESPHome voice assistant protocol
│   ├── sendspin                                # Sendspin client subsystem
│   │   ├── client.py                            # WebSocket connection and protocol
│   │   ├── clock_sync.py                        # Kalman filter time synchronization
│   │   ├── controller.py                        # EventBus handlers for ducking/commands
│   │   ├── discovery.py                        # mDNS server discovery
│   │   ├── __init__.py
│   │   ├── models.py                            # Sendspin internal state
│   │   └── player.py                            # PCM sink and decoder pipeline
│   ├── tray_client                                # Desktop tray client
│   │   ├── client.py                            # PyQt5 system tray application
│   │   ├── __init__.py
│   │   └── __main__.py                            # Tray client entry point
│   ├── util.py                                    # MAC address, slugify, helpers
│   ├── xvf3800_button_controller.py            # XVF3800 USB mute integration
│   ├── xvf3800_led_backend.py                    # XVF3800 USB LED ring driver
│   └── zeroconf.py                                # mDNS discovery advertisement
├── mypy.ini
├── pylintrc
├── pyproject.toml
├── README.md
├── respeaker2mic                                # reSpeaker 2mic hat driver installers
│   └── install-respeaker-drivers.sh            # verion 1.0 hardware driver installer
├── script
│   ├── format
│   ├── lint
│   ├── run
│   ├── setup
│   ├── test
│   └── tray
├── service                                        # systemd unit files
│   ├── aec-module-load.service                    # Audio Echo Cancellation unit file
│   ├── linux-voice-assistant.service            # LVA unit file
│   ├── linux-voice-assistant-tray.service        # Tray Client unit file
│   └── linux-voice-assistant_xvf3800.service    # LVA unit file with pipewire depends
├── setup.cfg
├── sounds
│   ├── LICENSE.md
│   ├── thinking                                # Thinking state sounds
│   │   ├── nothing.flac
│   │   ├── processing.flac
│   │   ├── thinking_modem.flac
│   │   ├── thinking_music_2.flac
│   │   ├── thinking_music_3.flac
│   │   └── thinking_music.flac
│   ├── timer                                    # Timer alarm sounds
│   │   └── timer_finished.flac
│   └── wakeup                                    # Wake word triggered sounds
│       └── wake_word_triggered.flac
├── tests
│   ├── lva_mic_capture.py
│   ├── ok_nabu.wav
│   ├── test_microwakeword.py
│   ├── test_openwakeword.py
│   ├── xvf3800_hid_mute_probe.py
│   └── xvf3800_probe.py
├── wakewords                                    # Wake word models
│   ├── alexa.json
│   ├── alexa.tflite
│   ├── choo_choo_homie.json
│   ├── choo_choo_homie.tflite
│   ├── hey_home_assistant.json
│   ├── hey_home_assistant.tflite
│   ├── hey_jarvis.json
│   ├── hey_jarvis.tflite
│   ├── hey_luna.json
│   ├── hey_luna.tflite
│   ├── hey_mycroft.json
│   ├── hey_mycroft.tflite
│   ├── okay_computer.json
│   ├── okay_computer.tflite
│   ├── okay_nabu.json
│   ├── okay_nabu.tflite
│   ├── openWakeWord
│   │   ├── alexa_v0.1.json
│   │   ├── alexa_v0.1.tflite
│   │   ├── computer_v2.json
│   │   ├── computer_v2.tflite
│   │   ├── hal_v2.json
│   │   ├── hal_v2.tflite
│   │   ├── hey_jarvis_v0.1.json
│   │   ├── hey_jarvis_v0.1.tflite
│   │   ├── hey_Marvin.json
│   │   ├── hey_Marvin.tflite
│   │   ├── hey_mycroft_v0.1.json
│   │   ├── hey_mycroft_v0.1.tflite
│   │   ├── hey_nabu_v2.json
│   │   ├── hey_nabu_v2.tflite
│   │   ├── hey_rhasspy_v0.1.json
│   │   ├── hey_rhasspy_v0.1.tflite
│   │   ├── jarvis_v2.json
│   │   ├── jarvis_v2.tflite
│   │   ├── marvin_v2.json
│   │   ├── marvin_v2.tflite
│   │   ├── ok_jarvis.json
│   │   ├── ok_jarvis.tflite
│   │   ├── ok_nabu_v0.1.json
│   │   └── ok_nabu_v0.1.tflite
│   ├── stop.json
│   └── stop.tflite
└── XVF3800
    └── 99-respeaker-xvf3800.rules                # XVF3800 USB permissions and disable power suspend UDEV rule
```

---

## Development & Testing

### Running Tests

The project includes a comprehensive test suite covering the fork's new architecture:

```bash
# Install development dependencies
./script/setup --dev

# Run all tests
./script/test

# Run specific test file
./script/test test_event_bus.py

# Run with coverage report
pytest tests/ --cov=linux_voice_assistant --cov-report=html
```

### Test Structure

- **Unit Tests**: Core architecture (EventBus, State, Configuration)
- **Integration Tests**: Controllers and hardware abstractions
- **Hardware Tests**: Physical device integration (XVF3800, ReSpeaker)
- **End-to-End Tests**: Complete voice assistant workflows

See [Testing Guide](docs/testing-guide.md) for detailed testing documentation and [tests/README.md](tests/README.md) for test-specific information.

### Code Quality

```bash
# Format code
black linux_voice_assistant/ tests/

# Lint code
flake8 linux_voice_assistant/ tests/

# Type checking
mypy linux_voice_assistant/
```

---

## License

Licensed under the [Apache License 2.0](LICENSE.md).

---

<!-- Links -->

[homeassistant]: https://www.home-assistant.io/
[esphome]: https://esphome.io/
[aioesphomeapi]: https://github.com/esphome/aioesphomeapi
[ohf-voice]: https://github.com/OHF-Voice/linux-voice-assistant
[music-assistant]: https://music-assistant.io/
[wakewords-collection]: https://github.com/fwartner/home-assistant-wakewords-collection
