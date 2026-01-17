Here are the **required accuracy fixes** to `docs/lva-desktop.md` based on the changes we made (tray now uses `load_config_from_json`, `tray.systemd_service_name`, venv-aware scripts, and MQTT is required for tray functionality).

### What needs changing

1. **Add `tray` config section (systemd service name)**

* Your tray client now reads `config.tray.systemd_service_name` (defaulting to `linux-voice-assistant.service`), so the doc should mention it and show it in the example config (optional, but recommended for clarity).

2. **Clarify that the tray requires MQTT to be enabled (it already does)**

* Keep your warning, but make it explicit that the tray client will fail to start if MQTT is disabled / host missing.

3. **System dependencies for PyQt5**

* `pip install PyQt5` often needs Qt/XCB related system libs on some distros (especially minimal installs). Your doc currently doesn’t mention them. I can’t guarantee which ones you want to standardize on for each distro, but adding a short “if PyQt5 fails” troubleshooting note is accurate and saves time.

4. **Systemd tray unit `After=` target**

* `graphical-session.target` exists in some environments but not all. A more common approach is `After=graphical.target` or `After=default.target` plus `Environment=DISPLAY=...` etc.
  That said: since you’re copying an existing `service/linux-voice-assistant-tray.service`, the doc should not prescribe targets that might differ from your shipped unit. The doc should instead say: “copy the provided unit and edit WorkingDirectory/ExecStart”.

5. **Color list**

* Still accurate. Idle “purple” is a fallback. You also note that idle uses MQTT color; your current doc still implies fixed purple. You should adjust the Idle bullet to: “Idle (default purple unless set from HA light entity)”.

---

## Entire modified `docs/lva-desktop.md`

````markdown
# Want to run LVA on a Linux Graphical Desktop?

This guide covers installing the Linux Voice Assistant (LVA) on a desktop Linux distribution (Debian, Fedora, Arch) and setting up the System Tray Client.

### 1. Install System Dependencies

You will need Python headers, `mpv` (for audio playback), and `git`.

**Debian / Ubuntu / Raspberry Pi OS:**
```bash
sudo apt update
sudo apt install \
  libmpv-dev git python3-dev mpv python3-venv
````

**Fedora:**

```bash
sudo dnf install \
  mpv-devel python3-devel git mpv
```

**Arch Linux:**

```bash
sudo pacman -Syu \
  python python-pip mpv
```

> Note: The tray client uses PyQt5 (installed via `script/setup --tray`). If PyQt5 fails to import or the tray icon does not appear on minimal desktops, you may need additional Qt/X11 system packages for your distro.

### 2. Configure Firewall

LVA uses port `6053` (TCP/UDP) for the ESPHome API. If you have a firewall enabled, you must allow this port so Home Assistant can connect.

**UFW (Ubuntu/Debian):**

```bash
sudo ufw allow 6053/udp
sudo ufw allow 6053/tcp
sudo ufw reload
```

**FirewallD (Fedora/CentOS):**

```bash
sudo firewall-cmd --permanent --zone=public --add-port=6053/tcp
sudo firewall-cmd --permanent --zone=public --add-port=6053/udp
sudo firewall-cmd --reload
```

### 3. Get the Code

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
```

### 4. Install LVA

We will set up a virtual environment and install the package with the optional `[tray]` dependencies (which installs PyQt5).

```bash
# Run the setup script (creates .venv and installs core deps + tray extras)
script/setup --tray
```

### 5. Configure LVA

Create your configuration file:

```bash
nano linux_voice_assistant/config.json
```

**Important:** For the Tray Client to show status colors (listening, thinking, etc.) and control the mute state, **MQTT is required**.

* The tray client requires `mqtt.host` to be set.
* If MQTT is disabled or missing, the tray client will not be able to show online/offline status, state colors, or mute control.

Update the example below with your specific MQTT broker details:

```json
{
  "app": {
    "name": "My Desktop Assistant"
  },
  "mqtt": {
    "host": "192.168.1.X",
    "port": 1883,
    "username": "your_mqtt_user",
    "password": "your_mqtt_password"
  },
  "tray": {
    "systemd_service_name": "linux-voice-assistant.service"
  }
}
```

> `tray.systemd_service_name` is the service that the tray menu will start/stop/restart.

### 6. Set up Systemd Services

