# XVF3800 Legacy LED Effects Mapping for Linux Voice Assistant

This document maps the **legacy ReSpeaker XVF3800 LED_EFFECT modes** to the higher-level LED effects used by the **linux-voice-assistant (LVA)**.

## XVF3800 Legacy LED_EFFECT Values

From the Seeed/XMOS documentation:

- `LED_EFFECT 0` → **off**
- `LED_EFFECT 1` → **breath**
- `LED_EFFECT 2` → **rainbow**
- `LED_EFFECT 3` → **single color**
- `LED_EFFECT 4` → **DoA mode**

## LVA Effects → XVF3800 Mapping

LVA exposes higher-level effects such as **Off / Solid / Slow/Medium/Fast Pulse / Slow/Medium/Fast Blink / Spin**.  
On XVF3800 (legacy firmware), these are currently mapped onto the limited built-in modes as follows:

| LVA Effect Name  | XVF3800 `LED_EFFECT` | Additional Parameters                | Behavior on XVF3800 (Legacy)                                           |
|------------------|----------------------|--------------------------------------|-------------------------------------------------------------------------|
| `off`            | `0` (off)            | –                                    | LED ring completely off.                                               |
| `solid`          | `3` (single color)   | `LED_COLOR`, `LED_BRIGHTNESS`        | Full ring in a single static color.                                    |
| `slow_pulse`     | `1` (breath)         | `LED_SPEED` = slow                   | Slow breathing in the configured color.                                |
| `medium_pulse`   | `1` (breath)         | `LED_SPEED` = medium                 | Medium breathing in the configured color.                              |
| `fast_pulse`     | `1` (breath)         | `LED_SPEED` = fast                   | Fast breathing in the configured color.                                |
| `slow_blink`     | `1` (breath) *       | `LED_SPEED` = slow                   | Approximated with slow breath (no true sharp on/off blink).           |
| `medium_blink`   | `1` (breath) *       | `LED_SPEED` = medium                 | Approximated with medium breath.                                       |
| `fast_blink`     | `1` (breath) *       | `LED_SPEED` = fast                   | Approximated with fast breath.                                         |
| `spin`           | `3` or `1` **        | Typically treated as solid/pulsed    | No native “spin”; currently falls back to a static or pulsed behavior. |

\* There is no dedicated “blink” primitive in the legacy API, so all blink variants are currently rendered as **breathing** with different speeds. They will look like fading rather than a true square‑wave blink until we add a more advanced mapping or per‑LED pattern logic.

\** The legacy firmware does not offer a true “spin” animation. For now, `spin` is effectively treated as either **single color** or a **breath** variant to keep behavior predictable.

## Practical Notes

- Under the legacy backend, **only three real modes** are used:  
  - `0` → off  
  - `1` → breath (with speed control)  
  - `3` → single color  
- All the LVA **pulse** and **blink** variants are essentially **different speeds of `LED_EFFECT=1`**.
- The **spin** effect is not yet a genuine rotating animation on XVF3800; it’s a placeholder mapped onto existing firmware modes.

As we move toward **firmware v2.0.7+** and `LED_RING_COLOR` support, we’ll be able to implement richer, per‑LED patterns (including real spin and more accurate blink behaviors) while keeping the same high-level LVA effect names.
