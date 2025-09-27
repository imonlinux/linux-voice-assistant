# Linux Voice Assistant on RaspberryPi with ReSpeaker 2‑Mic — Installation & Configuration Guide

> Created using ChatGPT 5 with the following prompt:
```html
      Using the following github document as a guide
      https://github.com/rhasspy/wyoming-satellite/blob/master/docs/tutorial_2mic.md,
      take the attached bash history of commands and create a similar document
      detailing the installation and configuration of this linux-voice-assistant project.
```
> Modeled after the Wyoming Satellite two‑mic tutorial, adapted from actual shell history.

This guide reproduces a working setup of the **linux-voice-assistant** project with **Wyoming OpenWakeWord** or **MicroWakeWord** on a Raspberry PI Zero 2W and a Respeaker 2‑mic HAT (e.g., seeed-2mic-voicecard). It assumes a fresh system with sudo access and the default "pi" user. Included is the option to use PipeWire or PulseAudio instead of ALSA.

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
      libmpv-dev mpv python3.11-dev avahi-daemon avahi-utils
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


## 4. Wyoming OpenWakeWord (OWW), skip to step 5. if using MicroWakeWord (MWW)


```bash
git clone https://github.com/rhasspy/wyoming-openwakeword.git
```

```bash
cp ~/linux-voice-assistant/wyoming-openwakeword/requirements.txt ~/wyoming-openwakeword/requirements.txt
cd wyoming-openwakeword/
script/setup
```


## 5. Linux Voice Assistant (LVA)

```bash
cd ~/linux-voice-assistant/
script/setup
```


## 6–7. Choose your install option "Choose your Adventure!"

Pick **one** of the following install paths. Expand a section to see the exact steps.

> Tip: PipeWire options run services in *user* mode (requires `loginctl enable-linger`); PulseAudio/ALSA options run services in *system* mode.

<details>
<summary><strong>PipeWire + OpenWakeWord (user-mode services)</strong></summary>

**Prep (PipeWire):** Follow the PipeWire tutorial first: [the tutorial](install_pipewire.md).

**Enable linger (required for user services to start after reboot):**
```bash
sudo loginctl enable-linger pi
```

**Install user-mode services:**
```bash
mkdir -p ~/.config/systemd/user

# LVA
cp ~/linux-voice-assistant/service/user-pw-oww-linux-voice-assistant.service.service    ~/.config/systemd/user/linux-voice-assistant.service

# OWW
cp ~/linux-voice-assistant/service/user-wyoming-openwakeword.service.service    ~/.config/systemd/user/wyoming-openwakeword.service
```

**Enable & start:**
```bash
systemctl --user daemon-reload
systemctl --user enable --now wyoming-openwakeword.service
systemctl --user enable --now linux-voice-assistant.service
```

**Verify:**
```bash
systemctl --user status linux-voice-assistant wyoming-openwakeword --no-pager -l
```
</details>

<details>
<summary><strong>PipeWire + MicroWakeWord (user-mode services)</strong></summary>

**Prep (PipeWire):** Follow the PipeWire tutorial first: [the tutorial](install_pipewire.md).

**Enable linger:**
```bash
sudo loginctl enable-linger pi
```

**Install user-mode service:**
```bash
mkdir -p ~/.config/systemd/user
cp ~/linux-voice-assistant/service/user-pw-mww-linux-voice-assistant.service.service    ~/.config/systemd/user/linux-voice-assistant.service
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
<summary><strong>PulseAudio + OpenWakeWord (system services)</strong></summary>

**Prep (PulseAudio):** Follow the PulseAudio tutorial first: [the tutorial](install_pulseaudio.md).

**Install LVA (system) + tuning drop-in:**
```bash
sudo cp ~/linux-voice-assistant/service/pa-oww-linux-voice-assistant.service         /etc/systemd/system/linux-voice-assistant.service

sudo mkdir -p /etc/systemd/system/linux-voice-assistant.service.d
sudo cp ~/linux-voice-assistant/service/10-tuning.conf         /etc/systemd/system/linux-voice-assistant.service.d/10-tuning.conf
```

**Install OWW (system):**
```bash
sudo cp ~/linux-voice-assistant/service/wyoming-openwakeword.service         /etc/systemd/system/wyoming-openwakeword.service
```

**Enable & start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-openwakeword linux-voice-assistant
sudo systemctl status linux-voice-assistant wyoming-openwakeword --no-pager -l
```
</details>

