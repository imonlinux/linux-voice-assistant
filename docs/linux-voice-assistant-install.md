Linux Voice Assistant on Raspberry Pi — Installation & Configuration Guide

> Created using ChatGPT 5 with the following prompt:

```html
      Using the following github document as a guide
      https://github.com/rhasspy/wyoming-satellite/blob/master/docs/tutorial_2mic.md,
      take the attached bash history of commands and create a similar document
      detailing the installation and configuration of this linux-voice-assistant project.
```

> Modeled after the Wyoming Satellite two‑mic tutorial, adapted from actual shell history.

This guide reproduces a working setup of the **linux-voice-assistant** project with **Wyoming OpenWakeWord** and **MicroWakeWord** on a Raspberry PI. It assumes a fresh system with sudo access and the default "pi" user. This guide now assumes a **PipeWire-Pulse** (recommended) or **PulseAudio** userspace audio stack (ALSA-only is no longer sufficient with the current audio backend). 

*See Section 10  of this document for LVA upgrade instructions.*

## Prerequisites

- Raspberry Pi OS Lite (64-bit) (Bookworm or Trixie)
- Default Python 3.11+ recommended
- A microphone and speaker (see the options for reSpeaker devices in section 5)
- Network access to your Home Assistant instance

## 1. Install system packages

```bash
sudo apt update
sudo apt upgrade
sudo apt install build-essential git \
      libmpv-dev mpv python3-dev python3-venv
sudo reboot
```

## 2. (Optional) ReSpeaker 2‑Mic HAT drivers or ReSpeaker XVF3800 support

Instructions for the install (or re-install) of the ReSpeaker 2-Mic Hat or the ReSpeaker XVF3800 USB 4-Mic Array have been moved to Section 5.

## 3. Get the code

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
```

## 4. Setup Linux Voice Assistant (LVA)

```bash
cd ~/linux-voice-assistant/
script/setup
```

## 5. Choose your audio stack + install the LVA service ("Choose your Adventure!")

Pick **one** of the following install paths. Expand a section to see the exact steps.

> **Recommended:** PipeWire (PipeWire-Pulse).
> 
> **Note:** With the current audio backend (`soundcard`), you must run **PipeWire-Pulse** or **PulseAudio**. ALSA-only is not supported.

> Tip: All services run in *user* mode (requires `loginctl enable-linger`);

<details>
<summary><strong>Optional (ReSpeaker 2‑Mic HAT drivers)</strong></summary>

If you are using the **ReSpeaker 2‑Mic HAT** (seeed-2mic-voicecard), install the vendor driver + overlay using the project helper script:
*Instructions to reinstall after a kernel upgrade below*
```bash
chmod +x ~/linux-voice-assistant/respeaker2mic/install-respeaker-drivers.sh
sudo ~/linux-voice-assistant/respeaker2mic/install-respeaker-drivers.sh
sudo reboot
```

After reboot, you can sanity-check the device is present:

```bash
arecord -l
aplay -l
```

**Optional (GPIO Button)**

Add support for the ReSpeaker 2-Mic HAT momentary button as a first-class control surface for the Linux Voice Assistant. The button now behaves like:

***Short press***

If TTS or music is playing → stop playback (equivalent to the Stop wake word)

Otherwise → start a new conversation (equivalent to a wake word trigger)

***Long press***

Toggle microphone mute (wired through the existing set_mic_mute event, so MQTT state and LEDs stay in sync)

The implementation uses a polling-based GPIO loop (RPi.GPIO) instead of kernel edge-detection to avoid “Failed to add edge detection” issues on some HAT/overlay setups. Button behavior is fully configurable via config.json.

**Requires a compatible GPIO board** This has been tested on the ReSpeaker 2-Mic Pi Hat:

**Edit the LVA config.json file:**

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

**Enable the GPIO button support:**

```bash
"button": {
  "enabled": true,
  "pin": 17,
  "long_press_seconds": 1.0
  }
