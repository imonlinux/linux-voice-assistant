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
- Raspberry Pi OS Lite (64-bit)
  - Linux 6.12.34+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.12.34-1+rpt1~bookworm
  - (2025-06-26) aarch64 GNU/Linux
- Default Python 3.11+ recommended
- A ReSpeaker 2‑mic sound card or compatable
- Network access to your Home Assistant instance


## 1. Install system packages

```bash
sudo apt update
sudo apt upgrade
sudo apt install libportaudio2 build-essential git \
      libmpv-dev mpv python3.11-dev 
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
cp ~/linux-voice-assistant/service/user-pw-linux-voice-assistant.service    ~/.config/systemd/user/linux-voice-assistant.service
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
cp ~/linux-voice-assistant/service/user-pa-linux-voice-assistant.service   ~/.config/systemd/user/linux-voice-assistant.service
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
cp ~/linux-voice-assistant/service/user-linux-voice-assistant.service     ~/.config/systemd/user/linux-voice-assistant.service
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

**Edit LVA user-mode service:**

```bash
systemctl --user edit --force --full linux-voice-assistant
```

**Add MQTT configuration entries to LVA user-mode service:**

```bash
  --mqtt-host <IP_Adress> \
  --mqtt-port 1883 \
  --mqtt-username <user_name> \
  --mqtt-password <password> \
```

**Example (Alsa LVA User Mode Service File)**
***Note: Change the MQTT values to match your system!***

```bash
[Unit]
Description=Linux Voice Assistant

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
Environment=PYTHONUNBUFFERED=1

ExecStart=/home/pi/linux-voice-assistant/script/run \
  --name 'Linux Voice Assistant' \
  --mqtt-host 192.168.1.2 \
  --mqtt-port 1883 \
  --mqtt-username mqtt \
  --mqtt-password Ch@ngeMe_1st \
  --wake-word-dir wakewords/openWakeWord  \
  --wake-word-dir wakewords \
  --stop-model stop \
  --audio-input-device seeed-2mic-voicecard \
  --audio-output-device alsa/plughw:CARD=seeed2micvoicec \
  --debug 

Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```


**Enable & start:**
```bash
systemctl --user daemon-reload
systemctl --user restart linux-voice-assistant.service
```

**Verify:**
```bash
systemctl --user status linux-voice-assistant --no-pager -l
```
</details>

<details>
<summary><strong>Optional (Grove Port LEDs)</strong></summary>

**Edit LVA user-mode service:**

```bash
systemctl --user edit --force --full linux-voice-assistant
```

**Add MQTT configuration entries to LVA user-mode service:**

```bash
  --led-interface gpio \
  --led-data-pin 12 \
  --led-clock-pin 13 \
```

**Example (Alsa LVA User Mode Service File with MQTT Enabled)**
***Note: Change the GPIO values to match your system!***

```bash
[Unit]
Description=Linux Voice Assistant

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
Environment=PYTHONUNBUFFERED=1

ExecStart=/home/pi/linux-voice-assistant/script/run \
  --name 'Linux Voice Assistant' \
  --mqtt-host 192.168.1.2 \
  --mqtt-port 1883 \
  --mqtt-username mqtt \
  --mqtt-password Ch@ngeMe_1st \
  --led-interface gpio \
  --led-data-pin 12 \
  --led-clock-pin 13 \
  --wake-word-dir wakewords/openWakeWord  \
  --wake-word-dir wakewords \
  --stop-model stop \
  --audio-input-device seeed-2mic-voicecard \
  --audio-output-device alsa/plughw:CARD=seeed2micvoicec \
  --debug 

Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```


**Enable & start:**
```bash
systemctl --user daemon-reload
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
ok_nabu_v0.1.tflite     -> OK Nabo **(I had to say OK Nobu)**
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
