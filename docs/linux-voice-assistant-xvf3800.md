# Linux Voice Assistant with ReSpeaker XVF3800 4‑Mic Array — Installation & Configuration Guide

> Drafted with ChatGPT based on existing `linux-voice-assistant-2mic-install.md` and community test results from the XVF3800 integration Discussion.

> Special thanks to @JenniferHatches and @cknic for making the recommendation and doing so much work already for support of the reSpeaker XVF3800. (https://github.com/cknic/linux-voice-assistant) The addition of XVF3800 support would never have happened without their troubleshooting of the code and fantastic suggestions when working through bugs.

This guide shows how to use the **Seeed ReSpeaker XVF3800 USB 4‑Mic Array** with the **Linux Voice Assistant** (LVA) project on Raspberry Pi or other Linux hosts.

Unlike the ReSpeaker 2‑Mic HAT, the XVF3800 is a **USB Audio Class 2.0** device: it acts as a “smart microphone + speaker” front end with built‑in **AEC, beamforming, noise suppression, AGC, and echo‑canceling speakerphone** processing.

No custom kernel drivers are required. Plug it in, setup the device in LVA’s `config.json`, and it will “just work” as a high‑quality mic and speaker (if connected).

---

## Prerequisites

- A Linux host (e.g., Raspberry Pi 4/5) running one of:
  - Raspberry Pi OS (Bookworm or Trixie)
  - Debian 13 (trixie) or similar
  - Other modern Linux with PipeWire or PulseAudio
- Python 3.11+ recommended
- **ReSpeaker XVF3800 4‑Mic Array**, connected via its **XMOS USB‑C port**
- SSH access to your LVA instance
- Network access to the Home Assistant for registration

## Recommended Configuration

- To use the advanced LED effects, upgrade to the latest XVF3800 firmware (respeaker_xvf3800_usb_dfu_firmware_v2.0.7.bin as of this writing). 
-- https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/blob/master/xmos_firmwares/dfu_guide.md
- Tested and works on a Raspberry Pi Zero 2W, but I don't recommend attempting to flash the firmware with this device.
- PipeWire (or PulseAudio) installed and tested. I haven't tested the XVF3800 with ALSA.

---

## 1. Install system packages and Pipewire (Recommended)

```bash
sudo apt update
sudo apt upgrade
sudo apt install build-essential git \
    libmpv-dev mpv python3-dev python3-venv \
    pulseaudio-utils pipewire wireplumber \
    pipewire-audio libspa-0.2-modules libusb-1.0-0 \
    dbus-user-session
sudo reboot
```

---

## 2. Get the code (XVF3800 branch)

If you don’t already have the project cloned:

```bash
cd ~
git clone https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
```

If you already have the repository, you can:

```bash
cp ~/linux-voice-assistant/linux_voice_assistant/config.json ~/config.json
cd ~/linux-voice-assistant
git pull
cp ~/config.json ~/linux-voice-assistant/linux_voice_assistant/config.json
```

Implement the UDEV rule to prevent the XVF3800 from taking naps and to allow user access to the device

```bash
sudo cp ~/linux-voice-assistant/XVF3800/99-respeaker-xvf3800.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```


Add user to the plugdev group for access to the XVF3800 and reboot

> Tip: If you use a different username than `pi`, adjust user accordingly.

```bash
sudo usermod -aG plugdev pi
sudo reboot
```

Set up the project (creates a virtualenv, installs dependencies, etc.):

```bash
cd ~/linux-voice-assistant
script/setup
```

1. Connect the XVF3800 to your Pi/host using the **XMOS USB‑C port** (the one next to the 3.5 mm audio jack).
2. If you want local audio output, connect the XVF3800’s headphone jack or JST-PH 2.0mm 2 Pin to an external speaker.

---

## 3. Update the config.json file for XVF3800 support and options

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
  },
  "audio": {
    "volume_sync": true,
    "max_volume_percent": 150
  }
}
```

> Take a look at ~/linux-voice-assistant/linux_voice_assistant/config.json.example for details on these settings as well as all available options.

---

## 4. Start LVA as a user-mode service

The service setup is identical to other configurations (2‑Mic, etc.). Assuming you want
LVA to run as a user‑mode systemd service:

Enable linger for your user (e.g. `pi`)

```bash
sudo loginctl enable-linger pi
```

Install the systemd user service

> Tip: If you use a different username than `pi`, adjust user accordingly.

```bash
mkdir -p ~/.config/systemd/user
cp ~/linux-voice-assistant/service/linux-voice-assistant_xvf3800.service    ~/.config/systemd/user/linux-voice-assistant.service
```

Enable & start

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant.service
```

> Note: The LVA initiate a XVF3800 reboot at start, this is needed to resolve a "mic not detecting audio" issue. As a result, the LVA will take a little longer to start up and be ready.

Check status:

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

Look for log lines indicating:

- LVA connected to Home Assistant
- Wake‑word engine initialized
- Satellite streaming started on wake word

---

## 5. Acoustic Echo Cancellation (AEC)

The XVF3800 already implements **hardware AEC / echo‑canceling speakerphone** processing.

---

## 6. Troubleshooting & debug tips

### Check that LVA sees the XVF3800

Make sure the XVF3800 is available to the LVA code:

```bash
cd ~/linux-voice-assistant
script/run --list-input-devices
script/run --list-output-devices
```

If `reSpeaker XVF3800 4-Mic Array Analog Stereo` doesn’t appear:

- Check `arecord -l` / `aplay -l` again.
- Ensure the XVF3800 is connected to the XMOS USB‑C port and powered.
- Check `lsusb` for a device with `2886:001a`:

  ```bash
  lsusb | grep -i 2886
  ```

### Tail the LVA logs

```bash
journalctl --user -u linux-voice-assistant.service -f
```

Look for:

- “Audio engine started.”
- “Opening audio input device: reSpeaker XVF3800 4-Mic Array Analog Stereo”
- Wake‑word detection lines (MicroWakeWord / OpenWakeWord)