```

**Example (LVA config.json file with MQTT, Grove Port, and GPIO Button enabled** ***Note: The GPIO button can be changed from the default (17) on the 2-Mic hat.***

```bash
{
  "app": {
    "name": "Linux Voice Assistant"
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  },
  "led": {
    "enabled": true,
    "led_type": "dotstar",
    "interface": "gpio",
    "clock_pin": 13,
    "data_pin": 12,
    "num_leds": 10
  },
  "button": {
  "enabled": true,
  "pin": 17,
  "long_press_seconds": 1.0
  }
}
```

**Enable & start:**

```bash
systemctl --user restart linux-voice-assistant.service
```

**Verify:**

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```
### Reinstall the reSpeaker 2Mic driver after a kernel upgrade (only if the upgrade breakes the driver)
Remove the existing DKMS entries for the driver:
```bash
sudo rm -rf /var/lib/dkms/seeed-voicecard/0.3
sudo rm -rf /usr/src/seeed-voicecard-0.3
```
Reinstall the driver:
```bash
sudo ./install-respeaker-drivers.sh 
sudo reboot
```

</details>

<details>
<summary><strong>Optional (ReSpeaker XVF3800 USB 4‑Mic Array)</strong></summary>

## Recommended Configuration

- To use the advanced LED effects, upgrade to the latest XVF3800 firmware (respeaker_xvf3800_usb_dfu_firmware_v2.0.7.bin as of this writing). 
  -- https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/blob/master/xmos_firmwares/dfu_guide.md
- Tested and works on a Raspberry Pi Zero 2W, but I don't recommend attempting to flash the firmware with this device.
- PipeWire (or PulseAudio) installed and tested.

If you are only using the Mic and or Speaker you do not need to do anything further. The LVA should load them automatically. If there is an issue, make sure that the "audio" section either omits the input_device and output_device or that they match the example config.json below.

If you are using the **ReSpeaker XVF3800 USB 4‑Mic Array** LEDs and Mute Button

Edit the config.json file:

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

Add or update the following sections (Recommended settings):

> Tip: Be sure to change the name if you have more than one LVA!

```json
{
  "app": {
    "name": "Linux Voice Assistant"
  },
  "audio": {
    "input_device": "reSpeaker XVF3800 4-Mic Array Analog Stereo",
    "input_block_size": 1024,
    "output_device": "pipewire/alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_114993701254500222-00.analog-stereo"
  },
  "led": {
    "enabled": true,
    "led_type": "xvf3800",
    "interface": "usb"
  },
  "button": {
    "enabled": true,
    "mode": "xvf3800",
    "poll_interval_seconds": 0.15
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_server",
    "password": "mqtt_password"
  }
}
```

> Take a look at ~/linux-voice-assistant/linux_voice_assistant/config.json.example for details on these settings as well as all available options.

Install these additional debian packages

```bash
sudo apt install \
    libusb-1.0-0 dbus-user-session
```

Install the udev rule shipped with the project so LVA can access the USB controls (LED ring, mute state, etc.).

```bash
# Copy the provided udev rule into place
sudo cp ~/linux-voice-assistant/XVF3800/99-respeaker-xvf3800.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
# Unplug/replug the XVF3800 (or reboot)
```

Add user to the plugdev group for access to the XVF3800 and reboot

> Tip: If you use a different username than `pi`, adjust user accordingly.

```bash
sudo usermod -aG plugdev pi
sudo reboot
```

</details>

<details>
<summary><strong>PipeWire (recommended; user-mode services)</strong></summary>

**Prep (PipeWire):** Follow the PipeWire tutorial first: [the tutorial](install_pipewire.md).

**Enable linger (required for user services to start after reboot):**

```bash
sudo loginctl enable-linger pi
```

**Install LVA user-mode services:**

```bash
mkdir -p ~/.config/systemd/user
```

```bash
cp ~/linux-voice-assistant/service/linux-voice-assistant.service    ~/.config/systemd/user/linux-voice-assistant.service
```

**Enable & start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant.service
```

**Verify:**

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

</details>

<details>
<summary><strong>PulseAudio (user-mode services)</strong></summary>

**Prep (PulseAudio):** Follow the PulseAudio tutorial first: [the tutorial](install_pulseaudio.md).

**Enable linger (required for user services to start after reboot):**

```bash
sudo loginctl enable-linger pi
```

**Install LVA user-mode services:**

```bash
mkdir -p ~/.config/systemd/user
```

```bash
cp ~/linux-voice-assistant/service/linux-voice-assistant.service   ~/.config/systemd/user/linux-voice-assistant.service
```

**Enable & start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant.service
```

