# Linux Voice Assistant with ReSpeaker XVF3800 4‑Mic Array — Installation & Configuration Guide

> Drafted with ChatGPT based on existing `linux-voice-assistant-2mic-install.md` and
> community test results from the XVF3800 integration Discussion.

This guide shows how to use the **Seeed ReSpeaker XVF3800 USB 4‑Mic Array** with the
**linux-voice-assistant** (LVA) project on Raspberry Pi or other Linux hosts.

Unlike the ReSpeaker 2‑Mic HAT, the XVF3800 is a **USB Audio Class 2.0** device:
it acts as a “smart microphone + speaker” front end with built‑in **AEC, beamforming,
noise suppression, AGC, and echo‑canceling speakerphone** processing.

LVA treats the XVF3800 as a **standard USB mic/speaker**:
- Audio in: 16 kHz, 16‑bit, 2‑channel PCM (downmixed to mono by the audio stack)
- Audio out: 16 kHz, 16‑bit, 2‑channel PCM via the XVF3800 headphone jack / speaker

No custom kernel drivers are required. Plug it in, select it as your audio device in
LVA’s `config.json`, and it will “just work” as a high‑quality mic for wake word and
ESPHome streaming.

---

## Prerequisites

- A Linux host (e.g., Raspberry Pi 4/5) running one of:
  - Raspberry Pi OS (Bookworm or Trixie)
  - Debian 13 (trixie) or similar
  - Other modern Linux with PipeWire or PulseAudio
- Python 3.11+ recommended
- **ReSpeaker XVF3800 4‑Mic Array**, connected via its **XMOS USB‑C port**
- Network access to your Home Assistant instance
- The `linux-voice-assistant` repository checked out (optionally on the `XVF3800` branch)

---

## 1. Get the code (XVF3800 branch)

If you don’t already have the project cloned, you can pull the dedicated XVF3800 branch:

```bash
cd ~
git clone --branch XVF3800 --single-branch https://github.com/imonlinux/linux-voice-assistant.git
cd linux-voice-assistant
```

If you already have the repository, you can switch branches:

```bash
cd ~/linux-voice-assistant
git fetch origin
git checkout XVF3800
```

Set up the project (creates a virtualenv, installs dependencies, etc.):

```bash
cd ~/linux-voice-assistant
script/setup
```

> Tip: If you use a different username than `pi`, adjust paths accordingly.

---

## 2. Connect and verify the XVF3800

### 2.1 Plug in the board

1. Connect the XVF3800 to your Pi/host using the **XMOS USB‑C port** (the one next to
   the 3.5 mm audio jack).
2. If you want local audio output, connect the XVF3800’s headphone jack to an external
   powered speaker.

### 2.2 Confirm ALSA sees the device

```bash
arecord -l
aplay   -l
```

You should see entries similar to:

```text
**** List of CAPTURE Hardware Devices ****
card 3: Array [reSpeaker XVF3800 4-Mic Array], device 0: USB Audio [USB Audio]
  Subdevices: 0/1
  Subdevice #0: subdevice #0

**** List of PLAYBACK Hardware Devices ****
card 3: Array [reSpeaker XVF3800 4-Mic Array], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
```

The exact card index (`3` above) may differ on your system.

### 2.3 Quick `arecord` test (optional)

Replace `3` with your card number:

```bash
arecord -D plughw:3,0 -c 2 -r 16000 -f S16_LE -d 5 -vv /tmp/xvf3800-test.wav
aplay /tmp/xvf3800-test.wav
```

You should hear your voice through your default playback device, confirming that:
- The XVF3800 is streaming at **16 kHz**, **16‑bit**, **stereo**, and
- ALSA can capture from it reliably.

---

## 3. PipeWire / PulseAudio device mapping

On a PipeWire or PulseAudio system, check how the XVF3800 shows up at the graph level:

```bash
pactl list short sources
pactl list short sinks
```

Typical output:

