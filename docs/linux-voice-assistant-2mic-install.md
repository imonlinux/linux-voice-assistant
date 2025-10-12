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
cp ~/linux-voice-assistant/service/user-pw-linux-voice-assistant.service.service    ~/.config/systemd/user/linux-voice-assistant.service
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
cp ~/linux-voice-assistant/service/user-pa-linux-voice-assistant.service.service    ~/.config/systemd/user/linux-voice-assistant.service
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
sudo cp ~/linux-voice-assistant/service/user-linux-voice-assistant.service     ~/.config/systemd/user/linux-voice-assistant.service
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
