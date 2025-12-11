# XVF3800 USB LED and Mute Button Support in `linux-voice-assistant`

The XVF3800 branch now includes **USB-based LED ring support** using the XVF3800’s legacy LED control commands (`LED_EFFECT`, `LED_BRIGHTNESS`, `LED_SPEED`, `LED_COLOR`). This post outlines how to pull the branch, refresh your virtualenv, and enable XVF3800 support in `config.json`. I fully expect bugs and you should too. Let me know if it works first try and I will by some lottery tickets as well.

---

## 1. Get the XVF3800 Branch

If you **already have** the repo:

```bash
cd ~/linux-voice-assistant
git fetch origin
git switch XVF3800      # or: git checkout xvf3800
git pull
```

If you’re **cloning fresh**:

```bash
cd ~
git clone --branch XVF3800 --single-branch https://github.com/imonlinux/linux-voice-assistant.git
```

---

## 2. Install/Refresh the Virtualenv (New Dependencies)

The XVF3800 backend uses **pyusb** and system libusb.

From the project root:

```bash
cd ~/linux-voice-assistant

# remove the linux_voice_assistant.egg-info folder if it exists
rm -rf linux_voice_assistant.egg-info

# Create venv if you don't already have one
script/setup
```
On Debian/RPi OS, make sure libusb is installed:


```bash
sudo apt update
sudo apt install libusb-1.0-0
```

No extra `hidapi` is required for the LED integration (this path is pure pyusb).

## 3. Copy the udev rule for the XVF3800

On the Pi:

```bash
sudo cp ~linux-voice-assistant/XVF3800/99-respeaker-xvf3800.rules /etc/udev/rules.d/99-respeaker-xvf3800.rules
```

This does:

- Matches the XVF3800 by VID/PID (2886:001a)

- Sets permissions to 0660

- Assigns it to the plugdev group

- Tags it for desktop access (uaccess)

### Add user (pi) to the plugdev group
**use the user name that you have created if not pi**

```bash
sudo usermod -aG plugdev pi
```

Then log out and back in (or reboot) so the new group membership takes effect.

### Reload udev rules and replug the device

If you didn't reboot, reload the udev rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Then unplug/replug the XVF3800 USB cable.

You can sanity-check permissions with:

```bash
ls -l /dev/bus/usb/*/*
```

Look for the device entry that corresponds to 2886:001a (you can pair it with lsusb), and confirm it’s root plugdev with crw-rw----.


## 4. Enable XVF3800 Support in `config.json`

Edit `linux_voice_assistant/config.json` and set the `led` block to use the XVF3800 USB backend. If you want to test the mute button, add the following button section as well. See the `linux_voice_assistant/config.json.example` file for additional options.

Minimal example:

```jsonc
{
  "app": {
    "name": "Linux Voice Assistant XVF3800"
  },
  "led": {
    "enabled": true,
    "led_type": "xvf3800",
    "interface": "usb"
  },
  "button": {
    "enabled": true,
    "mode": "xvf3800"
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt_username",
    "password": "mqtt_password"
  }
}
```

---

## 5. Run the Assistant with XVF3800 LEDs

Stop any running systemd units:

```bash
systemctl --user stop linux-voice-assistant.service
```


From the project root:

```bash
cd ~/linux-voice-assistant
script/run --debug
```

With the XVF3800 branch + config above, the **USB LED ring** should now:

- Show the configured **idle / listening / thinking / responding / error** effects
- Turn **solid dim red** when the mic is muted
- Track changes coming from the MQTT LED state entities

The mute button should now:

- Keep hardware mute & software/LVA mute fully in sync.
- No short/long press differentiation:
	One physical control, one responsibility: mute/unmute.

`Wake/stop is still done via wake word or a separate GPIO button (you can still wire a standalone Pi button and run mode="gpio" in parallel if desired).`
