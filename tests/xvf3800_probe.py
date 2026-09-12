#!/usr/bin/env python3
"""XVF3800 microphone capture probe (soundcard backend).

Lists input devices, opens the selected mic, records a short block and
prints shape / sample range so capture problems (wrong device, silent
channel, sample-rate mismatch) are visible before starting the satellite.

Uses the same `soundcard` library as the daemon itself.
"""
import argparse

import numpy as np
import soundcard as sc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--device",
        help="Exact input device name to test (as shown by --list-input-devices)",
        required=True,
    )
    parser.add_argument(
        "--samplerate",
        type=float,
        default=16000.0,
        help="Sample rate to test (default: 16000)",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=2.0,
        help="Duration to record for debug (default: 2s)",
    )
    args = parser.parse_args()

    mics = sc.all_microphones()
    print("=== soundcard.all_microphones() ===")
    selected = None
    for idx, mic in enumerate(mics):
        flag = "*" if args.device in (mic.name, str(idx)) else " "
        print(
            f"{flag} [{idx}] {mic.name} "
            f"(channels={mic.channels}, default={mic.isdefault})"
        )
        if flag == "*":
            selected = mic

    if selected is None:
        # Fallback: soundcard's fuzzy name match (substring, case-insensitive)
        try:
            selected = sc.get_microphone(args.device)
        except Exception as err:
            raise SystemExit(
                f"Device {args.device!r} not found by exact name or substring; "
                f"pick one from the list above ({err})"
            )

    print(f"\n=== Opening recorder: {selected.name} ===")
    num_frames = int(args.seconds * args.samplerate)
    print(f"Recording {args.seconds} seconds ({num_frames} frames @ {args.samplerate:g} Hz)...")

    with selected.recorder(samplerate=int(args.samplerate), channels=1) as rec:
        chunk = rec.record(numframes=num_frames)

    arr = np.asarray(chunk)
    print(f"Recorded array shape: {arr.shape}, dtype={arr.dtype}")
    if arr.ndim == 2:
        print(f"-> {arr.shape[1]} channel(s)")
    # soundcard yields float32 in [-1.0, 1.0]; report as int16 like the
    # previous sounddevice-based probe did
    pcm16 = np.clip(arr.flatten() * 32767.0, -32768, 32767).astype(np.int16)
    print(f"Sample min/max (int16): {pcm16.min()} / {pcm16.max()}")
    peak = np.max(np.abs(pcm16))
    if peak < 100:
        print("-> WARNING: capture is essentially silent (peak < 100). "
              "Check the device is the XVF3800 and its input gain/mute state.")


if __name__ == "__main__":
    main()