**Verify:**

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```
</details>

<details>
<summary><strong>Optional (MQTT Controls)</strong></summary>

## 🔌 MQTT Controls Overview

The Linux Voice Assistant (LVA) creates several MQTT entities under the Home Assistant MQTT Discovery protocol. Each entity is prefixed by your LVA's unique device ID defined with the --name field in the LVA User-Mode Serviced Unit (e.g., `linux_voice_assistant`).

---

### 🎤 Microphone Mute (`switch` entity)

Controls the microphone mute state.

- **MQTT Discovery Topic:** `homeassistant/switch/<device_id>_mute/config`
- **Name:** `[LVA Name] Mute Microphone`
- **Icon:** `mdi:microphone-off`
- **Functionality:** Toggles the microphone mute. When muted, the LEDs will display a dim red `solid` effect.

---

## 💡 LED Control for Linux Voice Assistant

These are MQTT entities created by the Linux Voice Assistant for controlling its integrated LEDs (e.g., DotStar, NeoPixel). These entities integrate seamlessly with Home Assistant via MQTT Discovery, allowing you to manage LED effects, colors, brightness, and the number of connected LEDs directly from your Home Assistant interface.

---

### 🔢 Number of LEDs (`number` entity)

This entity allows you to specify the physical number of addressable LEDs connected to your device.

- **MQTT Discovery Topic:** `homeassistant/number/<device_id>_num_leds/config`
- **Name:** `[LVA Name] Number of LEDs`
- **Functionality:** Sets the total number of LEDs.
- **Important:** This setting requires a **restart of the LVA service file** to take effect, as the LED hardware driver needs to be re-initialized. The value is persisted in `preferences.json`.

---

### ✨ State-Based LED Controls (`select` and `light` entities)

For each distinct LVA state (Idle, Listening, Thinking, Responding, Error), a `select` entity for choosing an effect and a `light` entity for controlling color and brightness are created.

- **States:**
  - `idle` (e.g., `[LVA Name] Idle Effect`, `[LVA Name] Idle Color`)
  - `listening` (e.g., `[LVA Name] Listening Effect`, `[LVA Name] Listening Color`)
  - `thinking` (e.g., `[LVA Name] Thinking Effect`, `[LVA Name] Thinking Color`)
  - `responding` (e.g., `[LVA Name] Responding Effect`, `[LVA Name] Responding Color`)
  - `error` (e.g., `[LVA Name] Error Effect`, `[LVA Name] Error Color`)

#### `select` Entities (Effect Selector)

- **MQTT Discovery Topic:** `homeassistant/select/<device_id>_<state_name>_effect/config`
- **Name:** `[LVA Name] [State Name] Effect`
- **Icon:** `mdi:palette-swatch-variant`
- **Functionality:** Allows selection of an animation/effect for the specific LVA state.

#### `light` Entities (Color & Brightness Control)

- **MQTT Discovery Topic:** `homeassistant/light/<device_id>_<state_name>_color/config`
- **Name:** `[LVA Name] [State Name] Color`
- **Functionality:** Controls the color and brightness for the specific LVA state when the selected effect uses color. Supports RGB color mode and brightness.

---

## 🎨 Available LED Effects

The following effects can be selected via the `[State Name] Effect` (select) entities:

| Effect Name | Description |
| --- | --- |
| **Off** | All LEDs are turned off. |
| **Solid** | All LEDs display a single, constant color. |
| **Slow Pulse** | LEDs slowly fade in and out. |
| **Medium Pulse** | LEDs fade in and out at a moderate speed. |
| **Fast Pulse** | LEDs rapidly fade in and out. |
| **Slow Blink** | LEDs turn on and off slowly. |
| **Medium Blink** | LEDs turn on and off at a moderate speed. |
| **Fast Blink** | LEDs rapidly turn on and off. |
| **Spin** | A single LED "spins" around the strip. |

**Edit LVA config.json file:**

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

**Add MQTT configuration entries to LVA config.json file:**

```bash
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  }
```

**Example of complete config.json file**
***Note: Change the MQTT values to match your system!***

```bash
{
  "app": {
    "name": "Linux Voice Assistant"
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  }
}
```

**Enable & start:**

```bash
systemctl --user restart linux-voice-assistant.service
```

**Verify:**

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

</details>

<details>
<summary><strong>Optional (Grove Port LEDs)</strong></summary>

This optional configuration support the use of the ReSpeaker 2Mic Grove Port with APA102 LEDs.

| Grove Pigtail | Function (on ReSpeaker Hat) | Solder to LED Strip |
| --- | --- | --- |
| ⚫ **Black Wire** | Ground (GND) | **GND** (Ground) |
| 🔴 **Red Wire** | Power (VCC) | **VCC / 5V** (Power) |
| 🟡 **Yellow Wire** | GPIO12 (Signal 1) | **DI** (Data Input) |
| ⚪ **White Wire** | GPIO13 (Signal 2) | **CI** (Clock Input) |

**Edit LVA config.json file:**

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

**Add Grove (GPIO) configuration entries to LVA config.json file:**

```bash
  "led": {
    "enabled": true,
    "led_type": "dotstar",
    "interface": "gpio",
    "clock_pin": 13,
    "data_pin": 12,
    "num_leds": 10
  }
