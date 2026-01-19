# Linux Voice Assistant on RaspberryPi with ReSpeaker 2‑Mic — Installation & Configuration Guide

> Created using ChatGPT 5 with the following prompt:
```html
      Using the following github document as a guide
      https://github.com/rhasspy/wyoming-satellite/blob/master/docs/tutorial_2mic.md,
      take the attached bash history of commands and create a similar document
      detailing the installation and configuration of this linux-voice-assistant project.
```
> Modeled after the Wyoming Satellite two‑mic tutorial, adapted from actual shell history.

This guide reproduces a working setup of the **linux-voice-assistant** project with **Wyoming OpenWakeWord** and **MicroWakeWord** on a Raspberry PI Zero 2W and a Respeaker 2‑mic HAT (e.g., seeed-2mic-voicecard). It assumes a fresh system with sudo access and the default "pi" user. Included is the option to use PipeWire or PulseAudio instead of ALSA.

## Prerequisites
- Raspberry Pi OS Lite (64-bit) (Bookworm or Trixie)
- Default Python 3.11+ recommended
- A ReSpeaker 2‑mic sound card or compatable
- Network access to your Home Assistant instance


## 1. Install system packages

```bash
sudo apt update
sudo apt upgrade
sudo apt install build-essential git \
      libmpv-dev mpv python3-dev python3-venv
sudo reboot
```


## 2. Get the code

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
```


## 3. Install ReSpeaker drivers

```bash
chmod +x ~/linux-voice-assistant/respeaker2mic/install-respeaker-drivers.sh
sudo ~/linux-voice-assistant/respeaker2mic/install-respeaker-drivers.sh 
sudo reboot
```


## 4. Linux Voice Assistant (LVA)

```bash
cd ~/linux-voice-assistant/
script/setup
```


## 5. Choose your install option "Choose your Adventure!"

Pick **one** of the following install paths. Expand a section to see the exact steps.

> Tip: All services run in *user* mode (requires `loginctl enable-linger`);

<details>
<summary><strong>PipeWire (user-mode services)</strong></summary>

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
<summary><strong>ALSA (user-mode services)</strong></summary>

**No extra audio stack needed.** If you’re using a different sound card/driver, confirm device names:
```bash
arecord -l
aplay -l
```

**Enable linger (required for user services to start after reboot):**
```bash
sudo loginctl enable-linger pi
```

**Install LVA user-mode services:**

```bash
mkdir -p ~/.config/systemd/user
```

```bash
cp ~/linux-voice-assistant/service/linux-voice-assistant.service     ~/.config/systemd/user/linux-voice-assistant.service
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

* **MQTT Discovery Topic:** `homeassistant/switch/<device_id>_mute/config`
* **Name:** `[LVA Name] Mute Microphone`
* **Icon:** `mdi:microphone-off`
* **Functionality:** Toggles the microphone mute. When muted, the LEDs will display a dim red `solid` effect.

---

## 💡 LED Control for Linux Voice Assistant
These are MQTT entities created by the Linux Voice Assistant for controlling its integrated LEDs (e.g., DotStar, NeoPixel). These entities integrate seamlessly with Home Assistant via MQTT Discovery, allowing you to manage LED effects, colors, brightness, and the number of connected LEDs directly from your Home Assistant interface.

---

### 🔢 Number of LEDs (`number` entity)

This entity allows you to specify the physical number of addressable LEDs connected to your device.


* **MQTT Discovery Topic:** `homeassistant/number/<device_id>_num_leds/config`
* **Name:** `[LVA Name] Number of LEDs`
* **Functionality:** Sets the total number of LEDs.
* **Important:** This setting requires a **restart of the LVA service file** to take effect, as the LED hardware driver needs to be re-initialized. The value is persisted in `preferences.json`.

---

### ✨ State-Based LED Controls (`select` and `light` entities)

For each distinct LVA state (Idle, Listening, Thinking, Responding, Error), a `select` entity for choosing an effect and a `light` entity for controlling color and brightness are created.

