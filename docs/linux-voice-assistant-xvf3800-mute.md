# XVF3800 Mute Button & USB Control Integration

This document explains how the **ReSpeaker XVF3800 4‑Mic USB Array** integrates its
built‑in mute button and red mute LED with the **Linux Voice Assistant (LVA)**.

The goal is:
- Pressing the XVF3800’s hardware mute button updates the LVA mic state.
- Muting/unmuting the LVA (via Home Assistant, MQTT, or other inputs) updates
  the XVF3800’s mic mute circuit and red LED.
- No extra binaries (like the vendor `xvf_host`) are required — LVA talks to
  the XVF3800 directly via **USB control transfers** using `pyusb`.

---

## 1. How it Works (High‑Level)

The XVF3800 exposes a vendor control protocol over USB that can read and write
internal GPIO pins (GPO). One of these pins drives both:

- the microphone mute circuit
- the red mute LED on the XVF3800 front panel

LVA’s `XVF3800ButtonController` does three things:

1. **Reads hardware mute state periodically**  
   It polls the XVF3800’s GPO state (pin X0D30) in a background thread using
   `pyusb`. If the value changes, LVA treats that as “the user pressed the
   XVF3800 mute button” and publishes a `set_mic_mute` event.

2. **Mirrors LVA mute → XVF3800**  
   It subscribes to `mic_muted` / `mic_unmuted` events. Whenever LVA’s mute
   state changes (for example, via MQTT, HA UI, or a GPIO button), the
   controller writes the new state back to X0D30 so that the mic mute circuit
   and red LED stay in sync.

3. **Initial sync on startup**  
   On first successful read, the controller treats the *hardware* mute state
   as the source of truth and publishes `set_mic_mute` so LVA starts aligned
   with whatever the XVF3800 is currently doing.

No PulseAudio/PipeWire configuration is required for this feature; it only
depends on **USB access to the XVF3800** and the `pyusb` library.

---

## 2. Prerequisites

- You are running a branch of **linux‑voice‑assistant** that includes:
  - `linux_voice_assistant/xvf3800_button_controller.py`
  - The updated `__main__.py` and `config.py` wiring for `XVF3800ButtonController`
- The ReSpeaker XVF3800 is connected over USB and appears in `lsusb` as:
  - `2886:001a Seeed Technology Co., Ltd. reSpeaker XVF3800 4-Mic Array`
- The LVA `.venv` includes `pyusb` (already present in `pyproject.toml`):
  ```bash
  source .venv/bin/activate
  pip install pyusb
  ```

On most systems, user‑mode processes can talk to the XVF3800 over USB without
`sudo` once the device is enumerated. If you see `USBError: [Errno 13] Access denied`,
you may need a udev rule — that’s outside this document but worth noting.

---

## 3. Enabling the XVF3800 Mute Integration

The integration is controlled via the `button` section of `config.json`.
Two “modes” are supported:

- `"gpio"`   – legacy Raspberry Pi GPIO button (ReSpeaker 2‑Mic HAT, etc.)
- `"xvf3800"` – USB‑based mute integration for the ReSpeaker XVF3800

### 3.1 Example `config.json` for XVF3800

Edit your LVA configuration file (usually `linux_voice_assistant/config.json`)
and add or adjust the `button` block like this:

```jsonc
{
  "app": {
    "name": "Linux Voice Assistant"
  },

  // ... other sections (audio, wake_word, esphome, led, mqtt, etc.) ...

  "button": {
    "enabled": true,
    "mode": "xvf3800",
    // Optional: how often to poll the XVF3800 GPO state (seconds)
    "poll_interval_seconds": 0.05
  }
}
```

Notes:

- `"enabled": true` turns on button support in general.
- `"mode": "xvf3800"` switches from the GPIO‑based button controller to the
  USB‑based XVF3800 controller.
- `poll_interval_seconds` is optional and defaults to `0.01` if omitted. Values
  between 0.02 and 0.1 are typically fine.

The GPIO‑specific fields (`pin`, `long_press_seconds`) are ignored in
`xvf3800` mode — the XVF3800’s onboard mute button is treated as a
**dedicated mute toggle only**, not a wake/stop button.

---

## 4. Runtime Behaviour

Once enabled and the LVA service is restarted, the mute integration behaves as follows:

### 4.1 Pressing the XVF3800 Mute Button