```

**Example (LVA config.json file with MQTT and Grove Port enabled)**
***Note: Change the GPIO values to match your system!***

```bash
{
  "app": {
    "name": "Linux Voice Assistant"
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  },
  "led": {
    "enabled": true,
    "led_type": "dotstar",
    "interface": "gpio",
    "clock_pin": 13,
    "data_pin": 12,
    "num_leds": 10
  }
}
```

**Enable & start:**

```bash
systemctl --user restart linux-voice-assistant.service
```

**Verify:**

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

</details>

<details>
<summary><strong>Optional (Acoustic Echo Cancellation) </strong></summary>

This optional configuration support the use AEC and require either a working PipeWire-Pulse or PulseAudio backend.

Enable the echo cancel PulseAudio module:

```bash
pactl load-module module-echo-cancel \
  aec_method=webrtc \
  aec_args="analog_gain_control=1 digital_gain_control=1 noise_suppression=1"
```

If successfully loaded you will see an ID presented after the command. Keep this ID as you may need it to "tune" the AEC.

```bash
pactl load-module module-echo-cancel \
  aec_method=webrtc \
  aec_args="analog_gain_control=1 digital_gain_control=1 noise_suppression=1"
536870916
```

Determine the names of the echo cancellation audio devices. On my system it is "Echo-Cancel Source" and "pipewire/echo-cancel-sink":

```bash
~/linux-voice-assistant/script/run --list-input-devices
Input devices
=============
[0] Built-in Audio Stereo
[1] Echo-Cancel Source
```

```bash
~/linux-voice-assistant/script/run --list-output-devices
Output devices
==============
auto: Autoselect device
pipewire: Default (pipewire)
pipewire/alsa_output.platform-soc_sound.stereo-fallback: Built-in Audio Stereo
pipewire/echo-cancel-sink: Echo-Cancel Sink
pulse/alsa_output.platform-soc_sound.stereo-fallback: Built-in Audio Stereo
pulse/echo-cancel-sink: Echo-Cancel Sink
alsa: Default (alsa)
alsa/sysdefault: Default Audio Device
alsa/lavrate: Rate Converter Plugin Using Libav/FFmpeg Library
alsa/samplerate: Rate Converter Plugin Using Samplerate Library
alsa/speexrate: Rate Converter Plugin Using Speex Resampler
alsa/jack: JACK Audio Connection Kit
alsa/oss: Open Sound System
alsa/pipewire: PipeWire Sound Server
alsa/speex: Plugin using Speex DSP (resample, agc, denoise, echo, dereverb)
alsa/upmix: Plugin for channel upmix (4,6,8)
alsa/vdownmix: Plugin for channel downmix (stereo) with a simple spacialization
alsa/playback: playback
alsa/capture: capture
alsa/dmixed: dmixed
alsa/array: array
alsa/plughw:CARD=vc4hdmi,DEV=0: vc4-hdmi, MAI PCM i2s-hifi-0/Hardware device with all software conversions
alsa/sysdefault:CARD=vc4hdmi: vc4-hdmi, MAI PCM i2s-hifi-0/Default Audio Device
alsa/hdmi:CARD=vc4hdmi,DEV=0: vc4-hdmi, MAI PCM i2s-hifi-0/HDMI Audio Output
alsa/dmix:CARD=vc4hdmi,DEV=0: vc4-hdmi, MAI PCM i2s-hifi-0/Direct sample mixing device
alsa/usbstream:CARD=vc4hdmi: vc4-hdmi/USB Stream Output
alsa/plughw:CARD=seeed2micvoicec,DEV=0: seeed-2mic-voicecard, bcm2835-i2s-wm8960-hifi wm8960-hifi-0/Hardware device with all software conversions
alsa/sysdefault:CARD=seeed2micvoicec: seeed-2mic-voicecard, bcm2835-i2s-wm8960-hifi wm8960-hifi-0/Default Audio Device
alsa/dmix:CARD=seeed2micvoicec,DEV=0: seeed-2mic-voicecard, bcm2835-i2s-wm8960-hifi wm8960-hifi-0/Direct sample mixing device
alsa/usbstream:CARD=seeed2micvoicec: seeed-2mic-voicecard/USB Stream Output
jack: Default (jack)
sdl: Default (sdl)
```

**Edit LVA config.json file:**

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

**Add AEC configuration entries to LVA config.json file:**

```bash
  "audio": {
    "input_device": "Echo-Cancel Source",
    "input_block_size": 1024,
    "output_device": "pipewire/echo-cancel-sink"
  }