<details>
<summary><strong>PulseAudio + MicroWakeWord (system services)</strong></summary>

**Prep (PulseAudio):** Follow the PulseAudio tutorial first: [the tutorial](install_pulseaudio.md).

**Install LVA (system) + tuning drop-in:**
```bash
sudo cp ~/linux-voice-assistant/service/pa-mww-linux-voice-assistant.service         /etc/systemd/system/linux-voice-assistant.service

sudo mkdir -p /etc/systemd/system/linux-voice-assistant.service.d
sudo cp ~/linux-voice-assistant/service/10-tuning.conf         /etc/systemd/system/linux-voice-assistant.service.d/10-tuning.conf
```

**Enable & start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now linux-voice-assistant
sudo systemctl status linux-voice-assistant --no-pager -l
```
</details>

<details>
<summary><strong>ALSA + OpenWakeWord (system services)</strong></summary>

**No extra audio stack needed.** If you’re using a different sound card/driver, confirm device names:
```bash
arecord -l
aplay -l
```

**Install LVA (system):**
```bash
sudo cp ~/linux-voice-assistant/service/alsa-oww-linux-voice-assistant.service         /etc/systemd/system/linux-voice-assistant.service
```

**Install OWW (system):**
```bash
sudo cp ~/linux-voice-assistant/service/wyoming-openwakeword.service         /etc/systemd/system/wyoming-openwakeword.service
```

**Enable & start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-openwakeword linux-voice-assistant
sudo systemctl status linux-voice-assistant wyoming-openwakeword --no-pager -l
```
</details>

<details>
<summary><strong>ALSA + MicroWakeWord (system services)</strong></summary>

**No extra audio stack needed.** If you’re using a different sound card/driver, confirm device names:
```bash
arecord -l
aplay -l
```

**Install LVA (system):**
```bash
sudo cp ~/linux-voice-assistant/service/alsa-mww-linux-voice-assistant.service         /etc/systemd/system/linux-voice-assistant.service
```

**Enable & start:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now linux-voice-assistant
sudo systemctl status linux-voice-assistant --no-pager -l
```
</details>

<details>
<summary><strong>Optional: Advertise LVA to Home Assistant via Avahi</strong></summary>

**Generate & install ESPHome-style mDNS service:**
```bash
chmod +x ~/linux-voice-assistant/script/gen-esphome-avahi.sh
sudo ~/linux-voice-assistant/script/gen-esphome-avahi.sh
sudo systemctl restart avahi-daemon.service
```
</details>


## 8. Connect to Home Assistant

### If HA does not discover the new LVA:

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"
6. During the registration process, use the wake word that you configured in your linux-voice-assistant.service file. Default is "alexa".


## 9. Verification
- Use "journalctl -u linux-voice-assistant.service -f" to check for errors for ALSA and PluseAudio. Debugging is enabled.
- Use "journalctl --user -u linux-voice-assistant.service -f" to check for errors when using PipeWire. Debugging is enabled.
 - Expect logs like `Connected to Home Assistant`
 - Look for `[OWW] Detection: name=...` followed by re-arming/cycling
 - Ask: *“What time is it?”* and confirm TTS reply
- If you do not get a voice response, check the Voice Assistant that you choose during registration has a voice assigned to it.
  
     ### Settings -> Voice assistants -> Assist (the assistant you configured) -> Text-to-speech -> Voice


## 10. Change OWW detection model

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

You just copy the ones you want into the ~/wyoming-openwakeword/wyoming-openwakeword/models directory. If a model is currupted, the wyoming-openwakeword.service will fail to start.

**Word of warning. I have had problems with some of the community provided wake words. YMMV**


## 11. Switching between OWW and MWW. Or ALSA and PA see section 7.

If you intend to switch from PA to ALSA, you must first stop the pusleaudio.service.

```bash
sudo systemctl stop pulseaudio.service
```
```bash
sudo systemctl disable pulseaudio.service
```
