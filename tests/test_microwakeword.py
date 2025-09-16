"""Tests for microWakeWord."""

import wave
from pathlib import Path

from linux_voice_assistant.microwakeword import MicroWakeWord, MicroWakeWordFeatures
from linux_voice_assistant.util import is_arm

_TESTS_DIR = Path(__file__).parent
_REPO_DIR = _TESTS_DIR.parent
_MICRO_DIR = _REPO_DIR / "wakewords"

if is_arm():
    _LIB_DIR = _REPO_DIR / "lib" / "linux_arm64"
else:
    _LIB_DIR = _REPO_DIR / "lib" / "linux_amd64"


libtensorflowlite_c_path = _LIB_DIR / "libtensorflowlite_c.so"


def test_features() -> None:
    features = MicroWakeWordFeatures(
        libtensorflowlite_c_path=libtensorflowlite_c_path,
    )
    ww = MicroWakeWord.from_config(
        config_path=_MICRO_DIR / "okay_nabu.json",
        libtensorflowlite_c_path=libtensorflowlite_c_path,
    )

    detected = False
    with wave.open(str(_TESTS_DIR / "ok_nabu.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnchannels() == 1

        for micro_input in features.process_streaming(
            wav_file.readframes(wav_file.getnframes())
        ):
            if ww.process_streaming(micro_input):
                detected = True

    assert detected