* **States:**
    * `idle` (e.g., `[LVA Name] Idle Effect`, `[LVA Name] Idle Color`)
    * `listening` (e.g., `[LVA Name] Listening Effect`, `[LVA Name] Listening Color`)
    * `thinking` (e.g., `[LVA Name] Thinking Effect`, `[LVA Name] Thinking Color`)
    * `responding` (e.g., `[LVA Name] Responding Effect`, `[LVA Name] Responding Color`)
    * `error` (e.g., `[LVA Name] Error Effect`, `[LVA Name] Error Color`)

#### `select` Entities (Effect Selector)

* **MQTT Discovery Topic:** `homeassistant/select/<device_id>_<state_name>_effect/config`
* **Name:** `[LVA Name] [State Name] Effect`
* **Icon:** `mdi:palette-swatch-variant`
* **Functionality:** Allows selection of an animation/effect for the specific LVA state.

#### `light` Entities (Color & Brightness Control)

* **MQTT Discovery Topic:** `homeassistant/light/<device_id>_<state_name>_color/config`
* **Name:** `[LVA Name] [State Name] Color`
* **Functionality:** Controls the color and brightness for the specific LVA state when the selected effect uses color. Supports RGB color mode and brightness.

---

## 🎨 Available LED Effects

The following effects can be selected via the `[State Name] Effect` (select) entities:

| Effect Name | Description |
| :--- | :--- |
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
| :--- | :--- | :--- |
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

| Setting                | Allowed Values | Typical Value | What It Does                                                                 | When To Change                                                                                  |
|------------------------|----------------|---------------|-------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| `analog_gain_control`  | `0` or `1`     | `0`           | Lets WebRTC AEC “ride” the hardware/analog mic gain.                         | Leave `0` when you already tuned mic gain in ALSA/Pulse. Use `1` only if your mic is too quiet and you want auto-leveling at the expense of some consistency. |
| `digital_gain_control` | `0` or `1`     | `1`           | Software AGC on the captured signal (after the ADC).                         | Keep `1` for voice assistants so wake-word and STT get a stable level. Turn `0` if you already run separate AGC or notice pumping/breathing.                  |
| `noise_suppression`    | `0` or `1`     | `1`           | Enables WebRTC noise reduction on the mic signal.                            | Keep `1` in most cases (fans, room noise). Try `0` if audio sounds “underwater” or dull and your environment is already very quiet.                          |
| `extended_filter`*     | `0` or `1`     | `1` (often)   | Uses a more robust AEC filter that handles tricky echo paths / long delays.  | Use `1` for speaker-in-room setups (like LVA) unless CPU is extremely constrained.                                      |
| `delay_agnostic`*      | `0` or `1`     | `1` (often)   | Makes AEC less sensitive to exact playback/capture latency.                  | Keep `1` if devices/paths change or Bluetooth is involved. Set `0` only if you know latency is rock-stable and want to shave a bit of CPU.                  |
| `drift_compensation`*  | `0` or `1`     | `1` (often)   | Compensates for clock drift between capture and playback devices.            | Use `1` if mic and speakers are on different hardware (USB mic + HDMI/Bluetooth out). `0` is OK when both share the same clock (onboard codec only).        |
| `voice_detection`*     | `0` or `1`     | `0` or `1`    | Simple VAD that can help AEC and noise suppression focus on speech segments. | Try `1` if you see good wake-word hits but noisy STT. Use `0` if it seems to cut off very quiet speech or initial phonemes.                                 |


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
<summary><strong>Optional (GPIO Button)</strong></summary>

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

You just copy the ones you want into the ~/linux-voice-assistant/wakewords/openWakeWord directory. If a model is currupted, the LVA will fail to start.
Each model added will need a corresponding json file. (note the json file names matches the tflite name)

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


## 9. Switching between ALSA, PW, or PA see section 5.

If you intend to switch from PA or PW to ALSA, you must first stop and disable the corresponding user-mode services.

###PulseAudio

```bash
sudo systemctl --user stop pulseaudio.service
```
```bash
sudo systemctl --user disable pulseaudio.service
```
###PipeWire

```bash
sudo systemctl --user stop pipewire.service
```
```bash
sudo systemctl --user disable pipewire.service
```