We will run LVA as a user-level service so it has access to your user audio session (PulseAudio/PipeWire).

#### A. Main LVA Service

```bash
mkdir -p ~/.config/systemd/user
cp service/linux-voice-assistant.service ~/.config/systemd/user/
```

Edit the service to match your installation path:

```bash
systemctl --user edit --force --full linux-voice-assistant.service
```

**Replace `/path/to/linux-voice-assistant` with your actual directory (e.g., `/home/yourname/linux-voice-assistant`):**

```ini
[Unit]
Description=Linux Voice Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# UPDATE THIS PATH:
WorkingDirectory=/path/to/linux-voice-assistant
Environment=PYTHONUNBUFFERED=1

# UPDATE THIS PATH:
ExecStart=/path/to/linux-voice-assistant/script/run

Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

#### B. Tray Client Service

```bash
cp service/linux-voice-assistant-tray.service ~/.config/systemd/user/
```

Edit the tray service to match your installation path:

```bash
systemctl --user edit --force --full linux-voice-assistant-tray.service
```

**Again, update the paths:**

```ini
[Unit]
Description=Linux Voice Assistant Tray Client

[Service]
Type=simple
# UPDATE THIS PATH:
WorkingDirectory=/path/to/linux-voice-assistant

# UPDATE THIS PATH:
ExecStart=/path/to/linux-voice-assistant/script/tray

Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

> The repository ships a tray unit file in `service/linux-voice-assistant-tray.service`. Prefer copying and editing that file as shown above, rather than hand-authoring targets that may differ between desktop environments.

### 7. Start the Application

You only need to enable and start the **Tray Client**. The Tray Client has a menu option to start/stop the main LVA background service for you.

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant-tray.service
```

### 8. Using the Tray Client

1. **Icon:** You should see a small circle icon in your system tray.

* **Grey:** Offline / LVA Service not running.
* **Idle:** Idle (Connected). Default is purple unless you change the Idle color via Home Assistant.
* **Blue:** Listening.
* **Yellow:** Thinking (Processing).
* **Green:** Responding (TTS).
* **Orange:** Error.
* **Red Tint:** Microphone Muted.

2. **Menu:** Left-click or Right-click the icon to:

* **Start LVA:** Launches the main background service.
* **Mute Microphone:** Toggles the software mute state.
* **Restart/Stop LVA:** Controls the background service.

3. **Home Assistant Integration:**

* Once LVA connects to MQTT, it will discover several entities in Home Assistant.
* You can change the LED/icon colors for each state (Idle, Listening, etc.) directly from Home Assistant using the `Light` entities created by LVA (e.g., `light.my_desktop_assistant_idle_color`). The tray icon will update instantly to match these settings.

```

---

### Only remaining “unknown” (but worth checking)
Your doc assumes the tray systemd unit exists at `service/linux-voice-assistant-tray.service`. If that filename differs in your repo, update the `cp ...` line accordingly.

If you paste `service/linux-voice-assistant-tray.service` I can verify the unit’s targets/requirements and make sure the doc matches it exactly.
```
# Want to run LVA on a Linux Graphical Desktop?

This guide covers installing the Linux Voice Assistant (LVA) on a desktop Linux distribution (Debian, Fedora, Arch) and setting up the System Tray Client.

### 1. Install System Dependencies

You will need Python headers, `mpv` (for audio playback), and `git`.

**Debian / Ubuntu / Raspberry Pi OS:**
```bash
sudo apt update
sudo apt install \
  libmpv-dev git python3-dev mpv python3-venv
````

**Fedora:**

```bash
sudo dnf install \
  mpv-devel python3-devel git mpv
```

**Arch Linux:**

```bash
sudo pacman -Syu \
  python python-pip mpv
```

> Note: The tray client uses PyQt5 (installed via `script/setup --tray`). If PyQt5 fails to import or the tray icon does not appear on minimal desktops, you may need additional Qt/X11 system packages for your distro.

### 2. Configure Firewall

LVA uses port `6053` (TCP/UDP) for the ESPHome API. If you have a firewall enabled, you must allow this port so Home Assistant can connect.

**UFW (Ubuntu/Debian):**

```bash
sudo ufw allow 6053/udp
sudo ufw allow 6053/tcp
sudo ufw reload
```

**FirewallD (Fedora/CentOS):**