```

**Example (LVA config.json file with MQTT, Grove Port, and AEC enabled)**
***Note: Change the source and sink values to match your system!***

```bash
{
  "app": {
    "name": "Linux Voice Assistant"
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  },
  "led": {
    "enabled": true,
    "led_type": "dotstar",
    "interface": "gpio",
    "clock_pin": 13,
    "data_pin": 12,
    "num_leds": 10
  },
  "audio": {
    "input_device": "Echo-Cancel Source",
    "input_block_size": 1024,
    "output_device": "pipewire/echo-cancel-sink"
  }
}
```

**AEP Tuning Options (aec_args)**

In order to modify the echo cancellation device setting, you must first remove the previously installed modules.

Remove AEP module (use the module ID listed when AEP module was loaded):

```bash
pactl unload-module 536870916
```

AEP tuning settings:

| Setting | Allowed Values | Typical Value | What It Does | When To Change |
| --- | --- | --- | --- | --- |
| `analog_gain_control` | `0` or `1` | `0` | Lets WebRTC AEC “ride” the hardware/analog mic gain. | Leave `0` when you already tuned mic gain in ALSA/Pulse. Use `1` only if your mic is too quiet and you want auto-leveling at the expense of some consistency. |
| `digital_gain_control` | `0` or `1` | `1` | Software AGC on the captured signal (after the ADC). | Keep `1` for voice assistants so wake-word and STT get a stable level. Turn `0` if you already run separate AGC or notice pumping/breathing. |
| `noise_suppression` | `0` or `1` | `1` | Enables WebRTC noise reduction on the mic signal. | Keep `1` in most cases (fans, room noise). Try `0` if audio sounds “underwater” or dull and your environment is already very quiet. |
| `extended_filter`* | `0` or `1` | `1` (often) | Uses a more robust AEC filter that handles tricky echo paths / long delays. | Use `1` for speaker-in-room setups (like LVA) unless CPU is extremely constrained. |
| `delay_agnostic`* | `0` or `1` | `1` (often) | Makes AEC less sensitive to exact playback/capture latency. | Keep `1` if devices/paths change or Bluetooth is involved. Set `0` only if you know latency is rock-stable and want to shave a bit of CPU. |
| `drift_compensation`* | `0` or `1` | `1` (often) | Compensates for clock drift between capture and playback devices. | Use `1` if mic and speakers are on different hardware (USB mic + HDMI/Bluetooth out). `0` is OK when both share the same clock (onboard codec only). |
| `voice_detection`* | `0` or `1` | `0` or `1` | Simple VAD that can help AEC and noise suppression focus on speech segments. | Try `1` if you see good wake-word hits but noisy STT. Use `0` if it seems to cut off very quiet speech or initial phonemes. |

**Enable & start:**

```bash
systemctl --user restart linux-voice-assistant.service
```

**Verify:**

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

</details>

<details>
<summary><strong>Optional (Sendspin client for Music Assistant)</strong></summary>

This optional configuration enables the **Sendspin** client inside LVA so Music Assistant can stream audio to the device and control it (play/pause/stop, volume, mute, etc.). The Sendspin client will automatically show up in Music Assistant with the name of the LVA.

**Requirements:**

- ***Requires Python 3.12 or higher*** Recommend RaspberryPI OS (Trixie)
- Music Assistant is running a Sendspin server on your network.
- LVA is installed with a working **PipeWire-Pulse** (recommended) or **PulseAudio** stack (see Section 5 above).
- FFMPEG for the FLAC codec if used.

***Confirm FFMPEG is installed***

```bash
sudo apt-get install ffmpeg
```

***Setup LVA to implement the Sendspin Client***

```bash
cd ~/linux-voice-assistant
script/setup --sendspin
```

***Edit LVA config.json file:***

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

***Add a Sendspin block (minimum):***

```json
  ,
  "sendspin": {
    "enabled": true,
    "connection": {
      "mdns": true
    }
  }
