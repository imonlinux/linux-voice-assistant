# Call for Testing: XVF3800 USB LED Support in `linux-voice-assistant`

The XVF3800 branch now includes **USB-based LED ring support** using the XVF3800’s legacy LED control commands (`LED_EFFECT`, `LED_BRIGHTNESS`, `LED_SPEED`, `LED_COLOR`). This post outlines how to pull the branch, refresh your virtualenv, and enable XVF3800 support in `config.json`.

---

## 1. Get the XVF3800 Branch

If you **already have** the repo:

```bash
cd ~/linux-voice-assistant
git fetch origin
git switch xvf3800      # or: git checkout xvf3800
git pull
```

If you’re **cloning fresh**:

```bash
cd ~
git clone https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
git switch xvf3800      # or: git checkout xvf3800
```

---

## 2. Refresh the Virtualenv (New Dependencies)

The XVF3800 backend uses **pyusb** and system libusb.

From the project root:

```bash
cd ~/linux-voice-assistant

# Create venv if you don't already have one
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Upgrade pip and reinstall project deps (including pyusb)
pip install --upgrade pip
pip install -e .
```

On Debian/RPi OS, make sure libusb is installed:

```bash
sudo apt update
sudo apt install libusb-1.0-0
```

No extra `hidapi` is required for the LED integration (this path is pure pyusb).

---

## 3. Enable XVF3800 Support in `config.json`

Edit `linux_voice_assistant/config.json` and set the `led` block to use the XVF3800 USB backend.

Minimal example:

```jsonc
{
  "app": {
    "name": "Linux Voice Assistant XVF3800"
  },

  "audio": {
    // Set this to your XVF3800 input device name or index if needed
    // "input_device": "reSpeaker XVF3800 4-Mic Array",
    "input_block_size": 1024
  },

  "led": {
    // New: XVF3800 USB LED backend
    // led_type can be "dotstar", "neopixel" or "xvf3800"
    "led_type": "xvf3800",

    // For XVF3800 we currently support only "usb"
    "interface": "usb",

    // Used for internal logic / MQTT, not hardware-driven on XVF3800
    "num_leds": 12,

    // Optional (defaults to true if omitted)
    "enabled": true
  },

  // If you previously used the 2-Mic HAT button, disable it here
  "button": {
    "enabled": false
  },

  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  }
}
```

> ✅ For non-XVF3800 setups, keep using `led_type: "dotstar"` / `"neopixel"` and your existing `interface` (`spi` or `gpio`).

---

## 4. Run the Assistant with XVF3800 LEDs

From the project root:

```bash
cd ~/linux-voice-assistant
source .venv/bin/activate
script/run --debug
```

With the XVF3800 branch + config above, the **USB LED ring** should now:

- Show the configured **idle / listening / thinking / responding / error** effects
- Turn **solid dim red** when the mic is muted
- Track changes coming from the MQTT LED state entities

Please report back:

- Which distro / Pi model you’re using
- Firmware version on the XVF3800 (if known)
- What you see on the LED ring for each LVA state  
  (idle, listening, thinking, responding, error, muted)

This will help tighten the mappings and prepare for a follow-up pass that uses the newer `LED_RING_COLOR` capabilities.
