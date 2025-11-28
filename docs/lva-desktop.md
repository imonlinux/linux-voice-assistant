# Want to run LVA on a Linux Graphical Desktop?

### 1. Install the dependancies:

### Debian based distro:

```bash
sudo apt update
sudo apt install \
  libmpv-dev git python3-dev mpv
```

### Fedora based distro:

```bash
sudo dnf install \
  mpv-devel python3-devel git mpv
```

### Arch based distro:

```bash
sudo pacman -Syu \
  python-pip python-virtualenv mpv
```

### 2. Open Firewall for LVA:

### UFW:

```bash
sudo ufw allow 6053/udp
sudo ufw allow 6053/tcp
sudo ufw reload
```

### Firewall-CMD:

```bash
sudo firewall-cmd --permanent --zone=public --add-port=6053/tcp
sudo firewall-cmd --permanent --zone=public --add-port=6053/udp
sudo firewall-cmd --reload
```

### 3. Get the code:

```bash
git clone https://github.com/imonlinux/linux-voice-assistant.git
```

### 4. Setup Linux Voice Assistant (LVA):

```bash
cd linux-voice-assistant/
chmod +x script/tray
script/setup
```

### 5. Modify config.json File:

```bash
nano linux_voice_assistant/config.json
```

Change the LVA name and add the details for MQTT (MQTT is required for the LVA Tray Client)
***Update the MQTT details to match your system!***

```bash
{
  "app": {
    "name": "Debian Voice Assistant"
  },
  "mqtt": {
    "host": "192.168.1.2",
    "port": 1883,
    "username": "mqtt-user",
    "password": "mqtt-password"
  }
}
```

### 6. Create LVA User Mode Systemd Unit file:

```bash
mkdir -p ~/.config/systemd/user

cp service/linux-voice-assistant.service ~/.config/systemd/user/linux-voice-assistant.service

```

### Edit the LVA Unit file for correct path:

```bash
systemctl --user edit --force --full linux-voice-assistant.service
```
### Enter the correct path for WorkingDirectory and ExecStart

```bash
[Unit]
Description=Linux Voice Assistant

[Service]
Type=simple
WorkingDirectory=/home/pi/linux-voice-assistant
Environment=PYTHONUNBUFFERED=1

ExecStart=/home/pi/linux-voice-assistant/script/run

Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

### 7. Create the LVA Tray Client User Mode Systemd Unit file:

```bash
cp service/linux-voice-assistant-tray.service ~/.config/systemd/user/linux-voice-assistant-tray.service
```

### Edit the LVA Tray Unit file for correct path:

```bash
systemctl --user edit --force --full linux-voice-assistant-tray.service
```

### Enter the correct path for WorkingDirectory and ExecStart

```bash
[Unit]
Description=Linux Voice Assistant Tray Client
# Make sure we have a graphical session
After=graphical-session.target
Wants=graphical-session.target

[Service]
Type=simple
WorkingDirectory=/home/james/Workbench/linux-voice-assistant
ExecStart=/home/james/Workbench/linux-voice-assistant/script/tray
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

### Start only the LVA Tray Application

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant-tray.service
```

This will add an icon to your taskbar. Click on the icon and select "Start LVA".

Follow the instructions in HA to register the LVA.

Use the MQTT device that is created in HA to adjust the icon color for each of the LVA states.