```

***As tested***

```bash
  ,
  "sendspin": {
  "enabled": true,
  "connection": {
    "time_sync_adaptive": true,
    "time_sync_interval_seconds": 1.0
  },
  "player": {
    "output_latency_ms": -600,
    "sync_target_latency_ms": 350,
    "sync_late_drop_ms": 250
  }
}
```

Due to differences in chipsets and the mpv player pipeline, there may be a consistent lead/lag when compared to other sendspin clients. Especially when they are on other platforms (i.e. ESP32). The best knob for bringing the LVA sendspin client into initial sync (calibrate) is output_latency_ms. My client was a ~1 second behind the other player. The -600 value has closed that to a point that it is hard to hear a difference, but you may need to adjust accordingly based on what your testing shows.

Here is a good rule for tuning:

`LVA player 1 second behind other player = "output_latency_ms": -600`

`LVA player 1 second ahead other player = "output_latency_ms": 600`

Then make adjustments until the two players are synchronized.

> Take a look at ~/linux-voice-assistant/linux_voice_assistant/config.json.example for details on these settings as well as all available options.

***Restart LVA:***

```bash
systemctl --user restart linux-voice-assistant.service
```

***Verify:***

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

</details>

## 6. Connect to Home Assistant

### If HA does not discover the new LVA:

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"
6. During the registration process, use the wake word that is displayed on the registration page. Default is "OK Nabu".

## 7. Verification

- Use "journalctl --user -u linux-voice-assistant.service -f" to check for errors. Debugging is enabled.
  
  - Expect logs like `Connected to Home Assistant`
  - Look for `[OWW] Detection: name=...` followed by re-arming/cycling
  - Ask: *“What time is it?”* and confirm TTS reply
- If you do not get a voice response, check the Voice Assistant that you choose during registration has a voice assigned to it.
  
  ### Settings -> Voice assistants -> Assist (the assistant you configured) -> Text-to-speech -> Voice
  

## 8. Change OWW detection model

After the LVA is registered with HA, you can change the Wake Word model used in the ESPHome Voice Assistant entity.

Project OWW models include:

```text
alexa_v0.1.tflite       -> Alexa
hey_jarvis_v0.1.tflite  -> Hey Jarvis
hey_mycroft_v0.1.tflite -> Hey Mycroft
hey_rhasspy_v0.1.tflite -> Hey Rhasspy
ok_nabu_v0.1.tflite     -> OK Nabu **(I had to say OK Nobu)**
```

Additional community provided OWW models available from this repository:
https://github.com/fwartner/home-assistant-wakewords-collection

You just copy the ones you want into the ~/linux-voice-assistant/wakewords/openWakeWord directory. If a model is corrupted, the LVA will fail to start.
Each model added will need a corresponding json file. (note the json file names match the tflite name)

***Example***

Add Model:

```
/linux-voice-assistant/wakewords/openWakeWord/hal_v2.tflite
```

Create Json:

```
/linux-voice-assistant/wakewords/openWakeWord/hal_v2.json
```

Contents:

```
{
  "type": "openWakeWord",
  "wake_word": "HAL",
  "model": "hal_v2.tflite"
}
```

**Word of warning. I have had problems with some of the community provided wake words. YMMV**

## 9. Switching between PipeWire and PulseAudio (see Section 5)

**ALSA-only is not supported** with the current audio backend.

If you want to switch between **PulseAudio** and **PipeWire-Pulse**, first stop/disable the currently-running user services, then follow the matching tutorial in Section 5 and restart LVA.

### PulseAudio

```bash
systemctl --user stop pulseaudio.service || true
systemctl --user disable pulseaudio.service || true
```

### PipeWire (PipeWire-Pulse)

```bash
systemctl --user stop pipewire.service pipewire-pulse.service wireplumber.service || true
systemctl --user disable pipewire.service pipewire-pulse.service wireplumber.service || true
```

## 10. Safely Upgrade from previous version of LVA

***You will need a minimum of 550 GB of free space to use this process.***

Verify that you have enough free disk space:

```bash
df -h

