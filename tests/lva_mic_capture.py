import argparse
import soundcard as sc
import numpy as np
import wave

DEVICE = "reSpeaker XVF3800 4-Mic Array Analog Stereo"
SECONDS = 5
SR = 16000
BLOCK = 1024

def main():
    parser = argparse.ArgumentParser(description="Record from soundcard mic to WAV.")
    parser.add_argument(
        "--channel",
        choices=["mix", "left", "right"],
        default="mix",
        help="mix=record 1ch (downmix/default). left/right=record 2ch and select.",
    )
    parser.add_argument("--seconds", type=int, default=SECONDS)
    parser.add_argument("--device", type=str, default=DEVICE)
    parser.add_argument("--out", type=str, default="/tmp/lva_soundcard.wav")
    args = parser.parse_args()

    if args.channel == "mix":
        in_ch = 1
        out_ch = 1
        out_path = args.out
    else:
        in_ch = 2
        out_ch = 1
        # If user didn't override --out, make filename reflect selection
        if args.out == "/tmp/lva_soundcard.wav":
            out_path = f"/tmp/lva_soundcard_{args.channel}.wav"
        else:
            out_path = args.out

    mic = sc.get_microphone(args.device, include_loopback=False)
    print("Using mic:", mic.name)
    print(f"Mode: {args.channel} (request channels={in_ch} -> write channels={out_ch})")

    frames = []
    with mic.recorder(samplerate=SR, channels=in_ch, blocksize=BLOCK) as r:
        for _ in range(int(SR * args.seconds / BLOCK)):
            buf = r.record(BLOCK)

            if in_ch == 1:
                # buf can be (BLOCK,) or (BLOCK, 1) depending on backend
                mono = buf.reshape(-1)
                sample = mono
            else:
                # buf should be (BLOCK, 2)
                if buf.ndim != 2 or buf.shape[1] < 2:
                    raise RuntimeError(f"Expected stereo buffer, got shape {buf.shape}")
                idx = 0 if args.channel == "left" else 1
                sample = buf[:, idx]

            rms = float(np.sqrt(np.mean(np.square(sample)))) if sample.size else 0.0
            print(f"rms={rms:.6f}")

            pcm16 = (np.clip(sample, -1.0, 1.0) * 32767.0).astype("<i2")
            frames.append(pcm16.tobytes())

    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(out_ch)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(b"".join(frames))

    print("Wrote:", out_path)

if __name__ == "__main__":
    main()

