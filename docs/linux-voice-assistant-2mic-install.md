# Linux Voice Assistant on 2‑Mic Linux — Installation & Configuration Guide

> Created using ChatGPT 5 with the following prompt:
```html
      Using the following github document as a guide
      https://github.com/rhasspy/wyoming-satellite/blob/master/docs/tutorial_2mic.md,
      take the attached bash history of commands and create a similar document
      detailing the installation and configuration of this linux-voice-assistant project.
```
> Modeled after the Wyoming Satellite two‑mic tutorial, adapted from actual shell history.

This guide reproduces a working setup of the **linux-voice-assistant** project with **Wyoming OpenWakeWord** on a Raspberry PI Zero 2W and a Respeaker 2‑mic HAT (e.g., seeed-2mic-voicecard). It assumes a fresh system with sudo access.

## Prerequisites
- A Linux system (Debian/Ubuntu/Raspberry Pi OS or compatible)
- Python 3.11+ recommended
- A 2‑mic sound card
- Network access to your Home Assistant instance


## 1. Install system packages

```bash
sudo apt update
sudo apt upgrade
sudo apt install libportaudio2 build-essential git libmpv-dev mpv python3.11-dev
sudo reboot
```


## 2. Get the code

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
git clone https://github.com/rhasspy/wyoming-openwakeword.git
```


## 3. Install ReSpeaker drivers

```bash
cd linux-voice-assistant/respeaker2mic/
chmod +x ./install-respeaker-drivers.sh
sudo ./install-respeaker-drivers.sh 
sudo reboot
```


## 4. Wyoming OpenWakeWord (OWW)

```bash
cp ./linux-voice-assistant/wyoming-openwakeword/requirements.txt ./wyoming-openwakeword/requirements.txt
cd wyoming-openwakeword/
script/setup
```


## 5. Configure audio devices, choose either Pulse Audio or ALSA (default)

### (Optional) Pulse Audio

See [the tutorial](docs/install_pulseaudio.md) to install and configure Pulse Audio.

### For Standard ALSA

You shouldn't have to change anything if you are using the driver provided in this repo. If you are using something else, find your sound device names and update the linux-voice-assistant/service/linux-voice-assistant.service file to match sound card details:

```bash
arecord -l
aplay -l
```


## 6. Systemd services

### (Optional) for Pulse Audio copy this service file into /etc/systemd/system/:

```bash
sudo cp ./service/pa-linux-voice-assistant.service /etc/systemd/system/linux-voice-assistant.service
```


### For ALSA copy this service file into /etc/systemd/system/:

```bash
sudo cp ./service/linux-voice-assistant.service /etc/systemd/system/linux-voice-assistant.service
```

### For either ALSA or Pusle Audio systems, copy the Wyoming OpenWakeWord service file into /etc/systemd/system/:

```bash
sudo cp ./service/wyoming-openwakeword.service /etc/systemd/system/wyoming-openwakeword.service
```

Start new services and confirm services are running:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now linux-voice-assistant
sudo systemctl status linux-voice-assistant wyoming-openwakeword --no-pager -l
```


## 7. Connect to Home Assistant

1. In Home Assistant, go to "Settings" -> "Device & services"
2. Click the "Add integration" button
3. Choose "ESPHome" and then "Set up another instance of ESPHome"
4. Enter the IP address of your voice satellite with port 6053
5. Click "Submit"


## 8. Verification

- Expect logs like `Connected to Home Assistant`
- Look for `[OWW] Detection: name=...` followed by re-arming/cycling
- Ask: *“What time is it?”* and confirm TTS reply
- If you do not get a voice response, check the Voice Assistant that you choose during registration has a voice assigned to it.
  
      Settings -> Voice assistants -> Assist (the assistant you configured) -> Text-to-speech -> Voice


## 9. Change OWW detection model (Depricated-Wake word can be selected in HA now)

Edit the linux-voice-assistant.service file and change the OWW configuration argument for --wake-word-name.
Project OWW models include:

```text
alexa_v0.1.tflite
hey_jarvis_v0.1.tflite
hey_mycroft_v0.1.tflite
hey_rhasspy_v0.1.tflite
ok_nabu_v0.1.tflite
```

Additional community provided OWW models available from this repository:
https://github.com/fwartner/home-assistant-wakewords-collection

**Word of warning. I have had problems with some of the community provided wake words. YMMV**

Edit linux-voice-assistant.service file:
```bash
sudo systemctl edit --force --full linux-voice-assistant.service 
```

Service file as provided using OWW 'ok_nabu':
```text
[Unit]
Description=Linux Voice Assistant
Requires=wyoming-openwakeword.service
After=sound.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
ExecStart=/home/pi/linux-voice-assistant/script/run \
  --name 'Linux Satellite' \
  --audio-input-device seeed-2mic-voicecard \
  --audio-output-device alsa/hw:1,0 \
  --wake-uri 'tcp://127.0.0.1:10400' \
  --wake-word-name 'ok_nabu'
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Service file using OWW 'alexa':

```text
[Unit]
Description=Linux Voice Assistant
Requires=wyoming-openwakeword.service
After=sound.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
ExecStart=/home/pi/linux-voice-assistant/script/run \
  --name 'Linux Satellite' \
  --audio-input-device seeed-2mic-voicecard \
  --audio-output-device alsa/hw:1,0 \
  --wake-uri 'tcp://127.0.0.1:10400' \
  --wake-word-name 'alexa'
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Reload systemd with updated linux-voice-assistant.service file, restart linux-voice-assistant.service, and confirm successful run:
```bash
sudo systemctl daemon-reload
sudo systemctl restart linux-voice-assistant.service
sudo systemctl status linux-voice-assistant wyoming-openwakeword --no-pager -l
```

## 10. Revert to MicroWakeWord

Edit the linux-voice-assistant.service file and remove the OWW configuration arguments:

```bash
sudo systemctl edit --force --full linux-voice-assistant.service 
```

Service file with OWW:
```text
[Unit]
Description=Linux Voice Assistant
Requires=wyoming-openwakeword.service
After=sound.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
ExecStart=/home/pi/linux-voice-assistant/script/run \
  --name 'Linux Satellite' \
  --audio-input-device seeed-2mic-voicecard \
  --audio-output-device alsa/hw:1,0 \
  --wake-uri 'tcp://127.0.0.1:10400' \
  --wake-word-name 'ok_nabu'
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Service file without OWW:
```text
[Unit]
Description=Linux Voice Assistant
Requires=wyoming-openwakeword.service
After=sound.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
ExecStart=/home/pi/linux-voice-assistant/script/run \
  --name 'Linux Satellite' \
  --audio-input-device seeed-2mic-voicecard \
  --audio-output-device alsa/hw:1,0 
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```
Reload systemd with updated linux-voice-assistant.service file and restart linux-voice-assistant.service and confirm successful run:
```bash
sudo systemctl daemon-reload
sudo systemctl restart linux-voice-assistant.service
sudo systemctl status linux-voice-assistant wyoming-openwakeword --no-pager -l
```