```text
# Sources
309 alsa_input.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_...-00.analog-stereo PipeWire s16le 2ch 16000Hz RUNNING

# Sinks
308 alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_...-00.analog-stereo PipeWire s16le 2ch 16000Hz SUSPENDED
```

Key points:

- The XVF3800 appears as an **analog stereo** source/sink (`2ch 16000 Hz`).
- LVA will use these devices through the `soundcard` library.

---

## 4. LVA’s view of the XVF3800 (input/output devices)

From the project root, use LVA’s helper flags to inspect devices:

```bash
cd ~/linux-voice-assistant
script/run -- --list-input-devices
script/run -- --list-output-devices
```

Example output:

```text
Input devices

[0] reSpeaker XVF3800 4-Mic Array Analog Stereo
```

```text
Output devices

auto: Autoselect device
pipewire: Default (pipewire)
pipewire/alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_...analog-stereo: reSpeaker XVF3800 4-Mic Array Analog Stereo
...
```

Take note of:

- The **input device name** for the XVF3800 (e.g. `reSpeaker XVF3800 4-Mic Array Analog Stereo`)
- The output device you want to use (often `pipewire` or the specific XVF “analog‑stereo” sink)

---

## 5. Configure LVA audio for XVF3800

Edit the main config:

```bash
nano ~/linux-voice-assistant/linux_voice_assistant/config.json
```

Add or update the `audio` section:

```json
"audio": {
  "input_device": "reSpeaker XVF3800 4-Mic Array Analog Stereo",
  "output_device": "pipewire",
  "input_block_size": 1024
}
```

Examples:

- If you want both input and output on the XVF3800:

  ```json
  "audio": {
    "input_device": "reSpeaker XVF3800 4-Mic Array Analog Stereo",
    "output_device": "pipewire/alsa_output.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_101991441253700076-00.analog-stereo",
    "input_block_size": 1024
  }
  ```

- If you only want to use the XVF3800 as a **mic** (and another device for playback), set
  `output_device` to your preferred sink from `--list-output-devices`.

### 5.1 How channels are handled (important detail)

The XVF3800 exposes itself as a **2‑channel** source (stereo, 16 kHz). Internally, the
LVA `AudioEngine` uses the `soundcard` library and **always requests 1 channel** from the
selected input device:

```python
with self.mic.recorder(
    samplerate=16000,
    channels=1,
    blocksize=self.block_size,
) as mic_in:
    audio_chunk_array = mic_in.record(self.block_size).reshape(-1)
    ...
```

This has two important implications:

1. The audio stack (PipeWire/PulseAudio/ALSA) takes care of converting from the XVF3800’s
   **2‑channel** stream to a **mono** stream before LVA sees it.
2. LVA’s wake‑word engines and ESPHome streaming always receive 16 kHz **mono** PCM
   (`int16`), regardless of whether the underlying hardware is mono or stereo.

In practice:

> You do **not** need any XVF3800‑specific code changes in the audio engine.  
> Selecting the XVF3800 as the `audio.input_device` is sufficient; LVA will process it
> as a mono mic just like other devices.

---

## 6. Start LVA as a user-mode service

The service setup is identical to other configurations (2‑Mic, etc.). Assuming you want
LVA to run as a user‑mode systemd service:

### 6.1 Enable linger for your user (e.g. `pi`)

```bash
sudo loginctl enable-linger pi
```

### 6.2 Install the systemd user service

```bash
mkdir -p ~/.config/systemd/user
cp ~/linux-voice-assistant/service/linux-voice-assistant.service    ~/.config/systemd/user/linux-voice-assistant.service
```

### 6.3 Enable & start

```bash
systemctl --user daemon-reload
systemctl --user enable --now linux-voice-assistant.service
```

Check status:

```bash
systemctl --user status linux-voice-assistant --no-pager -l
```

Look for log lines indicating:

- LVA connected to Home Assistant
- Wake‑word engine initialized
- Satellite streaming started on wake word

---

## 7. Acoustic Echo Cancellation (AEC) considerations

