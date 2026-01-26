# Linux Voice Assistant

Experimental Linux voice assistant (LVA) for [Home Assistant][homeassistant] that uses the [ESPHome][esphome] protocol/API (via [aioesphomeapi](https://github.com/esphome/aioesphomeapi)).

Runs on Linux `aarch64` and `x86_64` platforms. Tested with Python 3.14, 3.13, and 3.11.

**Confirmed working on Rasberry OS (Trixie), Fedora, and  Arch**

Supports announcments, start/continue conversation, and timers.

See [the tutorial](docs/linux-voice-assistant-install.md) to build a satellite using a [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) and a [ReSpeaker 2Mic HAT](https://wiki.keyestudio.com/Ks0314_keyestudio_ReSpeaker_2-Mic_Pi_HAT_V1.0).

The LVA now supports the ReSpeaker XVF3800 4 Mic array via the USB interface. See Section 5 of [the tutorial](docs/linux-voice-assistant-install.md).

Want to run the satellite on a Linux desktop using a simple tray client? See [this tutorial](docs/lva-desktop.md).

How about a Sendspin client? See Section 5 of [the tutorial](docs/linux-voice-assistant-install.md) ***Requires Python 3.12 or higher***.

### What's working:
- **The LVA can now be setup with a fully supported Sendspin client.**
- This fork is from https://github.com/OHF-Voice/linux-voice-assistant Release v1.0.0 which introduces the ability to use both MicroWakeWord and OpenWakeWord detections models.
- Refactor: Sync with Upstream architectural changes, includes the 7 commits since the release of v1.0.0.
- **Core Refactor (Nov 2025):** Major architectural cleanup moving audio logic to a dedicated engine.
- **Added ReSpeaker 2-Mic Button Support:** The GPIO button (17) can be enabled via the config.json file. See section 5 of [the tutorial](docs/linux-voice-assistant-install.md).
- **Changed Alarm to include a Duration Setting:** Added a MQTT control to make the alarm duration adjustable.
- **Performance Optimization:** Optimized audio threading for lower CPU usage when muted and improved responsiveness (reduced latency) when unmuted.
- **Non-Blocking Operations:** Wake word model downloads are now handled in background threads, preventing the device (LEDs/MQTT) from freezing during configuration updates.
- Updated to support LED Events including GPIO based LED controls. Defaults to the ReSpeaker 2Mic Hat SPI leds, but you can use the Grove port GPIO12/13 by updating the config.json file.
- Updated to support running either APA102 or WS2812B LEDs from the SPI interface using a Micro Connectors 40-pin GPIO 1 to 2 Expansion Board. See the tutorial for instructions.
- You can choose between all MWW and OWW wake word within HA after the VLA is registered. Chosen wake words are saved to preferences.json in the linux-voice-assistant folder.
- The volume control is now persistent between connections and reboots. The volume setting gets stored in preferences.json and loaded when LVA starts.
- **Acoustic Echo Cancellation (AEC) using WebRTC.** See section 5 of [the tutorial](docs/linux-voice-assistant-install.md) for instructions on implementation and tuning.
- **openWakeWord models theshold sensitivity can now be overridden by per model.json, globally with CLI flag --wake-word-threshold, or config.json.**

### Add Full MQTT Control for LEDs and Mute
- This fork introduces a comprehensive MQTT integration to bypass limitations in the pinned aioesphomeapi library and provide full remote control over the voice satellite's features and appearance.

- It uses MQTT Discovery to automatically create and configure a device and its associated entities within Home Assistant. This allows for real-time control from the HA interface and enables powerful automations.

#### Key Features:

- A switch entity to mute and unmute the microphone.

- A full suite of select and light entities to customize the effect, color, and brightness for each voice assistant state (Idle, Listening, Thinking, Responding, Error).

- A number entity to configure the number of LEDs in the strip, allowing for use with custom hardware.

- A number entity to configure the duration of the alarm in seconds. A setting of 0 leaves the alarm in (until stopped) mode.

- All settings are persistent, retained by the MQTT broker and re-applied whenever the application restarts.

  See section 5 of [the tutorial](docs/linux-voice-assistant-install.md) to enable MQTT Controls.

## Minimal Installation

Install system dependencies (`apt-get`):

* `libportaudio2` (for `sounddevice`)
* `build-essential` (for `pymicro-features`)
* `libmpv-dev` (for `python-mpv`)
* `mpv` (for testing)
* `libmpv-dev` (for building spidev)
  
Clone and install project:

``` sh
git clone https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
script/setup
```

## Running

Use `script/run` or `python3 -m linux_voice_assistant`

See `linux_voice_assistant/config.json.example` for more options.

## Connecting to Home Assistant if not auto detected

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"


## ToDo:

* ~~Implement the Sendspin client protocol~~
* ~~Implement echo-cancellation filter in PipeWire/PulseAudio.~~ (Taken from upstream and successfully tested)
* ~~Merge jianyu-li's PR from source project to add mute switch function in this branch~~
* ~~Implement MQTT entities to support advanced controls of the LVA.~~
* ~~Configure LVA to advertise on Zeroconf/mDNS via Avahi for HA to auto detect (in progress)~~ (Not needed as Release v1.0.0 implemented in code)
* ~~Implement a single LVA systemd unit file that can be addapted using profiles and drop-ins (in progress)~~
* Implement OWW model validation checks and error handling so that a bad model doesn't crash OWW
* ~~Make the selection of the right ALSA ar PA device more scripted~~
* ~~Make a Docker of the project~~ Already done in parent repo.
* ~~Add sensor entity that displays which wake word engine is being used for HA~~ (Not needed as you can use both at the same time)
* Could this be an DEB package?
* ~~Implement Stop for OWW~~ (Not needed, Stop with MWW works even when using OWW detection models)
* Stretch goal: create a smart installer full of validation and sanity checks
<!-- Links -->
[homeassistant]: https://www.home-assistant.io/
[esphome]: https://esphome.io/

[wyoming]: https://github.com/rhasspy/wyoming-openwakeword/
[future proof home]: https://github.com/FutureProofHomes/wyoming-enhancements/
[aioesphomeapi]: https://github.com/esphome/aioesphomeapi
