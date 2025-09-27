# Linux Voice Assistant on 2‑Mic Linux — Installation & Configuration Guide

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
git clone https://github.com/rhasspy/wyoming-openwakeword.git
```


## 3. Install ReSpeaker drivers

```bash
chmod +x ~/linux-voice-assistant/respeaker2mic/install-respeaker-drivers.sh
sudo ~/linux-voice-assistant/respeaker2mic/install-respeaker-drivers.sh 
sudo reboot
```


## 4. Wyoming OpenWakeWord (OWW), skip to step 5. if using MicroWakeWord (MWW)

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


## 6. Configure audio devices, choose PipeWire, PulseAudio, or ALSA (default)

### (Optional) PipeWire

See [the tutorial](install_pipewire.md) to install and configure PipeWire.

### (Optional) PulseAudio

See [the tutorial](install_pulseaudio.md) to install and configure PulseAudio.

### For Standard ALSA

You shouldn't have to change anything if you are using the driver provided in this repo. If you are using something else, find your sound device names and update the linux-voice-assistant/service/alsa-oww-linux-voice-assistant.service used with OpenWakeWord or linux-voice-assistant/service/alsa-mww-linux-voice-assistant.service file used with MicroWakeWord to match sound card details:

```bash
arecord -l
aplay -l
```


## 7. Systemd services

### (Optional) enable Avahi service file to advertise LVA to HA:

```bash
chmod +x ~/linux-voice-assistant/script/gen-esphome-avahi.sh
sudo ~/linux-voice-assistant/script/gen-esphome-avahi.sh
```

```bash
sudo systemctl restart avahi-daemon.service
```

### (Optional) for PipeWire with OWW copy these service files to /home/pi/.config/systemd/user
### Note: This installs OWW and LVA as User Mode Systemd services. This requires linger to be enabled on the user (pi).
### Note: Without linger the User Mode Systemd service(s) will not start automatically after a reboot.

Enable linger for the user:

```bash
sudo loginctl enable-linger pi
```

Install User Mode Service files:

```bash
mkdir -p ~/.config/systemd/user

cp ~/linux-voice-assistant/service/user-pw-oww-linux-voice-assistant.service.service ~/.config/systemd/user/linux-voice-assistant.service

cp ~/linux-voice-assistant/service/user-wyoming-openwakeword.service.service ~/.config/systemd/user/wyoming-openwakeword.service.service
```

Enable and start the user mode services:

```bash
systemctl --user daemon-reload
systemctl --user enable --now wyoming-openwakeword.service
systemctl --user enable --now linux-voice-assistant.service
```

Check that the user mode services are running:

```bash
sytemctl --user status linux-voice-assistat.service
```

#### Proceed to step 8 (Connecting to HA)


### (Optional) for PipeWire with MWW copy this service file to /home/pi/.config/systemd/user
### Note: This installs LVA as User Mode Systemd service. This requires linger to be enabled on the user (pi).
### Note: Without linger the User Mode Systemd service(s) will not start automatically after a reboot.

Enable linger for the user:

```bash
sudo loginctl enable-linger pi
```

Install User Mode Service file:

```bash
mkdir -p ~/.config/systemd/user

cp ~/linux-voice-assistant/service/user-pw-mww-linux-voice-assistant.service.service ~/.config/systemd/user/linux-voice-assistant.service
```

Enable and start the user mode service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant.service
```

Check that the user mode service is running:

```bash
sytemctl --user status linux-voice-assistat.service
```

#### Proceed to step 8 (Connecting to HA)


### (Optional) for PulseAudio with OWW copy this service file into /etc/systemd/system/:

```bash
sudo cp ~/linux-voice-assistant/service/pa-oww-linux-voice-assistant.service /etc/systemd/system/linux-voice-assistant.service
```
Create the systemd drop-in directory for linux-voice-assistant.service:

```bash
sudo mkdir -p /etc/systemd/system/linux-voice-assistant.service.d
```

```bash
sudo cp ~/linux-voice-assistant/service/10-tuning.conf \
      /etc/systemd/system/linux-voice-assistant.service.d/10-tuning.conf
```

### OR for Pulse Audio  with MWW copy this service file into /etc/systemd/system/:

```bash
sudo cp ~/linux-voice-assistant/service/pa-mww-linux-voice-assistant.service /etc/systemd/system/linux-voice-assistant.service
```
Create the systemd drop-in directory for linux-voice-assistant.service:

```bash
sudo mkdir -p /etc/systemd/system/linux-voice-assistant.service.d
```

```bash
sudo cp ~/linux-voice-assistant/service/10-tuning.conf \
      /etc/systemd/system/linux-voice-assistant.service.d/10-tuning.conf
```


### For ALSA with OWW copy this service file into /etc/systemd/system/:

```bash
sudo cp ~/linux-voice-assistant/service/alsa-oww-linux-voice-assistant.service /etc/systemd/system/linux-voice-assistant.service
```


### OR for ALSA with MWW copy this service file into /etc/systemd/system/:

```bash
sudo cp ~/linux-voice-assistant/service/alsa-mww-linux-voice-assistant.service /etc/systemd/system/linux-voice-assistant.service
```

### For either ALSA or PulseAudio systems with the OWW copy this service file into /etc/systemd/system/:
### Not needed if using PipeWire

```bash
sudo cp ~/linux-voice-assistant/service/wyoming-openwakeword.service /etc/systemd/system/wyoming-openwakeword.service
```

### For either ALSA or PulseAudio start new services and confirm services are running:
### Not needed if using PipeWire

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now linux-voice-assistant
sudo systemctl status linux-voice-assistant wyoming-openwakeword --no-pager -l
```


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