The XVF3800 already implements **hardware AEC / echo‑canceling speakerphone** processing.
In many cases, you can rely **only** on the XVF3800’s built‑in AEC and **skip** the
PipeWire/PulseAudio `module-echo-cancel` setup.

You have three practical options:

### 7.1 Rely on XVF3800 AEC only (recommended starting point)

- Do **not** load any user‑mode echo‑cancel module.
- Set `input_device` to the XVF3800 source (or an echo‑cancel source that wraps it, if your
  system has one by default).
- Play media (music, games, etc.) through the XVF3800 or another output and test:
  - Wake word recognition reliability
  - STT quality in noisy playback conditions

If it works well, this is the simplest and most CPU‑efficient setup.

### 7.2 Combine XVF3800 with software AEC

If you already have a PipeWire/PulseAudio echo‑cancel device configured, you can route
XVF3800 through it:

1. Create an echo‑cancel source/sink pair (see `install_pipewire.md` or
   `install_pulseaudio.md`).
2. Use `script/run --list-input-devices` / `--list-output-devices` to find names like
   `Echo-Cancel Source` and `pipewire/echo-cancel-sink`.
3. Point LVA at those devices in `config.json`:

   ```json
   "audio": {
     "input_device": "Echo-Cancel Source",
     "input_block_size": 1024,
     "output_device": "pipewire/echo-cancel-sink"
   }
   ```

This stacks XVF3800’s hardware processing with additional software AEC. It may help in
particularly difficult acoustic environments, at the cost of some complexity and CPU.

### 7.3 No AEC at all (not recommended for open‑air speakers)

You can also run without any AEC (hardware or software) if:

- You use **headphones** instead of speakers, or
- The device isn’t playing audio while listening (e.g. strictly microphone‑only role).

In that case, you can treat XVF3800 as “just another USB mic” and ignore its speakerphone
capabilities.

---

## 8. Troubleshooting & debug tips

### 8.1 Check that LVA sees the XVF3800

Re‑run:

```bash
cd ~/linux-voice-assistant
script/run -- --list-input-devices
script/run -- --list-output-devices
```

If `reSpeaker XVF3800 4-Mic Array Analog Stereo` doesn’t appear:

- Check `arecord -l` / `aplay -l` again.
- Ensure the XVF3800 is connected to the XMOS USB‑C port and powered.
- Check `lsusb` for a device with `2886:001a`:

  ```bash
  lsusb | grep -i 2886
  ```

### 8.2 Tail the LVA logs

```bash
journalctl --user -u linux-voice-assistant.service -f
```

Look for:

- “Audio engine started.”
- “Opening audio input device: reSpeaker XVF3800 4-Mic Array Analog Stereo”
- Wake‑word detection lines (MicroWakeWord / OpenWakeWord)

### 8.3 Optional: XVF3800 probe script

On the `XVF3800` branch, there is a small probe script in `tests/xvf3800_probe.py` that
can be used to inspect how the audio backend opens the device.

For example:

```bash
cd ~/linux-voice-assistant
python tests/xvf3800_probe.py --device "reSpeaker XVF3800 4-Mic Array Analog Stereo"
```

This will print:

- The device list from the `soundcard` backend
- The samplerate and channel count used when opening the stream
- The shape and dtype of the recorded buffer

It’s mainly a debug tool for development and Discussion threads; you don’t need it for
normal operation.

---

## 9. Next steps: LEDs, button, and DOA integration

This document covers **audio‑only** integration for the XVF3800. The board also has:

- A 12‑LED WS2812 ring
- A hardware mute button and mute LED
- Direction‑of‑arrival (DOA) and voice activity indicators

Future work in the LVA project may add an optional `Xvf3800Controller` that:

- Talks to the XVF3800 over USB using a Python SDK (pyusb or HID)
- Publishes DOA, VAD, and mute button events to LVA’s internal event bus
- Synchronizes mute state and LED behavior between XVF3800, LVA, and Home Assistant

For now, the important takeaway is:

> As soon as the XVF3800 is selected as the input device in `config.json`, the Linux
> Voice Assistant can use it as a high‑quality microphone front end with no additional
> code changes.