Filesystem      Size  Used Avail Use% Mounted on
udev            912M     0  912M   0% /dev
tmpfs           198M  3.4M  195M   2% /run
# This is the entry that indicates available free space
/dev/mmcblk1p1   57G  3.2G   53G   6% /
#
tmpfs           988M     0  988M   0% /dev/shm
tmpfs           5.0M     0  5.0M   0% /run/lock
tmpfs           1.0M     0  1.0M   0% /run/credentials/systemd-resolved.service
tmpfs           1.0M     0  1.0M   0% /run/credentials/systemd-networkd.service
tmpfs           988M     0  988M   0% /tmp
/dev/zram1       47M  424K   43M   1% /var/log
tmpfs           1.0M     0  1.0M   0% /run/credentials/systemd-journald.service
tmpfs           198M  8.0K  198M   1% /run/user/1000
tmpfs           1.0M     0  1.0M   0% /run/credentials/getty@tty1.service
tmpfs           1.0M     0  1.0M   0% /run/credentials/serial-getty@ttyS0.service
```

Stop any running LVA systemd unit files:

```bash
# Stop LVA Service
systemctl --user stop linux-voice-assistant.service

# Stop Tray Client Service if you are using it
systemctl --user stop linux-voide-assistant-tray.service
```

Save current version of LVA (the safe part):

```bash
mv ~/linux-voice-assistant ~/linux-voice-assistant_save
```

Clone the latest version of the LVA project:

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
```

Restore saved config.json and preferences.json files:

```bash
cp ~/linux-voice-assistant_save/preferences.json ~/linux-voice-assistant/
cp ~/linux-voice-assistant_save/linux_voice_assistant/config.json ~/linux-voice-assistant/linux_voice_assistant/
```

Setup the new version of LVA:

```bash
cd ~/linux-voice-assistant

# LVA without the Sendspin client or Tray client
script/setup

# LVA and the Sendspin client
script/setup --sendspin

# LVA with Tray and Sendspin client
script/setup --tray --sendspin
```

If you're sure that everything went well, restart the LVA service

```bash
# LVA service
systemctl --user restart linux-voice-assistant.service

# LVA Tray service
systemctl --user restart linux-voice-assistant-tray.service
```

If you're not sure, or if the LVA didn't start as expected, run the LVA via CLI with debug:

```bash
# LVA
script/run --debug

# LVA Tray client
script/run --tray
```

Optionally, once you are comfortable with the new version, remove the older version of the LVA:

```bash
# !!! BE CAREFUL WITH THIS COMMAND !!!
rm -rf ~/linux-voice-assistant_save
```