```bash
sudo firewall-cmd --permanent --zone=public --add-port=6053/tcp
sudo firewall-cmd --permanent --zone=public --add-port=6053/udp
sudo firewall-cmd --reload
```

### 3. Get the Code

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
```

### 4. Install LVA

We will set up a virtual environment and install the package with the optional `[tray]` dependencies (which installs PyQt5).

```bash
# Run the setup script (creates .venv and installs core deps + tray extras)
script/setup --tray
```

### 5. Configure LVA

Create your configuration file:

```bash
nano linux_voice_assistant/config.json
```

**Important:** For the Tray Client to show status colors (listening, thinking, etc.) and control the mute state, **MQTT is required**.

* The tray client requires `mqtt.host` to be set.
* If MQTT is disabled or missing, the tray client will not be able to show online/offline status, state colors, or mute control.

Update the example below with your specific MQTT broker details:

```json
{
  "app": {
    "name": "My Desktop Assistant"
  },
  "mqtt": {
    "host": "192.168.1.X",
    "port": 1883,
    "username": "your_mqtt_user",
    "password": "your_mqtt_password"
  },
  "tray": {
    "systemd_service_name": "linux-voice-assistant.service"
  }
}
```

> `tray.systemd_service_name` is the service that the tray menu will start/stop/restart.

### 6. Set up Systemd Services

We will run LVA as a user-level service so it has access to your user audio session (PulseAudio/PipeWire).

#### A. Main LVA Service

```bash
mkdir -p ~/.config/systemd/user
cp service/linux-voice-assistant.service ~/.config/systemd/user/
```

Edit the service to match your installation path:

```bash
systemctl --user edit --force --full linux-voice-assistant.service
```

**Replace `/path/to/linux-voice-assistant` with your actual directory (e.g., `/home/yourname/linux-voice-assistant`):**

```ini
[Unit]
Description=Linux Voice Assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
# UPDATE THIS PATH:
WorkingDirectory=/path/to/linux-voice-assistant
Environment=PYTHONUNBUFFERED=1

# UPDATE THIS PATH:
ExecStart=/path/to/linux-voice-assistant/script/run

Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

#### B. Tray Client Service

```bash
cp service/linux-voice-assistant-tray.service ~/.config/systemd/user/
```

Edit the tray service to match your installation path:

```bash
systemctl --user edit --force --full linux-voice-assistant-tray.service
```

**Again, update the paths:**

```ini
[Unit]
Description=Linux Voice Assistant Tray Client

[Service]
Type=simple
# UPDATE THIS PATH:
WorkingDirectory=/path/to/linux-voice-assistant

# UPDATE THIS PATH:
ExecStart=/path/to/linux-voice-assistant/script/tray

Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

> The repository ships a tray unit file in `service/linux-voice-assistant-tray.service`. Prefer copying and editing that file as shown above, rather than hand-authoring targets that may differ between desktop environments.

### 7. Start the Application

You only need to enable and start the **Tray Client**. The Tray Client has a menu option to start/stop the main LVA background service for you.

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant-tray.service
```

### 8. Using the Tray Client

1. **Icon:** You should see a small circle icon in your system tray.

* **Grey:** Offline / LVA Service not running.
* **Idle:** Idle (Connected). Default is purple unless you change the Idle color via Home Assistant.
* **Blue:** Listening.
* **Yellow:** Thinking (Processing).
* **Green:** Responding (TTS).
* **Orange:** Error.
* **Red Tint:** Microphone Muted.

2. **Menu:** Left-click or Right-click the icon to:

* **Start LVA:** Launches the main background service.
* **Mute Microphone:** Toggles the software mute state.
* **Restart/Stop LVA:** Controls the background service.

3. **Home Assistant Integration:**

* Once LVA connects to MQTT, it will discover several entities in Home Assistant.
* You can change the LED/icon colors for each state (Idle, Listening, etc.) directly from Home Assistant using the `Light` entities created by LVA (e.g., `light.my_desktop_assistant_idle_color`). The tray icon will update instantly to match these settings.

```

---

### Only remaining “unknown” (but worth checking)
Your doc assumes the tray systemd unit exists at `service/linux-voice-assistant-tray.service`. If that filename differs in your repo, update the `cp ...` line accordingly.

If you paste `service/linux-voice-assistant-tray.service` I can verify the unit’s targets/requirements and make sure the doc matches it exactly.
```