1. The XVF3800 firmware changes the GPO state of pin X0D30 and lights or clears
   the red mute LED.
2. The `XVF3800ButtonController` sees the changed GPO values the next time it
   polls USB.
3. It publishes a `set_mic_mute` event on the LVA `EventBus` with the new state.
4. The `MicMuteHandler`:
   - Updates `ServerState.mic_muted` and `mic_muted_event` (pausing/resuming the
     audio capture thread).
   - Publishes `mic_muted` / `mic_unmuted` events used by other components
     (LED controller, MQTT, etc.).
   - Notifies the MQTT controller so the HA “Mute Microphone” entity stays in sync.

From the user’s perspective:

- The red LED on the XVF3800 tracks mic mute.
- The HA “Mute Microphone” switch also tracks the hardware button.
- The audio engine actually stops capturing when muted.

### 4.2 Toggling Mute from Home Assistant / MQTT / Other Inputs

1. Home Assistant toggles the LVA “Mute Microphone” switch (or another input
   publishes a `set_mic_mute` event).
2. The `MicMuteHandler` applies the new mute state, as usual.
3. It emits a `mic_muted` or `mic_unmuted` event.
4. The `XVF3800ButtonController` receives this event and writes the new state
   back to X0D30 via USB.
5. The XVF3800’s mic mute circuit and red LED update to match LVA.

This keeps **hardware**, **LVA state**, and **Home Assistant UI** aligned.

---

## 5. Coexistence with Other Buttons

The XVF3800 mute integration is intentionally narrow in scope:

- It does **not** implement short vs. long press behaviour.
- It does **not** trigger wake‑word, stop‑word, or playback controls.
- It is purely “mute on / mute off” with bidirectional sync.

If you also have a GPIO button (e.g. a ReSpeaker 2‑Mic HAT momentary switch)
wired for wake/stop behaviour, you can:

- Leave `button.mode: "gpio"` and **not** use the XVF3800 mute integration, or
- Move wake/stop to the GPIO button controller and leave the XVF3800 button
  as mute‑only by setting `mode: "xvf3800"` and handling wake/stop elsewhere.

At the moment, `button.mode` is **single‑valued** — it selects either the
GPIO controller or the XVF3800 controller, not both at once. If you ever want
to support both concurrently, that would be a separate enhancement (e.g. a
list of button backends).

---

## 6. Troubleshooting

### 6.1 No USB Access / Permission Errors

Symptoms in the logs (journalctl or console):

```text
Failed to initialize XVF3800 USB client; mute button integration will be disabled
USBError: [Errno 13] Access denied (insufficient permissions)
```

Check:

- `lsusb` shows `2886:001a` while LVA is running.
- The LVA service is running as the user who owns the `.venv` and has
  access to USB devices.
- If necessary, add a udev rule that grants access to `idVendor=0x2886`
  and `idProduct=0x001a` for your LVA user, then reload udev and
  replug the device.

The controller will keep retrying USB initialization; once permissions are
fixed, it should connect without restarting LVA.

### 6.2 Mute Button Works but HA Entity Doesn’t Update

- Verify MQTT is enabled and configured correctly in `config.json`.
- Confirm that the HA “Mute Microphone” entity exists and belongs to the
  correct LVA device.
- Check logs for `MicMuteHandler` and `MqttController` messages when pressing
  the XVF3800 mute button.

### 6.3 HA Mute Switch Works but XVF3800 LED Doesn’t Change

- Ensure `button.enabled` is `true` and `"mode": "xvf3800"` in `config.json`.
- Look for log lines like:
  - `Initializing XVF3800ButtonController (mode=xvf3800)`
  - `Connected to ReSpeaker XVF3800 for mute control`
- If you see only GPIO logs, confirm that the branch you’re running includes
  `xvf3800_button_controller.py` and the updated `__main__.py` wiring.

---

## 7. Summary

With `mode: "xvf3800"`, the Linux Voice Assistant treats the ReSpeaker
XVF3800’s onboard mute button and red LED as a **first‑class mute control**:

- Pressing the XVF3800 mute button updates LVA and Home Assistant.
- Muting via Home Assistant or other inputs updates the XVF3800 hardware.
- The audio engine, LEDs, and MQTT entities stay in sync with the device’s
  actual hardware mute state.

This integration is fully optional and coexists with existing audio and LED
behaviour — it simply adds a reliable, hardware‑based mute path for the
XVF3800‑based satellites.
