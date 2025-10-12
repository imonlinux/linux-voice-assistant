# Linux Voice Assistant

Experimental Linux voice assistant (LVA) for [Home Assistant][homeassistant] that uses the [ESPHome][esphome] protocol/API (via [aioesphomeapi](https://github.com/esphome/aioesphomeapi)).

Runs on Linux `aarch64` and `x86_64` platforms. Tested with Python 3.13 and Python 3.11.
Supports announcments, start/continue conversation, and timers.

See [the tutorial](docs/linux-voice-assistant-2mic-install.md) to build a satellite using a [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/) and a [ReSpeaker 2Mic HAT](https://wiki.keyestudio.com/Ks0314_keyestudio_ReSpeaker_2-Mic_Pi_HAT_V1.0). 

### What's working:
- This fork is from https://github.com/OHF-Voice/linux-voice-assistant Release v1.0.0 which introduces the ablity to use both MicroWakeWord and OpenWakeWord detections models.
- Updated to support LED Events including GPIO based LED controls. Defaults to the ReSpeaker 2Mic Hat SPI leds, but you can use the Grove port GPIO12/13 by adding run config statements.
- Now supports **ALSA, PulseAudio and PipeWire** playback backends using the updated `linux_voice_assistant/mpv_player.py`.
- You can choose between ALSA, PulseAudio, or PipeWire by enabling the matching systemd User Mode service file.
- You can choose between all MWW and OWW wake word within HA after the VLA is registered. Choosen wake words are saved to preferences.json in the linux-voice-assistant folder.
- The volume control is now persistant between connections and reboots. The volume setting gets stored in prefernces.json and loaded when LVA starts.
- Microphone mute button entity added. Changes the LED event to dim red while muted.

## Installation

Install system dependencies (`apt-get`):

* `libportaudio2` (for `sounddevice`)
* `build-essential` (for `pymicro-features`)
* `libmpv-dev` (for `python-mpv`)
* `mpv` (for testing)
* `libmpv-dev` (for building spidev)
  
Clone and install project:

``` sh
git clone https://github.com/OHF-Voice/linux-voice-assistant.git
cd linux-voice-assistant
script/setup
```

## Running

Use `script/run` or `python3 -m linux_voice_assistant`

See `--help` for more options.

## Connecting to Home Assistant

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"


## ToDo:

* Implement echo-cancellation filter in PipeWire. (Improves wake word detection when audio is being played)
* ~~Merge jianyu-li's PR from source project to add mute switch function in this branch~~
* ~~Configure LVA to advertise on Zeroconf/mDNS via Avahi for HA to auto detect (in progress)~~ (Not needed as Release v1.0.0 implemented in code)
* Implement a single LVA systemd unit file that can be addapted using profiles and drop-ins (in progress)
* Implement OWW model validation checks and error handling so that a bad model doesn't crash OWW
* Make the selection of the right ALSA ar PA device more scripted
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
