#!/usr/bin/env python3
import argparse
import time

import numpy as np
import sounddevice as sd


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

    print("=== sounddevice.query_devices() (input subset) ===")
    devices = sd.query_devices()
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            flag = "*" if args.device in (dev["name"], str(idx)) else " "
            print(
                f"{flag} [{idx}] {dev['name']} "
                f"(max_input_channels={dev['max_input_channels']}, "
                f"default_samplerate={dev['default_samplerate']})"
            )

    print("\n=== Opening stream ===")
    sd.default.samplerate = args.samplerate
    sd.default.dtype = "int16"

    # Allow using either index or full name
    device = args.device
    try:
        device = int(args.device)
    except ValueError:
        pass

    # First just open/close the stream to see what PortAudio says
    with sd.InputStream(device=device, channels=0) as stream:
        print(f"Opened stream with device={device}")
        print(f"  samplerate: {stream.samplerate}")
        print(f"  channels:   {stream.channels}")
        print(f"  dtype:      {stream.dtype}")
        print(f"  blocksize:  {stream.blocksize}")

    # Now actually record a short block and inspect the shape
    print("\n=== Recording test block ===")
    duration = args.seconds
    num_frames = int(duration * args.samplerate)
    print(f"Recording {duration} seconds ({num_frames} frames)...")

    recorded = sd.rec(frames=num_frames, samplerate=args.samplerate,
                      channels=0, dtype="int16", device=device)
    sd.wait()

    arr = np.array(recorded)
    print(f"Recorded array shape: {arr.shape}, dtype={arr.dtype}")
    if arr.ndim == 1:
        print("-> Mono (1D) array returned")
    elif arr.ndim == 2:
        print(f"-> Multichannel: {arr.shape[1]} channels")
    else:
        print("-> Unexpected ndim:", arr.ndim)

    print(f"Sample min/max: {arr.min()} / {arr.max()}")


if __name__ == "__main__":
    main()
