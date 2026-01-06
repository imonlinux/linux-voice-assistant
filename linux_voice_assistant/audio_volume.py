"""System output volume helpers.

LVA historically only adjusted mpv's per-player volume. On PipeWire/PulseAudio
setups where the default sink volume starts low (e.g. 40%), HA shows LVA at
100% while the actual OS sink remains capped.

This module keeps the OS output volume aligned with LVA's persisted
preferences.volume_level (0.0–1.0).

We try backends in this order:
  1) PipeWire: wpctl
  2) PulseAudio (incl. pipewire-pulse): pactl
  3) ALSA: amixer
"""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import subprocess
from typing import Optional

_LOGGER = logging.getLogger(__name__)


def _clamp01(v: float) -> float:
    try:
        v = float(v)
    except Exception:
        return 1.0
    if math.isnan(v) or math.isinf(v):
        return 1.0
    return max(0.0, min(1.0, v))


def _pactl_sink_from_output_device(output_device: Optional[str]) -> str:
    """Best-effort mapping of mpv audio-device -> pactl sink name.

LVA commonly uses:
  - "pipewire/<pactl sink name>"
  - "pulse/<pactl sink name>"
  - "alsa_output...." (already a sink name)

Fallback is @DEFAULT_SINK@.
"""
    if not output_device:
        return "@DEFAULT_SINK@"

    dev = output_device.strip()

    for prefix in ("pipewire/", "pulse/"):
        if dev.startswith(prefix) and len(dev) > len(prefix):
            return dev[len(prefix) :]

    if dev.startswith("alsa_output."):
        return dev

    # Some users pass the raw sink name without a prefix.
    if "alsa_output." in dev:
        return dev

    return "@DEFAULT_SINK@"


def _run_cmd(cmd: list[str], timeout_s: float = 2.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "").strip()
        return ok, out
    except FileNotFoundError:
        return False, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, repr(e)


def set_output_volume(
    volume_0_1: float,
    output_device: Optional[str] = None,
    logger: logging.Logger = _LOGGER,
) -> bool:
    """Set OS output volume to match LVA volume (0.0–1.0).

Returns True if any backend succeeded.
"""
    vol = _clamp01(volume_0_1)

    # --- 1) PipeWire: wpctl -------------------------------------------------
    if shutil.which("wpctl"):
        # wpctl accepts @DEFAULT_AUDIO_SINK@ and a linear factor (e.g. 0.40)
        ok, out = _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol:.3f}"])
        if ok:
            logger.debug("Set PipeWire sink volume via wpctl: %.3f", vol)
            return True
        logger.debug("wpctl set-volume failed: %s", out)

    # --- 2) PulseAudio: pactl ----------------------------------------------
    if shutil.which("pactl"):
        sink = _pactl_sink_from_output_device(output_device)
        pct = int(round(vol * 100.0))
        ok, out = _run_cmd(["pactl", "set-sink-volume", sink, f"{pct}%"])
        if ok:
            logger.debug("Set Pulse sink volume via pactl: sink=%s pct=%d", sink, pct)
            return True
        logger.debug("pactl set-sink-volume failed: %s", out)

    # --- 3) ALSA: amixer ----------------------------------------------------
    if shutil.which("amixer"):
        pct = int(round(vol * 100.0))
        # Common mixer controls across SBC images.
        for control in ("Master", "PCM", "Speaker", "Headphone"):
            ok, out = _run_cmd(["amixer", "-q", "sset", control, f"{pct}%"])
            if ok:
                logger.debug("Set ALSA volume via amixer: control=%s pct=%d", control, pct)
                return True
            logger.debug("amixer sset %s failed: %s", control, out)

    return False


async def ensure_output_volume(
    volume: float,
    output_device: Optional[str] = None,
    attempts: int = 10,
    delay_seconds: float = 0.5,
    logger: logging.Logger = _LOGGER,
) -> bool:
    """Retry output volume sync during startup.

Useful at boot when PipeWire/Pulse/ALSA might not be fully ready yet.
"""
    for i in range(1, max(1, attempts) + 1):
        ok = await asyncio.to_thread(set_output_volume, volume, output_device, logger)
        if ok:
            return True
        if i < attempts:
            await asyncio.sleep(delay_seconds)
    return False
